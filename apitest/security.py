"""Local UI boundary, private files and credential-safe diagnostics."""
from __future__ import annotations

import copy
import base64
import binascii
import ipaddress
import json
import os
import re
import secrets
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

MASK = "__APITEST_KEEP_SECRET__"
_known_secrets = set()
_sensitive = re.compile(r"api.?key|authorization|password|secret|access.?token|bearer", re.I)


def register_secrets(*values):
    for value in values:
        if isinstance(value, str) and len(value) >= 4 and not value.startswith("${") and value != MASK:
            _known_secrets.add(value)


def safe_url(value):
    try:
        u = urlsplit(value)
        if u.scheme not in ("http", "https", "socks5", "socks5h"):
            return value
        host = u.hostname or ""
        if ":" in host:
            host = "[" + host + "]"
        if u.port:
            host += ":" + str(u.port)
        # Diagnostics never need URL credentials, query strings or fragments.
        return urlunsplit((u.scheme, host, u.path, "", ""))
    except ValueError:
        return "[invalid URL]"


def redact(value):
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _sensitive.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    for secret in sorted(_known_secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = re.sub(r"https?://[^\s\"'<>]+", lambda m: safe_url(m.group()), value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", value)
    return value


def private_write(path, text):
    """Create/replace an owner-only regular file without following a destination symlink."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("Refusing to overwrite a symlink")
    fd, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate_endpoint(ep):
    from .providers import PROVIDERS
    if not isinstance(ep, dict) or ep.get("provider", "openai") not in PROVIDERS + ("auto", ""):
        raise ValueError("Unsupported provider")
    url = ep.get("base_url") or ep.get("url") or ""
    if not url and ep.get("provider") == "gemini":
        return
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("Base URL must be HTTP(S), without credentials, query or fragment")
    if u.scheme == "http" and u.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Remote API endpoints require HTTPS")


def public_endpoints(endpoints):
    out = copy.deepcopy(endpoints)
    for ep in out.values():
        for k in ("api_key", "key"):
            if ep.get(k):
                ep[k] = MASK
        for k in ep.get("headers", {}):
            ep["headers"][k] = MASK
        # Unknown body settings can contain credentials; redact sensitive field names.
        if "extra_body" in ep:
            ep["extra_body"] = redact(ep["extra_body"])
    return out


def resolve_browser_endpoints(incoming, stored):
    """Only saved credentials may resolve ENV references; keep them bound to a destination."""
    from .config import expand_env
    out = {}
    for name, ep in incoming.items():
        ep = copy.deepcopy(ep)
        validate_endpoint(ep)
        old = stored.get(name, {})
        same = (ep.get("base_url", ep.get("url", "")), ep.get("provider", "")) == (old.get("base_url", old.get("url", "")), old.get("provider", ""))
        for k in ("api_key", "key"):
            if ep.get(k) == MASK:
                if not same:
                    raise ValueError("Destination changed: enter a new API key")
                ep[k] = expand_env(old.get(k, ""))
            elif "${" in str(ep.get(k, "")):
                if not same or ep[k] != old.get(k):
                    raise ValueError("Environment references must be configured locally")
                ep[k] = expand_env(old[k])
        for k, v in ep.get("headers", {}).items():
            if v == MASK:
                if not same:
                    raise ValueError("Destination changed: enter new headers")
                ep["headers"][k] = expand_env(old.get("headers", {}).get(k, ""))
        if "${" in json.dumps(ep):
            raise ValueError("Browser input cannot expand server environment variables")
        out[name] = ep
    return out


class LocalBoundary:
    """Same-origin local UI; remote listening additionally requires HTTP authentication."""
    def __init__(self, app):
        self.app = app
        self.token = secrets.token_urlsafe(32)
        self.remote_password = None

    def enable_remote(self, password):
        if not isinstance(password, str) or len(password) < 16:
            raise ValueError("远程访问密码至少16个字符")
        register_secrets(password)
        self.remote_password = password

    def authorized(self, header):
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:], validate=True)
        except (ValueError, binascii.Error):
            return False
        user, separator, password = decoded.partition(b":")
        return bool(separator) and secrets.compare_digest(user, b"apitest") and secrets.compare_digest(password, self.remote_password.encode("utf-8"))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        request = Request(scope, receive)
        if self.remote_password and not self.authorized(request.headers.get("authorization", "")):
            return await JSONResponse({"error": "Authentication required"}, status_code=401, headers={"WWW-Authenticate": 'Basic realm="apitest", charset="UTF-8"', "Cache-Control": "no-store"})(scope, receive, send)
        host = request.url.hostname
        origin = request.headers.get("origin")
        expected = str(request.base_url).rstrip("/")
        denied = not host
        if not self.remote_password:
            denied |= host not in ("localhost", "127.0.0.1", "::1")
        peer = (scope.get("client") or ("127.0.0.1", 0))[0]
        try:
            if not self.remote_password:
                denied |= not ipaddress.ip_address(peer).is_loopback
        except ValueError:
            denied = True
        # A deliberate top-level navigation may bootstrap the local UI. Its APIs
        # and subresources still reject cross-site requests.
        navigation = request.method == "GET" and request.url.path == "/" and request.headers.get("sec-fetch-mode") == "navigate"
        denied |= request.headers.get("sec-fetch-site") == "cross-site" and not navigation
        denied |= bool(origin and origin != expected)
        mutation = request.method not in ("GET", "HEAD", "OPTIONS")
        try:
            content_length = int(request.headers.get("content-length", "0") or 0)
        except ValueError:
            return await JSONResponse({"error": "Invalid content length"}, status_code=400)(scope, receive, send)
        if content_length > 1024 * 1024:
            return await JSONResponse({"error": "Request too large"}, status_code=413)(scope, receive, send)
        if request.url.path.startswith("/api/"):
            denied |= not secrets.compare_digest(request.cookies.get("apitest_session", "").encode(), self.token.encode())
            if mutation:
                denied |= request.headers.get("content-type", "").split(";")[0].strip() != "application/json"
                denied |= not secrets.compare_digest(request.headers.get("x-apitest-token", "").encode(), self.token.encode())
        if denied:
            return await JSONResponse({"error": "Local same-origin session required"}, status_code=403)(scope, receive, send)
        scope["apitest_token"] = self.token
        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend([(b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"no-referrer"), (b"content-security-policy", ("default-src 'self'; script-src 'self' 'nonce-" + self.token + "'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'").encode())])
                message["headers"] = headers
            await send(message)
        await self.app(scope, receive, secure_send)

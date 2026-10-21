"""连通性诊断：DNS / TCP / TLS / HTTP 各阶段耗时。"""
from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

from .providers import Target, auth_headers, models_url


@dataclass
class PingResult:
    host: str
    ip: str = ""
    dns: float = 0.0
    tcp: float = 0.0
    tls: float = 0.0
    http: float = 0.0
    status: int = 0
    error: str = ""
    server: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def ping_once(target: Target, timeout: float = 10.0, proxy: Optional[str] = None, verify: bool = True) -> PingResult:
    url = models_url(target)
    u = urlparse(url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    r = PingResult(host=host)
    if proxy:
        # 走代理时无法拆分阶段，只测 HTTP
        return _http_only(target, url, r, timeout, proxy, verify)
    try:
        t0 = time.perf_counter()
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        r.dns = time.perf_counter() - t0
        family, _, _, _, addr = infos[0]
        r.ip = addr[0]

        t1 = time.perf_counter()
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(addr)
        r.tcp = time.perf_counter() - t1

        if u.scheme == "https":
            ctx = ssl.create_default_context()
            if not verify:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            t2 = time.perf_counter()
            ssock = ctx.wrap_socket(sock, server_hostname=host)
            r.tls = time.perf_counter() - t2
            ssock.close()
        else:
            sock.close()
    except Exception as e:  # noqa: BLE001
        r.error = f"{e.__class__.__name__}: {e}"
        return r
    return _http_only(target, url, r, timeout, None, verify)


def _http_only(target: Target, url: str, r: PingResult, timeout: float, proxy: Optional[str], verify: bool) -> PingResult:
    kwargs = dict(timeout=timeout, verify=verify, follow_redirects=False, trust_env=False)
    if proxy:
        kwargs["proxy"] = proxy
    try:
        with httpx.Client(**kwargs) as c:
            t3 = time.perf_counter()
            resp = c.get(url, headers=auth_headers(target))
            r.http = time.perf_counter() - t3
            r.status = resp.status_code
            r.server = resp.headers.get("server", "") or resp.headers.get("cf-ray", "")
            if resp.status_code >= 500:
                r.error = f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        r.error = f"{e.__class__.__name__}: {e}"
    return r

"""三类 API 风格的请求构造与流式解析：OpenAI 兼容 / Anthropic Messages / Google Gemini。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional, Tuple

import httpx
from .security import register_secrets, validate_endpoint
from .pricing import validate_price_multiplier

PROVIDERS = ("openai", "responses", "anthropic", "gemini")
ANTHROPIC_VERSION = "2023-06-01"
GEMINI_DEFAULT_URL = "https://generativelanguage.googleapis.com"


@dataclass
class Target:
    """一个待测目标 = 端点 + 模型。"""

    name: str
    provider: str
    base_url: str
    api_key: str
    model: str
    headers: Dict[str, str] = field(default_factory=dict)
    extra_body: Dict[str, Any] = field(default_factory=dict)
    price_in: Optional[float] = None  # USD / 1M input tokens
    price_out: Optional[float] = None  # USD / 1M output tokens
    currency: str = "USD"
    effort: Optional[str] = None
    price_cache_read: Optional[float] = None
    price_cache_write: Optional[float] = None
    price_multiplier: float = 1.0

    def __post_init__(self):
        self.price_multiplier = validate_price_multiplier(self.price_multiplier)
        validate_endpoint({"provider": self.provider, "base_url": self.base_url})
        register_secrets(self.api_key, *(self.headers or {}).values())

    @property
    def label(self) -> str:
        return f"{self.name}/{self.model}"


def detect_provider(base_url: str, api_key: str = "") -> str:
    u = (base_url or "").lower()
    if "anthropic" in u or api_key.startswith("sk-ant-"):
        return "anthropic"
    if "googleapis.com" in u or "gemini" in u or api_key.startswith("AIza"):
        return "gemini"
    return "openai"


def _strip(url: str) -> str:
    return url.rstrip("/")


def chat_url(t: Target, stream: bool = True) -> str:
    base = _strip(t.base_url) or (GEMINI_DEFAULT_URL if t.provider == "gemini" else "")
    if t.provider == "openai":
        if base.endswith("/chat/completions"):
            return base
        if re.search(r"/v\d+(beta)?$", base):
            return base + "/chat/completions"
        return base + "/v1/chat/completions"
    if t.provider == "responses":
        if base.endswith("/responses"):
            return base
        return base + ("/responses" if re.search(r"/v\d+$", base) else "/v1/responses")
    if t.provider == "anthropic":
        if base.endswith("/messages"):
            return base
        if re.search(r"/v\d+$", base):
            return base + "/messages"
        return base + "/v1/messages"
    if t.provider == "gemini":
        if ":generateContent" in base or ":streamGenerateContent" in base:
            return base
        if not re.search(r"/v\d+(beta)?$", base):
            base = base + "/v1beta"
        method = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{base}/models/{t.model}:{method}"
    raise ValueError(f"未知 provider: {t.provider}")


def models_url(t: Target) -> str:
    base = _strip(t.base_url) or (GEMINI_DEFAULT_URL if t.provider == "gemini" else "")
    for suffix in ("/chat/completions", "/messages", "/responses"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    if t.provider == "gemini":
        if not re.search(r"/v\d+(beta)?$", base):
            base += "/v1beta"
        return base + "/models"
    if re.search(r"/v\d+(beta)?$", base):
        return base + "/models"
    return base + "/v1/models"


def auth_headers(t: Target) -> Dict[str, str]:
    h: Dict[str, str] = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if t.provider in ("openai", "responses"):
        if t.api_key:
            h["Authorization"] = f"Bearer {t.api_key}"
    elif t.provider == "anthropic":
        if t.api_key:
            h["x-api-key"] = t.api_key
        h["anthropic-version"] = ANTHROPIC_VERSION
    elif t.provider == "gemini":
        if t.api_key:
            h["x-goog-api-key"] = t.api_key
    h.update(t.headers or {})
    return h


def _openai_uses_max_completion_tokens(model: str) -> bool:
    m = model.lower()
    return bool(re.match(r"^(o\d|gpt-[56]|gpt-4\.1-)", m)) or "reasoning" in m


def build_body(
    t: Target,
    prompt: str,
    system: Optional[str],
    max_tokens: int,
    temperature: Optional[float],
    stream: bool,
) -> Dict[str, Any]:
    body: Dict[str, Any]
    if t.provider == "openai":
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {"model": t.model, "messages": messages, "stream": stream}
        if _openai_uses_max_completion_tokens(t.model):
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        if temperature is not None:
            body["temperature"] = temperature
        if stream:
            body["stream_options"] = {"include_usage": True}
    elif t.provider == "anthropic":
        body = {
            "model": t.model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if system:
            body["system"] = system
        if temperature is not None:
            body["temperature"] = temperature
    elif t.provider == "responses":
        body = {"model": t.model, "input": prompt, "stream": stream, "max_output_tokens": max_tokens, "store": False}
        if system:
            body["instructions"] = system
        if temperature is not None:
            body["temperature"] = temperature
    elif t.provider == "gemini":
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature
    else:
        raise ValueError(f"未知 provider: {t.provider}")
    if t.extra_body:
        _deep_merge(body, t.extra_body)
    if t.effort:
        if t.provider == "openai":
            body["reasoning_effort"] = t.effort
        elif t.provider == "responses":
            body.setdefault("reasoning", {})["effort"] = t.effort
        elif t.provider == "anthropic":
            body.setdefault("output_config", {})["effort"] = t.effort
            body.setdefault("thinking", {"type": "adaptive"})
        else:
            body.setdefault("generationConfig", {}).setdefault("thinkingConfig", {})["thinkingLevel"] = t.effort
    # Body overrides must never bypass the selected output ceiling or create N completions.
    if body.get("n", 1) != 1:
        raise ValueError("Only one completion per request is supported")
    if t.provider == "gemini":
        if body["generationConfig"].get("candidateCount", 1) != 1:
            raise ValueError("Only one Gemini candidate is supported")
        body["generationConfig"]["maxOutputTokens"] = min(max_tokens, int(body["generationConfig"].get("maxOutputTokens", max_tokens)))
    else:
        keys = ("max_tokens", "max_completion_tokens", "max_output_tokens")
        for key in keys:
            if key in body:
                body[key] = min(max_tokens, int(body[key]))
    if t.provider == "responses" and body.get("tools"):
        raise ValueError("Built-in tools have separate charges; benchmark text output only")
    return body


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


# ---------------------------------------------------------------------------
# 流式解析
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    kind: str  # text | reasoning | usage | stop | error
    text: str = ""
    usage: Optional[Dict[str, int]] = None  # input / output / reasoning
    error: str = ""


async def iter_sse(response: httpx.Response) -> AsyncIterator[Tuple[str, str]]:
    """把 SSE 响应解析为 (event, data) 对。兼容无 event 行、多行 data、注释行。"""
    event = ""
    data_lines = []
    async for raw in response.aiter_lines():
        line = raw.rstrip("\r")
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = "", []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)


def _usage_openai(u: Dict[str, Any]) -> Dict[str, int]:
    details = u.get("completion_tokens_details") or {}
    return {
        "input": int(u.get("prompt_tokens") or 0),
        "output": int(u.get("completion_tokens") or 0),
        "reasoning": int(details.get("reasoning_tokens") or 0),
        "cache_read": int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0),
        "cache_write": int((u.get("prompt_tokens_details") or {}).get("cache_write_tokens") or 0),
    }


def _usage_responses(u):
    details = u.get("input_tokens_details") or {}
    return {"input": int(u.get("input_tokens") or 0), "output": int(u.get("output_tokens") or 0),
            "reasoning": int((u.get("output_tokens_details") or {}).get("reasoning_tokens") or 0),
            "cache_read": int(details.get("cached_tokens") or 0),
            "cache_write": int(details.get("cache_write_tokens") or 0)}


def _usage_anthropic(u):
    # Anthropic input_tokens excludes cache reads/writes; our input is the total.
    usage = {}
    if "input_tokens" in u:
        usage["input"] = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    for dest, source in (("output", "output_tokens"), ("cache_read", "cache_read_input_tokens"), ("cache_write", "cache_creation_input_tokens")):
        if source in u:
            usage[dest] = int(u[source] or 0)
    return usage


async def parse_stream(provider: str, response: httpx.Response) -> AsyncIterator[StreamEvent]:
    async for event, data in iter_sse(response):
        if data.strip() == "[DONE]":
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue

        if provider == "responses":
            typ = obj.get("type") or event
            if typ == "response.output_text.delta" and obj.get("delta"):
                yield StreamEvent("text", text=obj["delta"])
            elif typ in ("response.reasoning_text.delta", "response.reasoning_summary_text.delta") and obj.get("delta"):
                yield StreamEvent("reasoning", text=obj["delta"])
            elif typ in ("response.completed", "response.incomplete"):
                res = obj.get("response") or {}
                u = res.get("usage") or {}
                yield StreamEvent("usage", usage=_usage_responses(u))
                reason = (res.get("incomplete_details") or {}).get("reason") if typ == "response.incomplete" else "stop"
                yield StreamEvent("stop", text=reason or "incomplete")
            elif typ in ("error", "response.failed"):
                yield StreamEvent("error", error=str(obj.get("error") or (obj.get("response") or {}).get("error") or "Response failed"))
        elif provider == "openai":
            if "error" in obj and obj["error"]:
                err = obj["error"]
                yield StreamEvent("error", error=err.get("message", str(err)) if isinstance(err, dict) else str(err))
                return
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or {}
                r = delta.get("reasoning_content") or delta.get("reasoning")
                if r:
                    yield StreamEvent("reasoning", text=r)
                c = delta.get("content")
                if c:
                    yield StreamEvent("text", text=c)
                if choice.get("finish_reason"):
                    yield StreamEvent("stop", text=str(choice["finish_reason"]))
            if obj.get("usage"):
                yield StreamEvent("usage", usage=_usage_openai(obj["usage"]))

        elif provider == "anthropic":
            typ = obj.get("type") or event
            if typ == "message_start":
                u = (obj.get("message") or {}).get("usage") or {}
                yield StreamEvent("usage", usage=_usage_anthropic(u))
            elif typ == "content_block_delta":
                d = obj.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    yield StreamEvent("text", text=d["text"])
                elif d.get("type") == "thinking_delta" and d.get("thinking"):
                    yield StreamEvent("reasoning", text=d["thinking"])
            elif typ == "message_delta":
                sr = (obj.get("delta") or {}).get("stop_reason")
                if sr:
                    yield StreamEvent("stop", text=str(sr))
                u = obj.get("usage") or {}
                if u:
                    yield StreamEvent(
                        "usage",
                        usage=_usage_anthropic(u),
                    )
            elif typ == "error":
                err = obj.get("error") or {}
                yield StreamEvent("error", error=f"{err.get('type', 'error')}: {err.get('message', '')}")
                return

        elif provider == "gemini":
            if "error" in obj:
                err = obj["error"]
                yield StreamEvent("error", error=err.get("message", str(err)) if isinstance(err, dict) else str(err))
                return
            for cand in obj.get("candidates") or []:
                for part in ((cand.get("content") or {}).get("parts") or []):
                    txt = part.get("text")
                    if not txt:
                        continue
                    if part.get("thought"):
                        yield StreamEvent("reasoning", text=txt)
                    else:
                        yield StreamEvent("text", text=txt)
                if cand.get("finishReason"):
                    yield StreamEvent("stop", text=str(cand["finishReason"]))
            um = obj.get("usageMetadata")
            if um:
                yield StreamEvent(
                    "usage",
                    usage={
                        "input": int(um.get("promptTokenCount") or 0),
                        "output": int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
                        "reasoning": int(um.get("thoughtsTokenCount") or 0),
                        "cache_read": int(um.get("cachedContentTokenCount") or 0),
                    },
                )


def parse_nonstream(provider: str, obj: Dict[str, Any]) -> Tuple[str, str, Dict[str, int], str]:
    """非流式响应 -> (text, reasoning, usage, stop_reason)。"""
    text, reasoning, usage, stop = "", "", {"input": 0, "output": 0, "reasoning": 0}, ""
    if provider == "responses":
        if obj.get("error") or obj.get("status") == "failed":
            raise RuntimeError(str(obj.get("error") or "Response failed"))
        for item in obj.get("output") or []:
            for part in item.get("content") or []:
                if part.get("type") == "output_text":
                    text += part.get("text") or ""
        u = obj.get("usage") or {}
        usage = _usage_responses(u)
        stop = (obj.get("incomplete_details") or {}).get("reason") or "stop"
    elif provider == "openai":
        if obj.get("error"):
            err = obj["error"]
            raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        for choice in obj.get("choices") or []:
            msg = choice.get("message") or {}
            text += msg.get("content") or ""
            reasoning += msg.get("reasoning_content") or msg.get("reasoning") or ""
            stop = str(choice.get("finish_reason") or stop)
        if obj.get("usage"):
            usage = _usage_openai(obj["usage"])
    elif provider == "anthropic":
        if obj.get("type") == "error":
            err = obj.get("error") or {}
            raise RuntimeError(f"{err.get('type', 'error')}: {err.get('message', '')}")
        for block in obj.get("content") or []:
            if block.get("type") == "text":
                text += block.get("text") or ""
            elif block.get("type") == "thinking":
                reasoning += block.get("thinking") or ""
        u = obj.get("usage") or {}
        usage = _usage_anthropic(u)
        stop = str(obj.get("stop_reason") or "")
    elif provider == "gemini":
        if obj.get("error"):
            err = obj["error"]
            raise RuntimeError(err.get("message", str(err)) if isinstance(err, dict) else str(err))
        for cand in obj.get("candidates") or []:
            for part in ((cand.get("content") or {}).get("parts") or []):
                if part.get("thought"):
                    reasoning += part.get("text") or ""
                else:
                    text += part.get("text") or ""
            stop = str(cand.get("finishReason") or stop)
        um = obj.get("usageMetadata") or {}
        usage = {
            "input": int(um.get("promptTokenCount") or 0),
            "output": int(um.get("candidatesTokenCount") or 0) + int(um.get("thoughtsTokenCount") or 0),
            "reasoning": int(um.get("thoughtsTokenCount") or 0),
            "cache_read": int(um.get("cachedContentTokenCount") or 0),
        }
    return text, reasoning, usage, stop


def parse_models(provider: str, obj: Any) -> list:
    """模型列表响应 -> [{"id":..., "name":..., "created":...}]"""
    out = []
    if provider == "gemini":
        for m in (obj.get("models") if isinstance(obj, dict) else []) or []:
            out.append({"id": (m.get("name") or "").replace("models/", ""), "name": m.get("displayName", ""), "created": ""})
        return out
    items = obj.get("data") if isinstance(obj, dict) else obj
    for m in items or []:
        if isinstance(m, str):
            out.append({"id": m, "name": "", "created": ""})
            continue
        out.append(
            {
                "id": m.get("id", ""),
                "name": m.get("display_name") or m.get("owned_by") or "",
                "created": m.get("created_at") or m.get("created") or "",
            }
        )
    return out


_CJK = re.compile(r"[一-鿿㐀-䶿぀-ヿ가-힯]")


def estimate_tokens(text: str) -> int:
    """无 usage 时的粗略估算：中日韩字符约 1 字 ≈ 0.7 token，其余按 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return max(1, int(round(cjk * 0.7 + other / 4.0)))


TRUNCATED_REASONS = {"length", "max_tokens", "max_output_tokens"}


def is_truncated(stop_reason: str) -> bool:
    """结束原因是否表示达到 max_tokens 被截断。"""
    return (stop_reason or "").lower() in TRUNCATED_REASONS

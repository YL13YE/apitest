"""异步执行器：单次请求计时、并发轮次、持续压测。"""
from __future__ import annotations

import asyncio
import time
import secrets
from dataclasses import replace
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

import hashlib

from .providers import (
    Target,
    auth_headers,
    build_body,
    chat_url,
    estimate_tokens,
    is_truncated,
    parse_nonstream,
    parse_stream,
)
from .security import redact
from .budget import RunControl, validate_options
from .pricing import usage_cost
from .scheduling import is_active, next_active
from .measurements import normalize_result, throughput_issue, output_limit_exceeded


@dataclass
class RunOptions:
    prompt: str
    system: Optional[str] = None
    max_tokens: int = 512
    temperature: Optional[float] = None
    stream: bool = True
    rounds: int = 3
    concurrency: int = 1
    warmup: int = 0
    timeout: float = 120.0
    interval: float = 0.0
    duration: Optional[float] = None  # 秒；设置后忽略 rounds，持续跑到超时
    retries: int = 0
    parallel_targets: bool = False  # 兼容旧参数，等价于 order="parallel"
    order: str = "interleaved"  # interleaved 交替轮询 | sequential 逐个目标 | parallel 全部并行
    verify_ssl: bool = True
    proxy: Optional[str] = None
    http2: bool = False
    keep_text: bool = True
    sample_interval: float = 0.0  # 每轮所有目标完成后，按起始时刻定点采样
    budget: Optional[float] = None
    hourly_budget: Optional[float] = None
    max_requests: Optional[int] = None  # 所有目标、预热、重试共用
    cache_mode: str = "cold"
    monitor_hours: Optional[List[int]] = None  # Daily allowed hours, Asia/Shanghai.
    usage_bounds: Dict[str, Dict[str, int]] = field(default_factory=dict, repr=False)
    control: Optional[RunControl] = field(default=None, repr=False, compare=False)
    # 流式进度回调 (label, index, text_so_far, elapsed)，Web 界面用于实时输出
    progress_cb: Optional[Callable[[str, int, str, float], None]] = field(default=None, repr=False, compare=False)


@dataclass
class RequestResult:
    target: str
    model: str
    provider: str
    index: int
    currency: str = "USD"
    effort: Optional[str] = None
    ok: bool = False
    error: str = ""
    error_kind: str = ""  # http / timeout / connect / empty / stream / other
    status_code: int = 0
    ttfb: float = 0.0  # 收到响应头
    ttft: float = 0.0  # 首个 token（文本或思考）
    ttft_text: float = 0.0  # 首个可见文本 token
    total: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tokens_estimated: bool = False
    chunks: int = 0
    text_chars: int = 0
    reasoning_chars: int = 0
    text: str = ""
    started_at: float = 0.0
    stream: bool = True
    warmup: bool = False
    attempt: int = 1
    cost: Optional[float] = None
    stop_reason: str = ""  # 结束原因：stop / end_turn / length / max_tokens ...
    text_hash: str = ""  # 输出文本 sha1 前 10 位，用于比较两边内容是否一致
    # 每个 chunk 的到达时刻（相对 t0），用于抖动分析
    chunk_times: List[float] = field(default_factory=list)
    text_chunk_times: List[float] = field(default_factory=list)
    budget_charge: float = 0.0
    cost_uncertain: bool = False
    cost_unknown_reason: str = ""
    visible_tokens_estimated: bool = False
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requested_max_tokens: Optional[int] = None
    requested_limit_field: Optional[str] = None
    price_multiplier: float = 1.0

    @property
    def truncated(self) -> bool:
        return is_truncated(self.stop_reason)

    @property
    def gen_time(self) -> float:
        """生成阶段耗时：流式为首个可见文本 token 之后（不受思考片段是否透传影响）；
        非流式或整段一次性返回时为整体耗时。"""
        if len(self.text_chunk_times) > 1:
            return max(self.text_chunk_times[-1] - self.text_chunk_times[0], 1e-6)
        return max(self.total, 1e-6)

    @property
    def tps(self) -> float:
        if not self.tps_eligible:
            return 0.0
        return max(0, self.output_tokens - self.reasoning_tokens) / self.gen_time if not self.visible_tokens_estimated else 0.0

    @property
    def tps_eligible(self) -> bool:
        return throughput_issue({"ok": self.ok, "stream": self.stream, "text_chunks": len(self.text_chunk_times),
            "gen_time": self.gen_time, "truncated": self.truncated, "tokens_estimated": self.tokens_estimated,
            "visible_tokens_estimated": self.visible_tokens_estimated, "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens, "output_limit_exceeded": output_limit_exceeded({
                "output_tokens": self.output_tokens, "tokens_estimated": self.tokens_estimated,
                "requested_max_tokens": self.requested_max_tokens})}) is None

    @property
    def max_gap(self) -> float:
        """流式 chunk 之间的最大停顿（秒）。"""
        if len(self.text_chunk_times) < 2:
            return 0.0
        return max(b - a for a, b in zip(self.text_chunk_times, self.text_chunk_times[1:]))

    def to_dict(self) -> Dict:
        d = asdict(self)
        d.pop("chunk_times", None)
        d.pop("text_chunk_times", None)
        d["tps"] = round(self.tps, 2)
        d["gen_time"] = self.gen_time
        d["max_gap"] = round(self.max_gap, 4)
        d["truncated"] = self.truncated
        gaps = [b - a for a, b in zip(self.text_chunk_times, self.text_chunk_times[1:])]
        d["stall_count"] = sum(g > 1 for g in gaps)
        d["stall_seconds"] = round(sum(g for g in gaps if g > 1), 4)
        d["chars_per_second"] = round(self.text_chars / self.gen_time, 2) if len(self.text_chunk_times) > 1 else None
        d["single_chunk"] = len(self.text_chunk_times) == 1
        d["text_chunks"] = len(self.text_chunk_times)
        return redact(normalize_result(d))


ResultCallback = Callable[[RequestResult], None]


def _classify_exc(e: BaseException) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "timeout"
    if isinstance(e, (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError)):
        return "connect"
    return "other"


async def do_request(
    client: httpx.AsyncClient,
    target: Target,
    opts: RunOptions,
    index: int,
    warmup: bool = False,
) -> RequestResult:
    r = RequestResult(target=target.name, model=target.model, provider=target.provider, index=index, currency=target.currency, price_multiplier=target.price_multiplier, effort=target.effort or "default", stream=opts.stream, warmup=warmup)
    url = chat_url(target, opts.stream)
    headers = auth_headers(target)
    body = build_body(target, opts.prompt, opts.system, opts.max_tokens, opts.temperature, opts.stream)
    if target.provider == "gemini":
        r.requested_limit_field = "generationConfig.maxOutputTokens"
        r.requested_max_tokens = body["generationConfig"]["maxOutputTokens"]
    else:
        fields = [key for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in body]
        r.requested_limit_field = ", ".join(fields)
        r.requested_max_tokens = min(body[key] for key in fields)
    r.started_at = time.time()
    t0 = time.perf_counter()
    text_parts: List[str] = []
    reasoning_chars = 0
    usage: Dict[str, int] = {}
    last_emit = 0.0

    try:
        if opts.stream:
            async with client.stream("POST", url, headers=headers, json=body, timeout=opts.timeout) as resp:
                r.ttfb = time.perf_counter() - t0
                r.status_code = resp.status_code
                if resp.status_code >= 300:
                    raw = await resp.aread()
                    r.error = f"HTTP {resp.status_code}: {raw.decode('utf-8', 'replace')[:300].strip()}"
                    r.error_kind = "http"
                    r.total = time.perf_counter() - t0
                    return r
                async for ev in parse_stream(target.provider, resp):
                    now = time.perf_counter() - t0
                    if ev.kind == "error":
                        r.error, r.error_kind = ev.error, "stream"
                        break
                    if ev.kind in ("text", "reasoning"):
                        if r.ttft == 0.0:
                            r.ttft = now
                        r.chunks += 1
                        r.chunk_times.append(now)
                        if ev.kind == "text":
                            r.text_chunk_times.append(now)
                            if r.ttft_text == 0.0:
                                r.ttft_text = now
                            text_parts.append(ev.text)
                        else:
                            reasoning_chars += len(ev.text)
                        if opts.progress_cb and now - last_emit >= 0.15:
                            last_emit = now
                            opts.progress_cb(target.label, index, "".join(text_parts), now)
                    elif ev.kind == "usage" and ev.usage:
                        for k, v in ev.usage.items():
                            if v or k.startswith("cache_"):
                                usage[k] = v
                    elif ev.kind == "stop" and ev.text:
                        r.stop_reason = ev.text
        else:
            resp = await client.post(url, headers=headers, json=body, timeout=opts.timeout)
            r.ttfb = time.perf_counter() - t0
            r.status_code = resp.status_code
            if resp.status_code >= 300:
                r.error = f"HTTP {resp.status_code}: {resp.text[:300].strip()}"
                r.error_kind = "http"
                r.total = time.perf_counter() - t0
                return r
            text, reasoning, usage, stop = parse_nonstream(target.provider, resp.json())
            r.stop_reason = stop
            text_parts.append(text)
            reasoning_chars = len(reasoning)
            r.chunks = 1
    except Exception as e:  # noqa: BLE001
        r.total = time.perf_counter() - t0
        r.error_kind = _classify_exc(e)
        msg = str(e) or e.__class__.__name__
        r.error = {"timeout": f"超时 (>{opts.timeout:g}s)", "connect": f"连接失败: {msg}"}.get(r.error_kind, f"{e.__class__.__name__}: {msg}")
        return r

    r.total = time.perf_counter() - t0
    text = "".join(text_parts)
    r.text_chars = len(text)
    r.text_hash = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10] if text else ""
    r.reasoning_chars = reasoning_chars
    if opts.keep_text:
        r.text = text
    r.input_tokens = usage.get("input", 0)
    r.output_tokens = usage.get("output", 0)
    r.reasoning_tokens = usage.get("reasoning", 0)
    r.cache_read_tokens = usage.get("cache_read", 0)
    r.cache_write_tokens = usage.get("cache_write", 0)
    r.visible_tokens_estimated = bool(reasoning_chars and not r.reasoning_tokens)
    if r.output_tokens <= 0 and (text or reasoning_chars):
        r.output_tokens = estimate_tokens(text) + int(reasoning_chars * 0.25)
        r.tokens_estimated = True
    if r.input_tokens <= 0:
        r.input_tokens = estimate_tokens(opts.prompt) + estimate_tokens(opts.system or "")
        r.tokens_estimated = True
    if r.error:
        return r
    if not text:
        r.error, r.error_kind = "无正文响应（可能仅思考或预算耗尽）", "empty"
        return r
    r.ok = True
    if target.price_in is not None and target.price_out is not None:
        r.cost = usage_cost(target, r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens)
    return r


def make_client(opts: RunOptions) -> httpx.AsyncClient:
    kwargs = dict(timeout=opts.timeout, verify=opts.verify_ssl, http2=opts.http2,
                  follow_redirects=False, trust_env=False,
                  limits=httpx.Limits(max_connections=max(2, opts.concurrency), max_keepalive_connections=max(2, opts.concurrency)))
    if opts.proxy:
        kwargs["proxy"] = opts.proxy
    return httpx.AsyncClient(**kwargs)


async def run_all(targets, opts, on_result=None, on_target_start=None):
    """One shared budget and deadline, including warmups and every retry attempt."""
    validate_options(opts)
    if not targets:
        raise ValueError("No targets")
    guard = opts.control = RunControl(targets, opts)
    results = []
    start = time.monotonic()
    deadline = start + opts.duration if opts.duration else None
    sem = asyncio.Semaphore(opts.concurrency)
    counters = {t.label: 0 for t in targets}

    def expired():
        return deadline is not None and time.monotonic() >= deadline

    async def pause(seconds):
        if deadline is not None:
            seconds = min(seconds, max(0, deadline - time.monotonic()))
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def wait_for_active_window():
        while not expired():
            now = time.time()
            if is_active(now, opts.monitor_hours):
                guard.phase, guard.next_sample_at = "sampling", None
                return True
            scheduled = next_active(now, opts.monitor_hours)
            guard.phase, guard.next_sample_at = "waiting", scheduled
            await pause(min(scheduled - now, 30))
        return False

    async with make_client(opts) as client:
        async def request(target, index, warmup=False):
            for attempt in range(1, opts.retries + 2):
                async with sem:
                    if expired() or not is_active(time.time(), opts.monitor_hours):
                        return
                    ticket = await guard.reserve(target)
                    if ticket is None:
                        return
                    request_opts = opts
                    if opts.cache_mode == "cold":
                        prefix = "[benchmark nonce " + secrets.token_hex(12) + "]\n"
                        request_opts = replace(opts, system=prefix + (opts.system or ""))
                    res = RequestResult(target=target.name, model=target.model, provider=target.provider, index=index, currency=target.currency, price_multiplier=target.price_multiplier, effort=target.effort or "default", stream=opts.stream, warmup=warmup, started_at=time.time())
                    began = time.monotonic()
                    timeout = opts.timeout if deadline is None else min(opts.timeout, max(.001, deadline - time.monotonic()))
                    try:
                        res = await asyncio.wait_for(do_request(client, target, request_opts, index, warmup), timeout)
                    except asyncio.TimeoutError:
                        res.error, res.error_kind = "请求总耗时超时", "timeout"
                        res.total = time.monotonic() - began
                    except asyncio.CancelledError:
                        res.error, res.error_kind = "请求已取消，费用未知", "cancelled"
                        res.total = time.monotonic() - began
                        await guard.settle(ticket, res)
                        if on_result:
                            on_result(res)
                        raise
                    except Exception as e:
                        res.error, res.error_kind = str(e), "other"
                        res.total = time.monotonic() - began
                    res.attempt = attempt
                    res.text, res.error = redact(res.text), redact(res.error)
                    await guard.settle(ticket, res)
                    if not warmup:
                        results.append(res)
                    if on_result:
                        on_result(res)
                if res.ok or warmup or res.status_code in (400, 401, 403, 404) or expired():
                    return
                if attempt <= opts.retries:
                    await pause(min(2 ** (attempt - 1), 8))

        await wait_for_active_window()
        for target in targets:
            if on_target_start:
                on_target_start(target)
            for i in range(opts.warmup):
                await request(target, -(i + 1), True)

        if opts.sample_interval:
            tick = 0
            while not expired():
                if not await wait_for_active_window():
                    break
                # Rotate first target each round; skip missed ticks instead of catching up.
                ordered = targets[tick % len(targets):] + targets[:tick % len(targets)]
                for target in ordered:
                    if expired():
                        break
                    await request(target, counters[target.label])
                    counters[target.label] += 1
                if guard.stop_reason in ("budget", "max_requests", "reported_cost_exceeded_reservation"):
                    break
                tick = max(tick + 1, int((time.monotonic() - start) // opts.sample_interval) + 1)
                delay = start + tick * opts.sample_interval - time.monotonic()
                due = next_active(time.time() + max(0, delay), opts.monitor_hours)
                guard.phase, guard.next_sample_at = "waiting", due
                # Recheck the clock after long idle periods; never catch up missed rounds.
                while not expired() and time.time() < due:
                    await pause(min(due - time.time(), 30))
        else:
            order = "parallel" if opts.parallel_targets else opts.order
            async def target_loop(target):
                while not expired() and (deadline is not None or counters[target.label] < opts.rounds):
                    before = guard.attempts
                    idx = counters[target.label]
                    counters[target.label] += 1
                    await request(target, idx)
                    if guard.attempts == before or guard.stop_reason:
                        break
                    await pause(opts.interval)
            if order == "parallel":
                await asyncio.gather(*(target_loop(t) for t in targets))
            elif order == "sequential":
                for target in targets:
                    await target_loop(target)
                    if expired() or guard.stop_reason:
                        break
            else:
                while not expired() and (deadline is not None or any(counters[t.label] < opts.rounds for t in targets)):
                    before = guard.attempts
                    for target in targets:
                        if expired() or guard.stop_reason:
                            break
                        count = opts.concurrency if deadline is not None else min(opts.concurrency, opts.rounds - counters[target.label])
                        first = counters[target.label]
                        counters[target.label] += max(0, count)
                        await asyncio.gather(*(request(target, first + i) for i in range(max(0, count))))
                        await pause(opts.interval)
                    if guard.attempts == before or guard.stop_reason:
                        break
    if not guard.stop_reason:
        guard.stop_reason = "duration" if expired() else "rounds"
    guard.phase, guard.next_sample_at = "finished", None
    return results

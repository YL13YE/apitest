"""异步执行器：单次请求计时、并发轮次、持续压测。"""
from __future__ import annotations

import asyncio
import time
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
    # 流式进度回调 (label, index, text_so_far, elapsed)，Web 界面用于实时输出
    progress_cb: Optional[Callable[[str, int, str, float], None]] = field(default=None, repr=False, compare=False)


@dataclass
class RequestResult:
    target: str
    model: str
    provider: str
    index: int
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

    @property
    def truncated(self) -> bool:
        return is_truncated(self.stop_reason)

    @property
    def gen_time(self) -> float:
        """生成阶段耗时：流式为首个可见文本 token 之后（不受思考片段是否透传影响）；
        非流式或整段一次性返回时为整体耗时。"""
        start = self.ttft_text if self.ttft_text > 0 else self.ttft
        if self.stream and start > 0 and self.chunks > 1 and self.total - start > 0.05:
            return self.total - start
        return max(self.total, 1e-6)

    @property
    def tps(self) -> float:
        if not self.ok or self.output_tokens <= 0:
            return 0.0
        return self.output_tokens / self.gen_time

    @property
    def max_gap(self) -> float:
        """流式 chunk 之间的最大停顿（秒）。"""
        if len(self.chunk_times) < 2:
            return 0.0
        return max(b - a for a, b in zip(self.chunk_times, self.chunk_times[1:]))

    def to_dict(self) -> Dict:
        d = asdict(self)
        d.pop("chunk_times", None)
        d["tps"] = round(self.tps, 2)
        d["gen_time"] = round(self.gen_time, 4)
        d["max_gap"] = round(self.max_gap, 4)
        d["truncated"] = self.truncated
        return d


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
    r = RequestResult(target=target.name, model=target.model, provider=target.provider, index=index, stream=opts.stream, warmup=warmup)
    url = chat_url(target, opts.stream)
    headers = auth_headers(target)
    body = build_body(target, opts.prompt, opts.system, opts.max_tokens, opts.temperature, opts.stream)
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
                if resp.status_code >= 400:
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
                            if v:
                                usage[k] = v
                    elif ev.kind == "stop" and ev.text:
                        r.stop_reason = ev.text
        else:
            resp = await client.post(url, headers=headers, json=body, timeout=opts.timeout)
            r.ttfb = time.perf_counter() - t0
            r.status_code = resp.status_code
            if resp.status_code >= 400:
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
    if r.output_tokens <= 0 and (text or reasoning_chars):
        r.output_tokens = estimate_tokens(text) + int(reasoning_chars * 0.25)
        r.tokens_estimated = True
    if r.input_tokens <= 0:
        r.input_tokens = estimate_tokens(opts.prompt) + estimate_tokens(opts.system or "")
        r.tokens_estimated = True
    if r.error:
        return r
    if not text and not reasoning_chars:
        r.error, r.error_kind = "空响应（无任何内容）", "empty"
        return r
    r.ok = True
    if target.price_in is not None or target.price_out is not None:
        r.cost = (r.input_tokens * (target.price_in or 0) + r.output_tokens * (target.price_out or 0)) / 1e6
    return r


async def _request_with_retry(client, target, opts, index, warmup=False) -> RequestResult:
    last: Optional[RequestResult] = None
    for attempt in range(1, opts.retries + 2):
        res = await do_request(client, target, opts, index, warmup)
        res.attempt = attempt
        if res.ok or res.error_kind == "http" and res.status_code in (400, 401, 403, 404):
            return res
        last = res
        if attempt <= opts.retries:
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
    assert last is not None
    return last


def make_client(opts: RunOptions) -> httpx.AsyncClient:
    limits = httpx.Limits(max_connections=max(opts.concurrency * 2, 10), max_keepalive_connections=max(opts.concurrency, 5))
    kwargs = dict(timeout=opts.timeout, limits=limits, verify=opts.verify_ssl, http2=opts.http2, follow_redirects=True)
    if opts.proxy:
        try:
            return httpx.AsyncClient(proxy=opts.proxy, **kwargs)
        except TypeError:  # httpx < 0.26
            return httpx.AsyncClient(proxies=opts.proxy, **kwargs)
    return httpx.AsyncClient(**kwargs)


async def run_target(
    client: httpx.AsyncClient,
    target: Target,
    opts: RunOptions,
    on_result: Optional[ResultCallback] = None,
    stop: Optional[asyncio.Event] = None,
) -> List[RequestResult]:
    results: List[RequestResult] = []
    stop = stop or asyncio.Event()

    # 预热：串行执行，不计入统计
    for i in range(opts.warmup):
        res = await do_request(client, target, opts, index=-(i + 1), warmup=True)
        if on_result:
            on_result(res)

    sem = asyncio.Semaphore(max(1, opts.concurrency))
    counter = {"i": 0}
    deadline = time.perf_counter() + opts.duration if opts.duration else None

    async def one(idx: int):
        async with sem:
            if stop.is_set():
                return
            res = await _request_with_retry(client, target, opts, idx)
            results.append(res)
            if on_result:
                on_result(res)
            if opts.interval > 0:
                await asyncio.sleep(opts.interval)

    if deadline is None:
        await asyncio.gather(*(one(i) for i in range(opts.rounds)))
    else:
        async def worker():
            while not stop.is_set() and time.perf_counter() < deadline:
                counter["i"] += 1
                await one(counter["i"] - 1)

        await asyncio.gather(*(worker() for _ in range(max(1, opts.concurrency))))
    results.sort(key=lambda x: x.index)
    return results


async def run_interleaved(
    client: httpx.AsyncClient,
    targets: List[Target],
    opts: RunOptions,
    on_result: Optional[ResultCallback],
    stop: asyncio.Event,
) -> List[RequestResult]:
    """交替轮询：每一轮依次给每个目标发请求（并发 c 时每轮每目标发 c 个），把时间漂移摊平。"""
    results: List[RequestResult] = []
    for t in targets:
        for i in range(opts.warmup):
            res = await do_request(client, t, opts, index=-(i + 1), warmup=True)
            if on_result:
                on_result(res)
    counters = {t.label: 0 for t in targets}
    deadline = time.perf_counter() + opts.duration if opts.duration else None
    c = max(1, opts.concurrency)

    async def one(t: Target) -> None:
        idx = counters[t.label]
        counters[t.label] += 1
        res = await _request_with_retry(client, t, opts, idx)
        results.append(res)
        if on_result:
            on_result(res)

    while not stop.is_set():
        if deadline is None and all(counters[t.label] >= opts.rounds for t in targets):
            break
        if deadline is not None and time.perf_counter() >= deadline:
            break
        for t in targets:
            if stop.is_set():
                break
            remaining = c if deadline is not None else min(c, opts.rounds - counters[t.label])
            if remaining <= 0:
                continue
            await asyncio.gather(*(one(t) for _ in range(remaining)))
            if opts.interval > 0:
                await asyncio.sleep(opts.interval)
    results.sort(key=lambda x: (x.target, x.model, x.index))
    return results


async def run_all(
    targets: List[Target],
    opts: RunOptions,
    on_result: Optional[ResultCallback] = None,
    on_target_start: Optional[Callable[[Target], None]] = None,
) -> List[RequestResult]:
    all_results: List[RequestResult] = []
    stop = asyncio.Event()
    order = "parallel" if opts.parallel_targets else (opts.order or "interleaved")
    if len(targets) < 2:
        order = "sequential"
    async with make_client(opts) as client:
        try:
            if order == "interleaved":
                for t in targets:
                    if on_target_start:
                        on_target_start(t)
                all_results.extend(await run_interleaved(client, targets, opts, on_result, stop))
            elif order == "parallel":
                for t in targets:
                    if on_target_start:
                        on_target_start(t)
                groups = await asyncio.gather(*(run_target(client, t, opts, on_result, stop) for t in targets))
                for g in groups:
                    all_results.extend(g)
            else:
                for t in targets:
                    if on_target_start:
                        on_target_start(t)
                    all_results.extend(await run_target(client, t, opts, on_result, stop))
        except (KeyboardInterrupt, asyncio.CancelledError):
            stop.set()
            raise
    return all_results

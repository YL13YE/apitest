"""本地 Web 图形界面：apitest web"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import os
import secrets
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import yaml
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route

from . import __version__
from .config import EXAMPLE_CONFIG, endpoint_targets, expand_env, find_config, load_config
from .history import (
    HISTORY_PATH,
    clear as history_clear,
    clear_pings,
    delete_ping,
    delete_run,
    find_run,
    load_pings,
    load_runs,
    make_ping_record,
    save_ping,
    save_run,
)
from .metrics import Summary, apply_baseline, comparability_notes, rank, summarize, hourly_rows, normalize_saved_run
from .budget import RunControl, validate_options
from .evaluation import build_overview, scenario_metadata
from .netcheck import ping_once
from .prompts import PRESETS
from .providers import Target, auth_headers, models_url, parse_models
from .report import build_payload, now_str, to_csv, to_markdown
from .runner import RequestResult, RunOptions, run_all
from .pricing import validate_price_multiplier, apply_price_catalogs
from .planning import monitor_estimate, observed_usage_bounds
from .security import MASK, LocalBoundary, private_write, public_endpoints, redact, register_secrets, resolve_browser_endpoints, validate_endpoint

STATIC = Path(__file__).parent / "static"


class RunState:
    def __init__(self, run_id: str, targets: List[Target], opts: RunOptions, meta: Dict[str, Any]):
        self.id = run_id
        self.targets = targets
        self.opts = opts
        self.meta = meta
        self.results: List[RequestResult] = []
        self.summaries: List[Dict[str, Any]] = []
        self.ranking: Dict[str, Optional[str]] = {}
        self.status = "running"
        self.error = ""
        self.started = time.time()
        self.subscribers: List[asyncio.Queue] = []
        self.task: Optional[asyncio.Task] = None
        self.history_id: Optional[str] = None

    def publish(self, event: Dict[str, Any]) -> None:
        for q in list(self.subscribers):
            if q.full():
                q.get_nowait()
            q.put_nowait(event)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "error": self.error,
            "meta": self.meta,
            "targets": [t.label for t in self.targets],
            "results": [r.to_dict() for r in self.results],
            "summaries": self.summaries,
            "ranking": self.ranking,
            "history_id": self.history_id,
            "elapsed": round(time.time() - self.started, 2),
            "budget": self.opts.control.snapshot() if self.opts.control else {},
            "hourly": hourly_rows(self.results),
        }

    def finalize(self) -> None:
        self.meta["budget"] = self.opts.control.snapshot() if self.opts.control else {}
        self.meta["status"] = self.status
        order = {t.label: i for i, t in enumerate(self.targets)}
        self.results.sort(key=lambda r: (order.get(f"{r.target}/{r.model}", 999), r.index))
        sums = summarize(self.results)
        apply_baseline(sums, self.meta.get("baseline"))
        self.summaries = [s.to_dict() for s in sums]
        self.ranking = {k: (v.label if v else None) for k, v in rank(sums).items()}
        self.meta["notes"] = comparability_notes(sums)
        self.meta["elapsed"] = round(time.time() - self.started, 2)
        self.meta["interrupted"] = self.status == "stopped"
        if self.results and not self.meta.get("no_history"):
            try:
                self.history_id = save_run(self.meta, self.summaries, [r.to_dict() for r in self.results], self.id)
            except OSError:
                pass


RUNS: Dict[str, RunState] = {}
CONFIG_PATH: Optional[Path] = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _raw_config() -> Dict[str, Any]:
    """读取原始（未展开环境变量）配置。"""
    if CONFIG_PATH and CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    data.setdefault("defaults", {})
    data.setdefault("endpoints", {})
    for ep in expand_env(data["endpoints"]).values():
        register_secrets(ep.get("api_key"), ep.get("key"), *(ep.get("headers") or {}).values())
    return data


def _targets_from_request(body: Dict[str, Any]) -> List[Target]:
    raw = _raw_config()
    endpoints = resolve_browser_endpoints(body.get("endpoints") or {}, raw["endpoints"])
    endpoints = apply_price_catalogs(endpoints, raw.get("price_catalogs"))
    targets: List[Target] = []
    for sel in body.get("targets") or []:
        name, model = sel.get("endpoint"), sel.get("model")
        if name not in endpoints:
            raise ValueError(f"未知端点: {name}")
        targets.extend(endpoint_targets(name, endpoints[name], [model] if model else None))
    seen, uniq = set(), []
    for t in targets:
        if t.label not in seen:
            seen.add(t.label)
            uniq.append(t)
    if not uniq:
        raise ValueError("请至少选择一个模型")
    return uniq


def _single_target(body: Dict[str, Any]) -> Target:
    endpoints = resolve_browser_endpoints(body.get("endpoints") or {}, _raw_config()["endpoints"])
    name = body.get("endpoint")
    if name not in endpoints:
        raise ValueError(f"未知端点: {name}")
    ep = dict(endpoints[name])
    ep.setdefault("models", ["-"])
    if not ep.get("models"):
        ep["models"] = ["-"]
    return endpoint_targets(name, ep)[0]


def _err(e: Exception, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": redact(str(e))}, status_code=code)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


async def index(request: Request) -> Response:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    token = request.scope["apitest_token"]
    response = HTMLResponse(html.replace("{{VERSION}}", __version__).replace("{{SESSION_TOKEN}}", token).replace("<script", '<script nonce="' + token + '"'))
    response.set_cookie("apitest_session", token, httponly=True, samesite="strict", secure=request.url.scheme == "https")
    return response


_ICON_TYPES = {"favicon.svg": "image/svg+xml", "favicon.ico": "image/x-icon", "favicon-192.png": "image/png", "i18n.js": "application/javascript", "chart.umd.min.js": "application/javascript", "overview.js": "application/javascript"}


def icon_route(name: str) -> Route:
    async def handler(request: Request) -> Response:
        return Response((STATIC / name).read_bytes(), media_type=_ICON_TYPES[name], headers={"Cache-Control": "no-cache" if name.endswith(".js") else "public, max-age=86400"})

    return Route("/" + name, handler)


async def get_config(request: Request) -> Response:
    raw = _raw_config()
    return JSONResponse(
        {
            "path": str(CONFIG_PATH) if CONFIG_PATH else None,
            "exists": bool(CONFIG_PATH and CONFIG_PATH.exists()),
            "defaults": redact(raw["defaults"]),
            "endpoints": public_endpoints(apply_price_catalogs(raw["endpoints"], raw.get("price_catalogs"))),
            "presets": {k: {"prompt": v["prompt"], "max_tokens": v["max_tokens"], "desc": v["desc"]} for k, v in PRESETS.items()},
            "version": __version__,
            "history_path": str(HISTORY_PATH),
            "supports_price_multiplier": True,
        }
    )


async def save_config(request: Request) -> Response:
    global CONFIG_PATH
    body = await request.json()
    raw = _raw_config()
    if "endpoints" in body:
        clean = {}
        for name, ep in body["endpoints"].items():
            ep = dict(ep)
            ep.pop("price_defaults", None)
            validate_endpoint(ep)
            if "price_multiplier" in ep:
                ep["price_multiplier"] = validate_price_multiplier(ep["price_multiplier"])
            old = raw["endpoints"].get(name, {})
            same = (ep.get("base_url", ep.get("url", "")), ep.get("provider", "")) == (old.get("base_url", old.get("url", "")), old.get("provider", ""))
            for k in ("api_key", "key"):
                if ep.get(k) == MASK:
                    if not same:
                        return _err(ValueError("地址已变化，请重新输入 Key"))
                    ep[k] = old.get(k, "")
                elif "${" in str(ep.get(k, "")) and ep.get(k) != old.get(k):
                    return _err(ValueError("环境变量引用请在本地配置文件设置"))
            ep["headers"] = {k: (old.get("headers", {}).get(k, "") if v == MASK and same else v) for k, v in ep.get("headers", {}).items()}
            if any("${" in str(v) and v != old.get("headers", {}).get(k) for k, v in ep["headers"].items()):
                return _err(ValueError("请求头环境变量引用请在本地配置文件设置"))
            if any(v == MASK for v in ep["headers"].values()):
                return _err(ValueError("地址已变化，请重新输入请求头"))
            clean[name] = ep
        raw["endpoints"] = clean
    if "defaults" in body:
        raw["defaults"] = body["defaults"]
    path = CONFIG_PATH or Path("apitest.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    private_write(path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    CONFIG_PATH = path
    return JSONResponse({"ok": True, "path": str(path)})


def _run_options(options, *, preview=False):
    o = dict(options or {})
    if o.get("mode") == "test":
        o = dict(o, days=None, hours=None, sample_minutes=None, hourly_budget=None, monitor_hours=None)
    elif o.get("mode") == "monitor":
        o = dict(o, max_requests=None)  # Older browser forms must not retain the old 48-call cap.
    preset_name = o.get("preset") or "medium"
    preset = PRESETS.get(preset_name, PRESETS["medium"])
    prompt = (o.get("prompt") or "").strip() or preset["prompt"]
    preset_label = preset_name if not (o.get("prompt") or "").strip() else None
    if o.get("days") not in (None, ""):
        days = float(o["days"])
        if not 1 <= days <= 7 or not days.is_integer():
            raise ValueError("观察天数必须是1至7的整数")
        duration = days * 86400
    else:
        duration = float(o["hours"]) * 3600 if o.get("hours") else (float(o["duration"]) if o.get("duration") else None)
    opts = RunOptions(
        prompt=prompt,
        system=(o.get("system") or "").strip() or None,
        max_tokens=int(o.get("max_tokens") or preset["max_tokens"]),
        temperature=float(o["temperature"]) if o.get("temperature") not in (None, "") else None,
        stream=bool(o.get("stream", True)),
        rounds=max(1, int(o.get("rounds") or 3)),
        concurrency=max(1, int(o.get("concurrency") or 1)),
        warmup=int(o.get("warmup") or 0),
        timeout=float(o.get("timeout") or 120),
        interval=float(o.get("interval") or 0),
        duration=duration,
        retries=int(o.get("retries") or 0),
        parallel_targets=bool(o.get("parallel_targets")),
        order=str(o.get("order") or ("parallel" if o.get("parallel_targets") else "interleaved")),
        verify_ssl=not bool(o.get("insecure")),
        proxy=(o.get("proxy") or "").strip() or None,
        sample_interval=float(o.get("sample_minutes") or 0) * 60,
        budget=float(o["budget"]) if o.get("budget") not in (None, "") else None,
        hourly_budget=float(o["hourly_budget"]) if o.get("hourly_budget") not in (None, "") else None,
        max_requests=int(o["max_requests"]) if o.get("max_requests") not in (None, "") else None,
        cache_mode=o.get("cache_mode") or "cold",
        monitor_hours=o.get("monitor_hours"),
    )
    validate_options(opts, require_cost_limit=not preview)
    if o.get("mode") == "monitor" and (not opts.duration or not opts.sample_interval or (not preview and opts.budget is None and opts.hourly_budget is None)):
        raise ValueError("实时监控需要设置观察天数、采样间隔，以及总预算或每小时预算")
    if opts.concurrency > 16 or opts.max_tokens > 131072 or (opts.duration or 0) > 7 * 86400:
        raise ValueError("Web 上限：并发16，输出131072 tokens，持续7天")
    return opts, o, preset_label, prompt, duration


async def start_run(request: Request) -> Response:
    body = await request.json()
    try:
        targets = _targets_from_request(body)
    except ValueError as e:
        return _err(e)
    opts, o, preset_label, prompt, duration = _run_options(body.get("options"))
    monitoring = bool(opts.sample_interval)
    if any(st.status == "running" and bool(st.opts.sample_interval) == monitoring for st in RUNS.values()):
        return _err(ValueError("已有监控任务运行" if monitoring else "已有临时测速任务运行"), 409)
    opts.usage_bounds, _ = observed_usage_bounds(load_runs(1000), targets, opts, preset_label)
    RunControl(targets, opts)  # fail closed before creating a background task
    meta = {
        "time": now_str(),
        "preset": preset_label,
        "prompt_chars": len(prompt),
        "system": bool(opts.system),
        "max_tokens": opts.max_tokens,
        "stream": opts.stream,
        "rounds": None if duration else opts.rounds,
        "duration": duration,
        "concurrency": opts.concurrency,
        "warmup": opts.warmup,
        "parallel_targets": opts.parallel_targets,
        "order": "parallel" if opts.parallel_targets else opts.order,
        "baseline": (o.get("baseline") or "").strip() or None,
        "tag": (o.get("tag") or "").strip() or None,
        "targets": [t.label for t in targets],
        "version": __version__,
        "source": "web",
        "no_history": bool(o.get("no_history")),
        "sample_interval": opts.sample_interval,
        "mode": "monitor" if opts.sample_interval else "test",
        "monitor_hours": opts.monitor_hours,
        "timezone": "Asia/Shanghai",
        "cache_mode": opts.cache_mode,
        **scenario_metadata(opts, preset_label, targets),
    }
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    state = RunState(run_id, targets, opts, meta)
    RUNS[run_id] = state

    def on_result(r: RequestResult) -> None:
        state.results.append(r)
        state.finalize()  # incremental checkpoint survives refresh/process interruption
        state.publish({"type": "result", "data": r.to_dict()})
        state.publish({"type": "telemetry", "budget": opts.control.snapshot(), "hourly": hourly_rows(state.results)})

    def on_progress(label: str, index: int, text: str, elapsed: float) -> None:
        if state.subscribers:
            state.publish({"type": "progress", "label": label, "index": index, "text": redact(text[-700:]), "chars": len(text), "elapsed": round(elapsed, 2)})

    opts.progress_cb = on_progress

    async def go():
        try:
            await run_all(targets, opts, on_result=on_result, on_target_start=lambda t: state.publish({"type": "target_start", "label": t.label}))
            state.status = "done"
        except asyncio.CancelledError:
            state.status = "stopped"
        except Exception as e:  # noqa: BLE001
            state.status, state.error = "error", redact(f"{e.__class__.__name__}: {e}")
        state.finalize()
        state.publish({"type": "done", "data": state.snapshot()})

    state.task = asyncio.create_task(go())
    return JSONResponse({"id": run_id, "targets": [t.label for t in targets], "per_target_total": None if duration else opts.rounds})


async def run_snapshot(request: Request) -> Response:
    st = RUNS.get(request.path_params["run_id"])
    if not st:
        return _err(ValueError("run 不存在"), 404)
    return JSONResponse(st.snapshot())


async def stop_run(request: Request) -> Response:
    st = RUNS.get(request.path_params["run_id"])
    if not st:
        return _err(ValueError("run 不存在"), 404)
    if st.task and not st.task.done():
        st.task.cancel()
        await asyncio.gather(st.task, return_exceptions=True)
        if st.status == "running":
            st.status = "stopped"
            st.finalize()
            st.publish({"type": "done", "data": st.snapshot()})
    return JSONResponse({"ok": True})


async def run_events(request: Request) -> Response:
    st = RUNS.get(request.path_params["run_id"])
    if not st:
        return _err(ValueError("run 不存在"), 404)
    q: asyncio.Queue = asyncio.Queue(maxsize=128)
    st.subscribers.append(q)

    async def gen():
        try:
            yield f"data: {json.dumps({'type': 'snapshot', 'data': st.snapshot()}, ensure_ascii=False)}\n\n"
            if st.status != "running":
                return
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    return
        finally:
            if q in st.subscribers:
                st.subscribers.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def run_export(request: Request) -> Response:
    st = RUNS.get(request.path_params["run_id"])
    if not st:
        return _err(ValueError("run 不存在"), 404)
    fmt = request.query_params.get("fmt", "json")
    sums = summarize(st.results)
    name = f"apitest-{st.id}.{fmt}"
    if fmt == "json":
        content, mt = json.dumps(build_payload(st.meta, sums, st.results), ensure_ascii=False, indent=2), "application/json"
    elif fmt == "csv":
        content, mt = to_csv(st.results), "text/csv"
    elif fmt == "md":
        content, mt = to_markdown(st.meta, sums), "text/markdown"
    else:
        return _err(ValueError("fmt 应为 json/csv/md"))
    return Response(content, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def history_list(request: Request) -> Response:
    last = int(request.query_params.get("last", 50))
    return JSONResponse([normalize_saved_run(run) for run in reversed(load_runs(last))])


async def model_overview(request: Request) -> Response:
    """Only aggregate numeric observations; never expose raw text, keys or URLs."""
    params = request.query_params
    hours = None if params.get("hours") == "all" else float(params.get("hours", "24"))
    runs = {r.get("id"): r for r in load_runs(1000)}
    for state in RUNS.values():
        if state.status == "running":
            runs[state.id] = {"id": state.id, "meta": state.meta, "results": [r.to_dict() for r in state.results]}
    thresholds = {k: params[k] for k in ("ttfa", "tps", "gap") if params.get(k)}
    payload = await asyncio.to_thread(build_overview, list(runs.values()), hours=hours, profile=params.get("profile", "interactive"), cohort=params.get("cohort"), thresholds=thresholds)
    payload["history_run_limit"] = 1000
    return JSONResponse(redact(payload))


async def history_item(request: Request) -> Response:
    run = find_run(request.path_params["run_id"])
    if not run:
        return _err(ValueError("记录不存在"), 404)
    run = normalize_saved_run(run)
    run["hourly"] = hourly_rows(run.get("results") or [])
    return JSONResponse(redact(run))


async def list_models(request: Request) -> Response:
    body = await request.json()
    try:
        t = _single_target(body)
    except ValueError as e:
        return _err(e)
    url = models_url(t)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False, verify=not body.get("insecure")) as c:
            t0 = time.perf_counter()
            resp = await c.get(url, headers=auth_headers(t))
            dt = time.perf_counter() - t0
        if resp.status_code >= 300:
            return _err(ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}"), 502)
        models = parse_models(t.provider, resp.json())
    except httpx.HTTPError as e:
        return _err(ValueError(f"请求失败: {e}"), 502)
    models.sort(key=lambda m: m["id"])
    return JSONResponse({"url": url, "elapsed": round(dt, 3), "models": models})


async def active_runs(request):
    return JSONResponse([st.snapshot() for st in RUNS.values() if st.status == "running"])


async def estimate_monitor(request: Request) -> Response:
    body = await request.json()
    targets = _targets_from_request(body)
    options = dict(body.get("options") or {}, mode="monitor")
    opts, _, preset, _, _ = _run_options(options, preview=True)
    start = float(body.get("start_at", time.time()))
    bounds, count = observed_usage_bounds(load_runs(1000), targets, opts, preset)
    return JSONResponse(monitor_estimate(targets, opts, start, usage_bounds=bounds, observed_samples=count))


async def active_monitors(request):
    """Small polling payload without response text or request credentials."""
    monitors = []
    for state in RUNS.values():
        if state.status != "running" or not state.opts.sample_interval:
            continue
        control = state.opts.control
        ends_at = state.started + state.opts.duration if state.opts.duration else None
        next_sample = control.next_sample_at if control else None
        if next_sample and ends_at and next_sample >= ends_at:
            next_sample = None
        monitors.append({"id": state.id, "started_at": state.started,
            "ends_at": ends_at,
            "elapsed": round(time.time() - state.started, 2),
            "sample_interval": state.opts.sample_interval, "duration": state.opts.duration,
            "monitor_hours": state.opts.monitor_hours, "timezone": "Asia/Shanghai",
            "phase": control.phase if control else "sampling", "next_sample_at": next_sample,
            "parameters": {"preset": state.meta.get("preset"), "prompt_chars": len(state.opts.prompt),
                "max_tokens": state.opts.max_tokens, "timeout": state.opts.timeout,
                "warmup": state.opts.warmup, "retries": state.opts.retries,
                "cache_mode": state.opts.cache_mode, "stream": state.opts.stream,
                "concurrency": state.opts.concurrency, "order": state.opts.order,
                "temperature": state.opts.temperature},
            "targets": [t.label for t in state.targets], "tag": state.meta.get("tag"),
            "completed": len([r for r in state.results if not r.warmup]),
            "successful": sum(r.ok for r in state.results if not r.warmup),
            "budget": state.opts.control.snapshot() if state.opts.control else {}})
    return JSONResponse(redact(monitors))


async def ping(request: Request) -> Response:
    body = await request.json()
    try:
        t = _single_target(body)
    except ValueError as e:
        return _err(e)
    n = max(1, min(10, int(body.get("rounds") or 3)))
    rows = []
    for _ in range(n):
        r = await asyncio.to_thread(ping_once, t, float(body.get("timeout") or 10), body.get("proxy") or None, not body.get("insecure"))
        rows.append({"host": r.host, "ip": r.ip, "dns": r.dns, "tcp": r.tcp, "tls": r.tls, "http": r.http, "status": r.status, "error": r.error, "server": r.server})
    rec_id = None
    if not body.get("no_history") and t.name != "__tmp__":
        try:
            rec_id = save_ping(make_ping_record(t.name, models_url(t), rows, source="web", proxy=body.get("proxy") or None))
        except OSError:
            pass
    return JSONResponse({"endpoint": t.name, "url": models_url(t), "rows": rows, "history_id": rec_id})


async def ping_history_list(request: Request) -> Response:
    ep = request.query_params.get("endpoint") or None
    last = int(request.query_params.get("last", 200))
    return JSONResponse(list(reversed(load_pings(ep, last))))


async def ping_history_delete(request: Request) -> Response:
    return JSONResponse({"ok": delete_ping(request.path_params["ping_id"])})


async def ping_history_clear(request: Request) -> Response:
    n = clear_pings(request.query_params.get("endpoint") or None)
    return JSONResponse({"ok": True, "deleted": n})


async def example_config(request: Request) -> Response:
    return PlainTextResponse(EXAMPLE_CONFIG)


async def raw_config(request: Request) -> Response:
    raw = _raw_config()
    raw["endpoints"] = public_endpoints(raw["endpoints"])
    return PlainTextResponse(yaml.safe_dump(redact(raw), allow_unicode=True, sort_keys=False))


async def history_export(request: Request) -> Response:
    run = find_run(request.path_params["run_id"])
    if not run:
        return _err(ValueError("记录不存在"), 404)
    run = normalize_saved_run(run)
    fmt = request.query_params.get("fmt", "json")
    name = f"apitest-{run['id']}.{fmt}"
    if fmt == "json":
        content, mt = json.dumps(run, ensure_ascii=False, indent=2), "application/json"
    elif fmt == "csv":
        content, mt = to_csv(run.get("results") or []), "text/csv"
    elif fmt == "md":
        content, mt = to_markdown(run.get("meta") or {}, [Summary.from_dict(s) for s in run.get("summaries") or []]), "text/markdown"
    else:
        return _err(ValueError("fmt 应为 json/csv/md"))
    return Response(content, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def history_delete(request: Request) -> Response:
    ok = delete_run(request.path_params["run_id"])
    return JSONResponse({"ok": ok})


async def history_clear_all(request: Request) -> Response:
    history_clear()
    return JSONResponse({"ok": True})


routes = [
    Route("/api/overview", model_overview),
    icon_route("overview.js"),
    Route("/api/runs/active", active_runs),
    Route("/api/monitors/active", active_monitors),
    Route("/api/monitor/estimate", estimate_monitor, methods=["POST"]),
    icon_route("chart.umd.min.js"),
    icon_route("favicon.svg"),
    icon_route("favicon.ico"),
    icon_route("favicon-192.png"),
    icon_route("i18n.js"),
    Route("/api/config/raw", raw_config),
    Route("/api/history/{run_id}/export", history_export),
    Route("/api/history/{run_id}", history_delete, methods=["DELETE"]),
    Route("/api/history", history_clear_all, methods=["DELETE"]),
    Route("/", index),
    Route("/api/config", get_config, methods=["GET"]),
    Route("/api/config", save_config, methods=["POST"]),
    Route("/api/config/example", example_config),
    Route("/api/run", start_run, methods=["POST"]),
    Route("/api/run/{run_id}", run_snapshot),
    Route("/api/run/{run_id}/stop", stop_run, methods=["POST"]),
    Route("/api/run/{run_id}/events", run_events),
    Route("/api/run/{run_id}/export", run_export),
    Route("/api/history", history_list),
    Route("/api/history/{run_id}", history_item),
    Route("/api/models", list_models, methods=["POST"]),
    Route("/api/ping", ping, methods=["POST"]),
    Route("/api/ping/history", ping_history_list),
    Route("/api/ping/history", ping_history_clear, methods=["DELETE"]),
    Route("/api/ping/history/{ping_id}", ping_history_delete, methods=["DELETE"]),
]

async def input_error(request, exc):
    return _err(exc)


app = LocalBoundary(Starlette(routes=routes, exception_handlers={ValueError: input_error, TypeError: input_error, KeyError: input_error}))


def serve(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True, config_path: Optional[str] = None, password_file: Optional[str] = None, ssl_certfile: Optional[str] = None, ssl_keyfile: Optional[str] = None) -> None:
    global CONFIG_PATH
    import uvicorn

    if bool(ssl_certfile) != bool(ssl_keyfile):
        raise ValueError("TLS 证书和私钥必须同时提供")
    if host not in ("127.0.0.1", "localhost", "::1"):
        password = os.environ.get("APITEST_WEB_PASSWORD")
        if not password:
            path = Path(password_file).expanduser() if password_file else Path.home() / ".apitest" / "web-password"
            if path.is_symlink():
                raise ValueError("密码文件不能是符号链接")
            if path.exists():
                password = path.read_text(encoding="utf-8").strip()
                if len(password) < 16:
                    raise ValueError("密码文件中的密码至少16个字符")
                path.chmod(0o600)
            else:
                password = secrets.token_urlsafe(32)
                private_write(path, password + "\n")
            print(f"远程访问用户名: apitest；密码文件: {path}")
        app.enable_remote(password)
    else:
        app.remote_password = None

    CONFIG_PATH = find_config(config_path) if config_path else find_config(None)
    scheme = "https" if ssl_certfile else "http"
    url = f"{scheme}://{'[' + host + ']' if ':' in host else host}:{port}"
    print(f"apitest web 界面: {url}   (配置文件: {CONFIG_PATH or '未找到，保存时将创建 ./apitest.yaml'})   Ctrl+C 退出")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    uvicorn.run(app, host=host, port=port, log_level="warning", ssl_certfile=ssl_certfile, ssl_keyfile=ssl_keyfile, proxy_headers=False)

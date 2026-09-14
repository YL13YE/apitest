"""本地 Web 图形界面：apitest web"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
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
from .metrics import Summary, apply_baseline, comparability_notes, rank, summarize
from .netcheck import ping_once
from .prompts import PRESETS
from .providers import Target, auth_headers, models_url, parse_models
from .report import build_payload, now_str, to_csv, to_markdown
from .runner import RequestResult, RunOptions, run_all

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
        }

    def finalize(self) -> None:
        order = {t.label: i for i, t in enumerate(self.targets)}
        self.results.sort(key=lambda r: (order.get(f"{r.target}/{r.model}", 999), r.index))
        sums = summarize(self.results)
        apply_baseline(sums, self.meta.get("baseline"))
        self.summaries = [s.to_dict() for s in sums]
        self.ranking = {k: (v.label if v else None) for k, v in rank(sums).items()}
        self.meta["notes"] = comparability_notes(sums)
        self.meta["elapsed"] = round(time.time() - self.started, 2)
        self.meta["interrupted"] = self.status == "stopped"
        if self.summaries and not self.meta.get("no_history"):
            try:
                self.history_id = save_run(self.meta, self.summaries, [r.to_dict() for r in self.results])
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
    return data


def _targets_from_request(body: Dict[str, Any]) -> List[Target]:
    endpoints = expand_env(body.get("endpoints") or {})
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
    endpoints = expand_env(body.get("endpoints") or {})
    name = body.get("endpoint")
    if name not in endpoints:
        raise ValueError(f"未知端点: {name}")
    ep = dict(endpoints[name])
    ep.setdefault("models", ["-"])
    if not ep.get("models"):
        ep["models"] = ["-"]
    return endpoint_targets(name, ep)[0]


def _err(e: Exception, code: int = 400) -> JSONResponse:
    return JSONResponse({"error": str(e)}, status_code=code)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


async def index(request: Request) -> Response:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html.replace("{{VERSION}}", __version__))


_ICON_TYPES = {"favicon.svg": "image/svg+xml", "favicon.ico": "image/x-icon", "favicon-192.png": "image/png", "i18n.js": "application/javascript"}


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
            "defaults": raw["defaults"],
            "endpoints": raw["endpoints"],
            "presets": {k: {"prompt": v["prompt"], "max_tokens": v["max_tokens"], "desc": v["desc"]} for k, v in PRESETS.items()},
            "version": __version__,
            "history_path": str(HISTORY_PATH),
        }
    )


async def save_config(request: Request) -> Response:
    global CONFIG_PATH
    body = await request.json()
    raw = _raw_config()
    if "endpoints" in body:
        raw["endpoints"] = body["endpoints"]
    if "defaults" in body:
        raw["defaults"] = body["defaults"]
    path = CONFIG_PATH or Path("apitest.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
    CONFIG_PATH = path
    return JSONResponse({"ok": True, "path": str(path)})


async def start_run(request: Request) -> Response:
    body = await request.json()
    try:
        targets = _targets_from_request(body)
    except ValueError as e:
        return _err(e)
    o = body.get("options") or {}
    preset_name = o.get("preset") or "medium"
    preset = PRESETS.get(preset_name, PRESETS["medium"])
    prompt = (o.get("prompt") or "").strip() or preset["prompt"]
    preset_label = preset_name if not (o.get("prompt") or "").strip() else None
    duration = float(o["duration"]) if o.get("duration") else None
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
    )
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
    }
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
    state = RunState(run_id, targets, opts, meta)
    RUNS[run_id] = state

    def on_result(r: RequestResult) -> None:
        if not r.warmup:
            state.results.append(r)
        state.publish({"type": "result", "data": r.to_dict()})

    def on_progress(label: str, index: int, text: str, elapsed: float) -> None:
        if state.subscribers:
            state.publish({"type": "progress", "label": label, "index": index, "text": text[-700:], "chars": len(text), "elapsed": round(elapsed, 2)})

    opts.progress_cb = on_progress

    async def go():
        try:
            await run_all(targets, opts, on_result=on_result, on_target_start=lambda t: state.publish({"type": "target_start", "label": t.label}))
            state.status = "done"
        except asyncio.CancelledError:
            state.status = "stopped"
        except Exception as e:  # noqa: BLE001
            state.status, state.error = "error", f"{e.__class__.__name__}: {e}"
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
    return JSONResponse({"ok": True})


async def run_events(request: Request) -> Response:
    st = RUNS.get(request.path_params["run_id"])
    if not st:
        return _err(ValueError("run 不存在"), 404)
    q: asyncio.Queue = asyncio.Queue()
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
    return JSONResponse(list(reversed(load_runs(last))))


async def history_item(request: Request) -> Response:
    run = find_run(request.path_params["run_id"])
    if not run:
        return _err(ValueError("记录不存在"), 404)
    return JSONResponse(run)


async def list_models(request: Request) -> Response:
    body = await request.json()
    try:
        t = _single_target(body)
    except ValueError as e:
        return _err(e)
    url = models_url(t)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True, verify=not body.get("insecure")) as c:
            t0 = time.perf_counter()
            resp = await c.get(url, headers=auth_headers(t))
            dt = time.perf_counter() - t0
        if resp.status_code >= 400:
            return _err(ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}"), 502)
        models = parse_models(t.provider, resp.json())
    except httpx.HTTPError as e:
        return _err(ValueError(f"请求失败: {e}"), 502)
    models.sort(key=lambda m: m["id"])
    return JSONResponse({"url": url, "elapsed": round(dt, 3), "models": models})


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
    if CONFIG_PATH and CONFIG_PATH.exists():
        return PlainTextResponse(CONFIG_PATH.read_text(encoding="utf-8"))
    return PlainTextResponse("# 尚未创建配置文件\n")


async def history_export(request: Request) -> Response:
    run = find_run(request.path_params["run_id"])
    if not run:
        return _err(ValueError("记录不存在"), 404)
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

app = Starlette(routes=routes)


def serve(host: str = "127.0.0.1", port: int = 8787, open_browser: bool = True, config_path: Optional[str] = None) -> None:
    global CONFIG_PATH
    import uvicorn

    CONFIG_PATH = find_config(config_path) if config_path else find_config(None)
    url = f"http://{host}:{port}"
    print(f"apitest web 界面: {url}   (配置文件: {CONFIG_PATH or '未找到，保存时将创建 ./apitest.yaml'})   Ctrl+C 退出")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    uvicorn.run(app, host=host, port=port, log_level="warning")

"""Explicitly scoped, whitelisted static exports. No API requests or credentials."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
import time
from pathlib import Path

from .evaluation import build_overview, row_effort
from .security import private_write, redact

STATIC = Path(__file__).parent / "static"
WINDOWS = (1, 6, 24, 72, 168)
MARKER = ".apitest-public.json"
FILES = (MARKER, ".nojekyll", "LICENSE", "index.html", "data.json", "manifest.json", "assets/site.css",
         "assets/overview.js", "assets/public.js", "assets/chart.umd.min.js",
         "assets/Chart.js.LICENSE.md", "assets/favicon.svg")
ROW_FIELDS = ("model", "index", "attempt", "warmup", "started_at", "ok", "stream",
              "text_chars", "ttft_text", "total", "gen_time", "tps", "tps_eligible",
              "single_chunk", "text_chunks", "chars_per_second", "max_gap", "stall_count",
              "output_tokens", "reasoning_tokens", "tokens_estimated", "visible_tokens_estimated",
              "truncated", "stop_reason", "requested_max_tokens", "status_code", "error_kind")
META_FIELDS = ("preset", "prompt_chars", "system", "max_tokens", "stream", "concurrency",
               "cache_mode", "temperature", "order", "scenario_key")
METRIC_FIELDS = ("n", "complete", "complete_rate", "complete_interval", "ttfa_p50", "ttfa_p90",
                 "ttfa_p95", "total_p50", "tps_p50", "chars_p50", "max_gap", "smooth_rate",
                 "good_rate", "bad", "unmeasured", "tail_ratio", "robust_cv")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _scoped_runs(runs, endpoint, models):
    allowed = set(models or [])
    clean, found = [], False
    for number, run in enumerate(runs):
        source = run.get("meta") or {}
        meta = {key: copy.deepcopy(source[key]) for key in META_FIELDS if key in source}
        meta["target_contexts"] = {}
        rows = []
        for original in run.get("results") or []:
            target = str(original.get("target") or "")
            effort = row_effort(original)
            base, sep, suffix = target.rpartition("@")
            name = base if sep and suffix in (effort, "default") else target
            if name != endpoint or allowed and original.get("model") not in allowed:
                continue
            found = True
            model = original.get("model")
            if not isinstance(model, str) or not re.fullmatch(r"[\w./:@+ -]{1,200}", model) or "://" in model or redact(model) != model:
                continue
            row = {key: copy.deepcopy(original[key]) for key in ROW_FIELDS if key in original}
            row["target"] = "public" + ("@" + effort if effort else "")
            row["effort"] = effort
            # Preserve protocol grouping without publishing internal target names.
            row["provider"] = original.get("provider") if original.get("provider") in ("openai", "responses", "anthropic", "gemini") else "unknown"
            context = (source.get("target_contexts") or {}).get(target + "/" + model)
            if context:
                meta["target_contexts"][row["target"] + "/" + model] = context
            rows.append(row)
        if rows:
            clean.append({"id": str(run.get("id", number)), "meta": meta, "results": rows})
    if runs and not found:
        raise ValueError("历史中没有匹配的端点/模型，未覆盖展示数据")
    return clean


def _project(view):
    variants = []
    for v in view["variants"]:
        variants.append({key: copy.deepcopy(v[key]) for key in
                         ("id", "model", "effort", "cohort", "condition", "assessment", "hourly",
                          "first_sample", "last_sample", "span_hours", "notes")})
        variants[-1]["metrics"] = {k: copy.deepcopy(v["metrics"][k]) for k in METRIC_FIELDS if k in v["metrics"]}
        variants[-1]["endpoint"] = "中转站实测"
        variants[-1]["errors"] = copy.deepcopy(v["errors"])  # Fixed categories/HTTP codes, never provider messages.
    return {"public": True, **{k: copy.deepcopy(view[k]) for k in
            ("version", "hours", "profile", "policy", "cohort", "sources", "limitations", "summary")},
            "cohorts": [{"id": c["id"], "label": c["label"], "settings": {"preset": c["settings"].get("preset") if c["settings"].get("preset") in ("short", "medium", "long", "long-input", "code", "json") else None}} for c in view["cohorts"]],
            "variants": variants}


def public_data(runs, endpoint, models=None, *, title="课题组模型表现", now=None):
    now = time.time() if now is None else now
    scoped = _scoped_runs(runs, endpoint, models)
    windows = {str(hours): {profile: _project(build_overview(scoped, hours=hours, profile=profile, cohort="all", now=now))
                           for profile in ("interactive", "reasoning")} for hours in WINDOWS}
    latest = max((v["last_sample"] for v in windows["168"]["interactive"]["variants"]), default=None)
    return {"format": "apitest-public-v1", "title": title, "timezone": "Asia/Shanghai",
            "last_sample": latest, "windows": windows}


def _assets(title):
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    css = re.search(r"<style>(.*?)</style>", source, re.S)[1]
    start = source.index('<section class="page" data-page="overview">')
    section = source[start:source.index("</section>", start) + len("</section>")]
    section = section.replace('class="page"', 'class="page active"', 1)
    section = section.replace('<h1>模型表现</h1>', '<h1>' + html.escape(title) + '</h1>')
    section = section.replace('<option value="all">全部记录</option>', '')
    # Public viewers choose a precomputed profile, not arbitrary assessment thresholds.
    section = re.sub(r'<details class="ov-policy">.*?</details>', '''<div class="field"><label>使用需求</label><select class="select" id="ov-profile"><option value="interactive">日常聊天</option><option value="reasoning">推理任务</option></select></div>
      <input id="ov-ttfa" type="hidden" value="3"><input id="ov-tps" type="hidden" value="30"><input id="ov-gap" type="hidden" value="1"><button id="ov-apply" hidden>应用</button>''', section, flags=re.S)
    section = section.replace('<input type="checkbox" id="ov-auto">', '<input type="checkbox" id="ov-auto" checked>')
    doc = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="中转站模型响应速度与输出流畅度实测"><title>''' + html.escape(title) + '''</title>
<link rel="icon" href="./assets/favicon.svg"><link rel="stylesheet" href="./assets/site.css">
<script defer src="./assets/chart.umd.min.js"></script><script defer src="./assets/public.js"></script><script defer src="./assets/overview.js"></script></head>
<body><header class="top"><div class="top-in"><a class="brand" href="#/overview">模型测速</a><div class="public-top"><span id="public-freshness">读取测试数据…</span><button class="btn btn-sm" id="public-theme">深色</button></div></div></header>
<main>''' + section + '''<footer class="muted" style="margin-top:24px;font-size:11px">本中转站的实测数据 · 时间均为北京时间 · 查看页面不会发起模型请求</footer></main></body></html>'''
    css += "\n.public-top{margin-left:auto;display:flex;gap:14px;align-items:center;font-size:11px;color:var(--text-2)}@media(max-width:600px){.top-in{height:auto;min-height:46px;flex-wrap:wrap;padding-top:8px;padding-bottom:8px}.public-top{flex-wrap:wrap;gap:8px}.ov-main-filters{width:100%}}\n"
    assets = {"LICENSE": (STATIC / "APITest.LICENSE.txt").read_text(encoding="utf-8"), "index.html": doc, "assets/site.css": css, "assets/public.js": (STATIC / "public.js").read_text(encoding="utf-8")}
    for filename in ("overview.js", "chart.umd.min.js", "Chart.js.LICENSE.md", "favicon.svg"):
        assets["assets/" + filename] = (STATIC / filename).read_text(encoding="utf-8")
    return assets


def export_site(out, runs, endpoint, models=None, *, title="课题组模型表现", now=None):
    out = Path(out).absolute()
    if not title.strip() or len(title) > 100:
        raise ValueError("展示标题需要1至100个字符")
    if any(p.is_symlink() for p in (out, *out.parents)):
        raise ValueError("展示目录不能包含符号链接")
    if out.exists() and not out.is_dir():
        raise ValueError("展示输出路径必须是目录")
    marker = out / MARKER
    if out.exists() and any(p.name != ".git" for p in out.iterdir()) and not marker.is_file():
        raise ValueError("输出目录不是本工具管理的展示目录，请使用空目录")
    if marker.exists() and marker.read_text(encoding="utf-8") != '{"format":"apitest-public-site-v1"}\n':
        raise ValueError("输出目录标识无效")
    for name in FILES:
        path = out / name
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("展示文件不能包含符号链接")
    data = public_data(runs, endpoint, models, title=title, now=now)
    digest = hashlib.sha256(_json(data).encode()).hexdigest()
    previous = None
    if (out / "data.json").exists():
        previous = json.loads((out / "data.json").read_text(encoding="utf-8"))
    data["generated_at"] = previous["generated_at"] if previous and previous.get("content_hash") == digest else int(time.time() if now is None else now)
    data["content_hash"] = digest
    manifest = {k: data[k] for k in ("format", "content_hash", "generated_at", "last_sample")}
    contents = {"manifest.json": _json(manifest) + "\n", MARKER: '{"format":"apitest-public-site-v1"}\n', ".nojekyll": "", "data.json": _json(data) + "\n", **_assets(title)}
    changed = []
    for name, content in contents.items():
        destination = out / name
        if not destination.exists() or destination.read_text(encoding="utf-8") != content:
            private_write(destination, content)
            changed.append(name)
    return {"directory": str(out), "changed_files": changed, "last_sample": data["last_sample"], "content_hash": digest}

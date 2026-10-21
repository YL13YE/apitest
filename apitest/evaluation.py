"""Read-only, scenario-separated evaluations of observed API user experience.

The sources justify the metrics/statistical method, not our adjustable UX cutoffs.
No LLM calls, prompt text, provider credentials or raw responses enter this view.
"""
from __future__ import annotations

import hashlib
import copy
import json
import math
import time
from collections import Counter, defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from .metrics import percentile
from .prompts import PRESETS
from .measurements import normalize_result

VERSION = "experience-v1"
PRESET_EFFORTS = ("low", "medium", "high", "xhigh")
SOURCES = [
    {"title": "Artificial Analysis：首正文与输出速度口径", "url": "https://artificialanalysis.ai/methodology"},
    {"title": "NVIDIA：Goodput 与体验约束", "url": "https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/perf_analyzer/genai-perf/docs/goodput.html"},
    {"title": "Google SRE：按用户目标定义 SLO", "url": "https://sre.google/workbook/implementing-slos/"},
    {"title": "NIST：Wilson 比例置信区间", "url": "https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm"},
    {"title": "SciPy：MAD 稳健波动与正态缩放", "url": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.median_abs_deviation.html"},
]
PROFILES = {
    "interactive": {"label": "日常聊天", "ttfa": 3.0, "tps": 30.0, "gap": 1.0},
    "reasoning": {"label": "允许等待的推理任务", "ttfa": 15.0, "tps": 20.0, "gap": 1.5},
}


def finite(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None


def wilson(successes, n):
    """Two-sided 95% Wilson interval; repeated samples may be correlated."""
    if n <= 0:
        return None
    z = 1.959963984540054
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [max(0.0, center - margin), min(1.0, center + margin)]


def scenario_metadata(opts, preset, targets=None):
    """Fingerprint the actual request content/settings without persisting prompts."""
    settings = {"prompt": opts.prompt, "system": opts.system, "max_tokens": opts.max_tokens,
                "temperature": opts.temperature, "stream": opts.stream, "concurrency": opts.concurrency,
                "cache_mode": opts.cache_mode, "order": "parallel" if opts.parallel_targets else opts.order}
    key = hashlib.sha256(json.dumps(settings, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    contexts = {}
    for target in targets or []:
        extra = copy.deepcopy(target.extra_body)
        if target.effort:
            extra.pop("reasoning_effort", None)
            for path in (("reasoning", "effort"), ("output_config", "effort"), ("generationConfig", "thinkingConfig", "thinkingLevel")):
                parent = extra
                for part in path[:-1]:
                    parent = parent.get(part, {}) if isinstance(parent, dict) else {}
                if isinstance(parent, dict):
                    parent.pop(path[-1], None)
        if extra:
            contexts[target.label] = hashlib.sha256(json.dumps(extra, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    pricing = {t.label: {"multiplier": t.price_multiplier, "currency": t.currency,
               "input": t.price_in, "output": t.price_out, "cache_read": t.price_cache_read,
               "cache_write": t.price_cache_write} for t in targets or []}
    return {"scenario_key": key, "target_contexts": contexts, "pricing": pricing, "preset": preset, "prompt_chars": len(opts.prompt), "system": bool(opts.system),
            "max_tokens": opts.max_tokens, "stream": opts.stream, "concurrency": opts.concurrency,
            "cache_mode": opts.cache_mode, "temperature": opts.temperature, "order": settings["order"]}


def scenario(meta, run_id):
    legacy = not bool(meta.get("scenario_key"))
    fields = {k: meta.get(k) for k in ("preset", "prompt_chars", "system", "max_tokens", "stream", "concurrency", "cache_mode", "temperature", "order")}
    if legacy and (fields["preset"] not in ("short", "medium", "long", "code", "long-input", "json") or fields["system"]):
        # Cannot establish that two custom historical prompts were the same.
        fields["unverified_run"] = run_id
    raw = meta.get("scenario_key") or json.dumps(fields, sort_keys=True)
    key = ("legacy-" if legacy else "signed-") + hashlib.sha256(raw.encode()).hexdigest()[:16]
    preset = PRESETS.get(fields["preset"])
    known_preset = False
    if preset and not fields["system"] and fields["prompt_chars"] == len(preset["prompt"]):
        expected = {"prompt": preset["prompt"], "system": None, **{k: fields[k] for k in
            ("max_tokens", "temperature", "stream", "concurrency", "cache_mode", "order")}}
        signature = hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        known_preset = legacy or meta.get("scenario_key") == signature
        if known_preset:
            key = "preset-" + hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()[:16]
    preset_labels = {"short": "短输出", "medium": "标准输出", "long": "长输出", "code": "代码", "long-input": "长上下文", "json": "JSON"}
    title = preset_labels.get(meta.get("preset"), "自定义任务")
    title += f" · 输出上限 {meta.get('max_tokens', '?')} · {'流式' if meta.get('stream') else '非流式'} · 并发 {meta.get('concurrency', '?')}"
    title += " · " + {"cold": "冷缓存", "warm": "固定前缀", "as-is": "原请求"}.get(meta.get("cache_mode"), "缓存条件未知")
    if legacy and not known_preset:
        title += " · 旧记录（参数信息不完整）"
    return key, {"id": key, "label": title, "legacy": legacy, "settings": {k: v for k, v in fields.items() if k != "unverified_run"}}


def badge(label, tone="neutral", detail=""):
    return {"label": label, "tone": tone, "detail": detail}


def row_effort(row):
    if row.get("effort"):
        effort = str(row["effort"])
        return None if effort == "default" else effort
    suffix = str(row.get("target") or "").rpartition("@")[2]
    return suffix if suffix in PRESET_EFFORTS else None


def median(values):
    return percentile(values, 50) if values else None


def measurable_output(row):
    elapsed = finite(row.get("gen_time"), True)
    return elapsed is not None and elapsed >= .05 and not row.get("single_chunk") and not row.get("output_limit_exceeded")


def metrics(rows, policy):
    complete = [r for r in rows if r.get("ok") and not r.get("truncated") and (r.get("text_chars", 0) or r.get("ttft_text", 0))]
    complete_ids = {id(r) for r in complete}
    latency = [v for r in complete if (v := finite(r.get("ttft_text"), True)) is not None]
    total = [v for r in complete if (v := finite(r.get("total"), True)) is not None]
    speed = [v for r in complete if measurable_output(r) and r.get("tps_eligible") and (v := finite(r.get("tps"), True)) is not None]
    chars = [v for r in complete if measurable_output(r) and (v := finite(r.get("chars_per_second"), True)) is not None]
    # A zero gap only means smooth output when multiple text chunks were observed.
    flow = [r for r in complete if r.get("stream") and measurable_output(r) and
            (r.get("tps_eligible") or finite(r.get("chars_per_second"), True) is not None) and finite(r.get("max_gap")) is not None]
    gaps = [float(r["max_gap"]) for r in flow]
    flow_ids = {id(r) for r in flow}
    good = bad = unknown = 0
    for r in rows:
        if id(r) not in complete_ids:
            bad += 1
            continue
        conditions = []
        ttfa = finite(r.get("ttft_text"), True)
        tps = finite(r.get("tps"), True) if r.get("tps_eligible") and measurable_output(r) else None
        gap = finite(r.get("max_gap")) if id(r) in flow_ids else None
        for value, passes in ((ttfa, ttfa is not None and ttfa <= policy["ttfa"]),
                              (tps, tps is not None and tps >= policy["tps"]),
                              (gap, gap is not None and gap <= policy["gap"])):
            if value is not None:
                conditions.append(passes)
        if False in conditions:
            bad += 1
        elif len(conditions) == 3:
            good += 1
        else:
            unknown += 1
    p50 = median(latency)
    p90 = percentile(latency, 90) if len(latency) >= 20 else None
    robust_cv = 1.4826 * median([abs(v - p50) for v in latency]) / p50 if len(latency) >= 20 and p50 else None
    return {"n": len(rows), "complete": len(complete), "complete_rate": len(complete) / len(rows) if rows else None,
            "complete_interval": wilson(len(complete), len(rows)), "latency_n": len(latency), "ttfa_p50": p50,
            "total_p50": median(total), "total_n": len(total),
            "ttfa_p90": p90, "ttfa_p95": percentile(latency, 95) if len(latency) >= 100 else None,
            "tps_p50": median(speed), "tps_n": len(speed), "chars_p50": median(chars),
            "gap_p50": median(gaps), "max_gap": max(gaps) if gaps else None, "flow_n": len(flow),
            "smooth_rate": sum(g <= policy["gap"] for g in gaps) / len(gaps) if gaps else None,
            "stall_count": sum(int(r.get("stall_count") or 0) for r in flow), "robust_cv": robust_cv,
            "tail_ratio": p90 / p50 if p90 is not None and p50 else None,
            "good": good, "bad": bad, "unmeasured": unknown, "good_rate": good / len(rows) if rows else None}


def assess(m, policy, span):
    n = m["n"]
    first = m["ttfa_p50"]
    if first is None:
        response = badge("未测首正文", detail="非流式或没有有效正文计时")
    elif first <= policy["ttfa"] / 2:
        response = badge("响应快", "good")
    elif first <= policy["ttfa"]:
        response = badge("响应较快", "good")
    elif first <= 2 * policy["ttfa"]:
        response = badge("等待稍长", "warn")
    else:
        response = badge("响应偏慢", "bad")
    speed = m["tps_p50"]
    output = badge("无法评估正文速度") if speed is None else (
        badge("输出快", "good") if speed >= policy["tps"] else badge("输出一般", "warn") if speed >= policy["tps"] / 2 else badge("输出偏慢", "bad"))
    smooth = m["smooth_rate"]
    fluidity = badge("未测流畅度") if smooth is None else (
        badge("流畅", "good") if smooth >= .95 else badge("基本流畅", "warn") if smooth >= .8 else badge("有卡顿", "bad"))
    stable_observation = m["tail_ratio"] is not None and m["tail_ratio"] <= 1.5 and m["robust_cv"] <= .25
    if n < 20 or m["tail_ratio"] is None or smooth is None:
        stability = badge("稳定性待观察", detail="至少20次且有流式测量，才描述波动；稳还需要更长时间的证据")
    elif m["complete_rate"] < .9 or m["tail_ratio"] > 3 or smooth < .8:
        stability = badge("不稳定", "bad", "本次记录存在较多失败、尾延迟或卡顿")
    elif n >= 100 and span >= 3600 and m["complete_interval"][0] >= .95 and stable_observation and smooth >= .95:
        stability = badge("稳", "good", "至少100次、跨度至少1小时；完整率Wilson下限≥95%，尾部与卡顿满足约束")
    elif m["complete_rate"] >= .95 and stable_observation and smooth >= .9:
        stability = badge("初步平稳", "good", "当前记录波动较小，尚不构成长时间可靠性保证")
    else:
        stability = badge("有波动", "warn")
    if n < 5:
        overall = badge("样本不足", detail="展示已测值；每组合至少5次才给综合体验评价")
    elif m["complete_rate"] < .9:
        overall = badge("不稳定", "bad", "完整响应不足90%；包含失败和截断")
    elif m["good_rate"] >= .9:
        overall = badge("好用" if n >= 20 else "初步好用", "good", "至少90%的尝试同时满足首正文、正文速度和停顿约束")
    elif m["good_rate"] >= .7:
        overall = badge("可用", "warn", "至少70%的尝试满足体验约束")
    elif m["unmeasured"] / n > .3:
        overall = badge("测量不全", detail="单块、非流式或缺少用量；不能把缺失值当成快或流畅")
    else:
        overall = badge("偏慢 / 卡顿", "bad", "超过30%的请求未达到所选体验目标")
    evidence = "样本不足" if n < 5 else "初步观察" if n < 20 else "可参考" if n < 100 else "较多样本"
    return {"overall": overall, "response": response, "output": output, "fluidity": fluidity, "stability": stability,
            "evidence": badge(evidence), "preliminary": n < 20}


def build_overview(runs, *, hours=24, profile="interactive", cohort=None, now=None, thresholds=None):
    if profile not in PROFILES:
        raise ValueError("Unknown evaluation profile")
    now = time.time() if now is None else now
    policy = dict(PROFILES[profile])
    for key, value in (thresholds or {}).items():
        value = finite(value, True)
        if key not in ("ttfa", "tps", "gap") or value is None or value > 10000:
            raise ValueError("Invalid experience threshold")
        policy[key] = value
    if hours is not None and (finite(hours, True) is None or hours > 24 * 365):
        raise ValueError("Invalid time window")
    since = now - hours * 3600 if hours is not None else 0
    scenarios, records, seen, run_ids = {}, [], set(), set()
    for run in runs:
        meta, rid = run.get("meta") or {}, str(run.get("id", "unknown"))
        for position, original in enumerate(run.get("results") or []):
            if row_effort(original) not in (None, *PRESET_EFFORTS):
                continue
            context = (meta.get("target_contexts") or {}).get(str(original.get("target")) + "/" + str(original.get("model")))
            row_meta = meta
            if context:
                row_meta = dict(meta, scenario_key=str(meta.get("scenario_key", rid)) + ":" + context)
            sid, info = scenario(row_meta, rid)
            if context:
                info["label"] += " · 高级参数 " + context[:6]
            stamp = finite(original.get("started_at"), True)
            if stamp is None or stamp < since or stamp > now:
                continue
            unique = (rid, original.get("target"), original.get("model"), original.get("index", position), original.get("attempt", 1), bool(original.get("warmup")))
            if unique in seen:
                continue
            seen.add(unique)
            row = normalize_result(original, meta)
            row["stream"] = row.get("stream", meta.get("stream", False))
            row["truncated"] = bool(row.get("truncated") or row.get("stop_reason") in ("length", "max_tokens", "max_output_tokens"))
            row["_time"], row["_cohort"] = stamp, sid
            row["currency"] = row.get("currency") or (meta.get("budget") or {}).get("currency") or "USD"
            records.append(row)
            scenarios.setdefault(sid, dict(info, latest=stamp, requests=0))
            scenarios[sid]["legacy"] |= info["legacy"]
            scenarios[sid]["latest"] = max(scenarios[sid]["latest"], stamp)
            scenarios[sid]["requests"] += 1
            run_ids.add(rid)
    cohorts = sorted(scenarios.values(), key=lambda c: c["latest"], reverse=True)
    labels = Counter(c["label"] for c in cohorts)
    numbered = Counter()
    for item in cohorts:
        if labels[item["label"]] > 1:
            numbered[item["label"]] += 1
            item["label"] += " · 条件 " + str(numbered[item["label"]])
    selected = (cohort if cohort in scenarios else "all") if cohorts else None
    groups = defaultdict(list)
    for row in records:
        if selected != "all" and row["_cohort"] != selected:
            continue
        target = str(row.get("target") or "unknown")
        base, sep, suffix = target.rpartition("@")
        effort = row_effort(row)
        endpoint = base if sep and (suffix == effort or (suffix == "default" and effort is None)) else target
        key = (row["_cohort"], endpoint, str(row.get("model") or "unknown"), effort, str(row.get("provider") or "unknown"), str(row["currency"]))
        groups[key].append(row)
    variants = []
    for (sid, endpoint, model, effort, provider, currency), all_rows in groups.items():
        rows = [r for r in all_rows if not r.get("warmup")]
        if not rows:
            continue
        m = metrics(rows, policy)
        span = max(r["_time"] for r in rows) - min(r["_time"] for r in rows)
        hours_map = defaultdict(list)
        for r in rows:
            hour = datetime.fromtimestamp(r["_time"], ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:00")
            hours_map[hour].append(r)
        hourly = []
        for hour, hr in sorted(hours_map.items()):
            hm = metrics(hr, policy)
            hourly.append({"hour": hour, "n": hm["n"], "ttfa": hm["ttfa_p50"], "tps": hm["tps_p50"], "complete_rate": hm["complete_rate"], "good_rate": hm["good_rate"]})
        missing_cost = any(r.get("cost_unknown_reason") == "price_missing" or (r.get("cost") is None and not r.get("budget_charge")) for r in all_rows)
        cost = None if missing_cost else sum(float(r.get("budget_charge", r.get("cost")) or 0) for r in all_rows)
        error_labels = {"timeout": "请求超时", "connect": "连接失败", "empty": "无有效正文", "stream": "流式错误", "cancelled": "已取消", "other": "请求异常", "incomplete": "未完整结束"}
        errors = Counter(("HTTP " + str(r["status_code"]) if r.get("status_code", 0) >= 300 else error_labels.get(r.get("error_kind"), "未完整结束")) for r in rows if not r.get("ok"))
        notes = []
        if m["n"] < 20:
            notes.append("样本较少，评价仅作观察，不代表长期表现。")
        if scenarios[sid].get("legacy"):
            notes.append("包含旧记录，按能核对的内置预设和请求参数统计。")
        exceeded = sum(bool(r.get("output_limit_exceeded")) for r in rows)
        if exceeded:
            notes.append(f"{exceeded}次接口报告输出超过请求上限，不参与输出速度评价。")
        if any(r.get("single_chunk") for r in rows):
            notes.append("含单块返回，不能据此判定流畅。")
        if not any(r.get("stream") for r in rows):
            notes.append("非流式请求只能比较完整耗时，不能推断首正文等待或流畅度。")
        if any(r.get("stream") and not measurable_output(r) for r in rows):
            notes.append("正文输出观察时段不足50ms或时段缺失，不据此评价速度和流畅度。")
        if any(r.get("truncated") for r in rows):
            notes.append("截断不算完整响应，也不参与正文速度评价。")
        if sum(bool(r.get("tps_eligible")) for r in rows) < len(rows):
            notes.append("正文速度仅使用可核对用量的有效流式记录。")
        sizes = [finite(r.get("output_tokens"), True) for r in rows if r.get("ok")]
        sizes = [v for v in sizes if v is not None]
        if sizes and median(sizes) < 128:
            notes.append("输出较短，吞吐估计可能受分块与固定开销影响。")
        ident = hashlib.sha256(json.dumps((sid, endpoint, model, effort, provider, currency)).encode()).hexdigest()[:20]
        variants.append({"id": ident, "endpoint": endpoint, "model": model, "effort": effort, "provider": provider,
                         "cohort": sid, "condition": scenarios[sid]["label"],
                         "currency": currency, "metrics": m, "assessment": assess(m, policy, span), "hourly": hourly,
                         "first_sample": min(r["_time"] for r in rows), "last_sample": max(r["_time"] for r in rows),
                         "span_hours": span / 3600, "cost": cost, "cost_uncertain": sum(bool(r.get("cost_uncertain")) for r in all_rows),
                         "charged_attempts": len(all_rows), "errors": dict(errors), "notes": notes})
    variants.sort(key=lambda v: (v["assessment"]["overall"]["tone"] != "good", -v["metrics"]["n"], v["model"], v["effort"] or ""))
    return {"version": VERSION, "generated_at": now, "hours": hours, "profile": profile, "policy": policy,
            "cohort": selected, "cohorts": cohorts, "variants": variants, "sources": SOURCES,
            "summary": {"variants": len(variants), "models": len({v["model"] for v in variants}),
                        "requests": sum(v["metrics"]["n"] for v in variants), "runs": len(run_ids),
                        "good": sum(v["assessment"]["overall"]["tone"] == "good" for v in variants),
                        "sufficient_samples": sum(v["metrics"]["n"] >= 20 for v in variants)},
            "limitations": ["评价针对接口体验，不评价模型智力、答案正确性或模型身份。",
                            "阈值是本工具可调整的体验策略，不是引用来源颁布的统一标准。",
                            "Wilson区间为独立同分布假设下的近似参考；连续采样可能相关，不构成SLA保证。",
                            "原生token口径不同，跨模型请结合字符速度；单块输出和缺失测量不会当成流畅。",
                            "没有采样的时段不视为可用或故障；所示比例不是全年可用率。",
                            "不同场景、协议、强度分别统计；当前窗口仅代表已有记录。"]}

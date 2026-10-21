"""Shared validity rules for live rows, saved rows and output-speed statistics."""
from __future__ import annotations

import math

MIN_OUTPUT_WINDOW = .05
ISSUES = {
    "request_failed": "请求未成功完成", "non_streaming": "非流式，无法测量正文输出速度",
    "single_chunk": "正文一次或单块返回，无法测量输出速度",
    "short_window": "正文分块集中在50毫秒内返回，无法可靠测量输出速度",
    "truncated": "输出被截断", "estimated_usage": "缺少可核对的 token 用量",
    "reasoning_usage": "无法区分正文与思考用量", "output_limit": "接口报告的输出 token 超过请求上限",
    "invalid_usage": "接口报告的 token 用量不一致", "unavailable": "缺少有效的输出计时",
}


def output_limit_exceeded(row, limit=None):
    limit = row.get("requested_max_tokens") or limit
    output = row.get("output_tokens")
    return bool(not row.get("tokens_estimated") and isinstance(limit, (int, float)) and limit > 0
                and isinstance(output, (int, float)) and output > limit)


def throughput_issue(row):
    if not row.get("ok"):
        return "request_failed"
    if not row.get("stream", True):
        return "non_streaming"
    if row.get("single_chunk") or row.get("text_chunks") is not None and row["text_chunks"] < 2:
        return "single_chunk"
    if row.get("output_limit_exceeded"):
        return "output_limit"
    if row.get("truncated"):
        return "truncated"
    elapsed = row.get("gen_time")
    if not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed) or elapsed <= 0:
        return "unavailable"
    if elapsed < MIN_OUTPUT_WINDOW:
        return "short_window"
    if row.get("tokens_estimated"):
        return "estimated_usage"
    if row.get("visible_tokens_estimated"):
        return "reasoning_usage"
    if (row.get("reasoning_tokens") or 0) > (row.get("output_tokens") or 0):
        return "invalid_usage"
    if row.get("tps_eligible") is False:
        return row.get("tps_issue") or "unavailable"
    return None


def normalize_result(row, meta=None):
    result = dict(row)
    result["stream"] = result.get("stream", (meta or {}).get("stream", True))
    result["truncated"] = bool(result.get("truncated") or result.get("stop_reason") in ("length", "max_tokens", "max_output_tokens"))
    if not result.get("requested_max_tokens"):
        result["requested_max_tokens"] = (meta or {}).get("max_tokens")
    result["output_limit_exceeded"] = output_limit_exceeded(result)
    issue = throughput_issue(result)
    result["tps_eligible"] = issue is None
    result["tps_issue"] = issue
    result["tps_note"] = ISSUES.get(issue, "")
    if issue:
        result["tps"] = None
    if issue in ("non_streaming", "single_chunk", "short_window", "output_limit"):
        result["chars_per_second"] = None
    for key in ("gen_time", "tps", "chars_per_second"):
        if isinstance(result.get(key), float) and not math.isfinite(result[key]):
            result[key] = None
    return result

"""Daily monitoring windows in Beijing time; all boundaries are half-open."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

TIMEZONE = ZoneInfo("Asia/Shanghai")


def validate_hours(hours):
    if hours is None:
        return
    if not isinstance(hours, list) or not hours or any(isinstance(h, bool) or not isinstance(h, int) or h < 0 or h > 23 for h in hours):
        raise ValueError("请至少选择一个监控小时，范围为0至23")
    if len(hours) != len(set(hours)):
        raise ValueError("监控时段不能重复")


def is_active(timestamp, hours):
    return hours is None or datetime.fromtimestamp(timestamp, TIMEZONE).hour in hours


def next_active(timestamp, hours):
    if is_active(timestamp, hours):
        return timestamp
    local = datetime.fromtimestamp(timestamp, TIMEZONE).replace(minute=0, second=0, microsecond=0)
    for offset in range(1, 25):
        candidate = local + timedelta(hours=offset)
        if candidate.hour in hours:
            return candidate.timestamp()
    raise ValueError("没有可用监控时段")


def sample_round_bound(start, duration, interval, hours):
    """Include partial first/last windows; count each continuous active window."""
    validate_hours(hours)
    if not all(math.isfinite(value) for value in (start, duration, interval)) or not 0 <= start <= 1e10 or not 0 < duration <= 7 * 86400 or interval <= 0:
        raise ValueError("无效的采样计划")
    end, at, segments = start + duration, start, []
    while at < end:
        boundary = min((math.floor(at / 3600) + 1) * 3600, end)
        if is_active(at, hours):
            if segments and segments[-1][1] == at:
                segments[-1][1] = boundary
            else:
                segments.append([at, boundary])
        at = boundary
    return sum(math.ceil((right - left) / interval) for left, right in segments)

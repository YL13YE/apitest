"""Shared attempt accounting. Unknown charges retain their reservation."""
from __future__ import annotations

import asyncio
import json
import math
import time

from .providers import build_body
from .scheduling import validate_hours


class RunControl:
    def __init__(self, targets, opts):
        self.opts = opts
        self.lock = asyncio.Lock()
        self.started = time.monotonic()
        self.attempts = 0
        self.spent = 0.0
        self.reported_cost = 0.0
        self.uncertain_cost = 0.0
        self.reserved = 0.0
        self.unknown = 0
        self.unknown_reasons = {}
        self.hour_spent = {}
        self.hour_reserved = {}
        self.stop_reason = ""
        self.phase = "sampling"
        self.next_sample_at = None
        self.bounded = opts.budget is not None or opts.hourly_budget is not None
        currencies = {t.currency for t in targets}
        if len(currencies) != 1:
            raise ValueError("一次任务只能使用同一币种，请先统一中转站价格")
        self.currency = next(iter(currencies), "USD")
        self.reservations = {}
        self.priced_labels = set()
        self.price_rates = {}
        for t in targets:
            body = build_body(t, opts.prompt, opts.system, opts.max_tokens, opts.temperature, opts.stream)
            # No tokenizer-dependent guess is presented as exact. A UTF-8 byte bound
            # includes wrappers/overrides and is intentionally conservative.
            input_bound = len(json.dumps(body, ensure_ascii=False).encode("utf-8")) + 1024
            observed = opts.usage_bounds.get(t.label, {})
            input_bound = max(input_bound, observed.get("input", 0))
            output_bound = max(opts.max_tokens, observed.get("output", 0))
            if self.bounded and (t.price_in is None or t.price_out is None):
                raise ValueError("启用预算必须为每个模型填写输入和输出价格")
            if t.price_in is not None and t.price_out is not None:
                self.priced_labels.add(t.label)
                rates = [t.price_in, t.price_out] + [x for x in (t.price_cache_read, t.price_cache_write) if x is not None]
                if any(isinstance(x, bool) or not math.isfinite(float(x)) or float(x) < 0 for x in rates):
                    raise ValueError("价格必须是有限非负数字")
                input_rate = max(float(x) for x in (t.price_in, t.price_cache_read, t.price_cache_write) if x is not None)
                input_rate *= t.price_multiplier
                output_rate = float(t.price_out) * t.price_multiplier
                amount = (input_bound * input_rate + output_bound * output_rate) / 1e6
                if not all(math.isfinite(value) for value in (input_rate, output_rate, amount)):
                    raise ValueError("倍率计算后的费用超出有效范围")
                self.price_rates[t.label] = (input_rate, output_rate)
                self.reservations[t.label] = amount
            else:
                self.reservations[t.label] = 0.0

    def hour(self):
        return int((time.monotonic() - self.started) // 3600)

    async def reserve(self, target):
        async with self.lock:
            hour = self.hour()
            amount = self.reservations[target.label]
            if self.opts.max_requests is not None and self.attempts >= self.opts.max_requests:
                self.stop_reason = "max_requests"
                return None
            if self.opts.budget is not None and self.spent + self.reserved + amount > self.opts.budget + 1e-12:
                self.stop_reason = "budget"
                return None
            used = self.hour_spent.get(hour, 0) + self.hour_reserved.get(hour, 0)
            if self.opts.hourly_budget is not None and used + amount > self.opts.hourly_budget + 1e-12:
                self.stop_reason = "hourly_budget"
                return None
            self.attempts += 1
            if self.stop_reason == "hourly_budget":
                self.stop_reason = ""
            self.reserved += amount
            self.hour_reserved[hour] = self.hour_reserved.get(hour, 0) + amount
            return (hour, amount, target.label)

    async def settle(self, ticket, result):
        hour, reserved, label = ticket
        async with self.lock:
            self.reserved = max(0, self.reserved - reserved)
            self.hour_reserved[hour] = max(0, self.hour_reserved.get(hour, 0) - reserved)
            unknown = result.cost is None or result.tokens_estimated or not result.ok
            charge = max(reserved, result.cost or 0) if unknown else result.cost
            result.budget_charge = charge
            result.cost_uncertain = unknown
            reason = "price_missing" if label not in self.priced_labels else ("request_failed" if not result.ok else ("usage_missing" if result.tokens_estimated or result.cost is None else ""))
            result.cost_unknown_reason = reason
            self.spent += charge
            if unknown:
                self.uncertain_cost += charge
            else:
                self.reported_cost += charge
            self.hour_spent[hour] = self.hour_spent.get(hour, 0) + charge
            if label in self.price_rates and not result.tokens_estimated:
                pin, pout = self.price_rates[label]
                observed = (max(0, result.input_tokens) * pin + max(self.opts.max_tokens, result.output_tokens) * pout) / 1e6
                self.reservations[label] = max(self.reservations[label], observed)
            if unknown:
                self.unknown += 1
                self.unknown_reasons[reason] = self.unknown_reasons.get(reason, 0) + 1
            if self.opts.budget is not None and self.spent > self.opts.budget:
                self.stop_reason = "reported_cost_exceeded_reservation"
            if self.opts.max_requests is not None and self.attempts >= self.opts.max_requests and not self.stop_reason:
                self.stop_reason = "max_requests"

    def snapshot(self):
        pricing_complete = len(self.priced_labels) == len(self.reservations)
        return {"attempts": self.attempts, "accounted_cost": round(self.spent, 8) if pricing_complete else None, "known_or_reserved_cost": round(self.spent, 8), "reported_cost": round(self.reported_cost, 8), "uncertain_cost": round(self.uncertain_cost, 8), "pricing_complete": pricing_complete, "cost_status": "unpriced" if not pricing_complete else ("reserved" if self.unknown else "estimated"), "unknown_reasons": dict(self.unknown_reasons), "reserved_cost": round(self.reserved, 8), "uncertain_attempts": self.unknown, "currency": self.currency, "budget": self.opts.budget, "hourly_budget": self.opts.hourly_budget, "max_requests": self.opts.max_requests, "stop_reason": self.stop_reason, "mode": "sampling" if self.opts.sample_interval else ("duration" if self.opts.duration else "rounds"), "rounds_per_target": None if self.opts.duration else self.opts.rounds, "duration_seconds": self.opts.duration, "sample_interval": self.opts.sample_interval}


def validate_options(opts, *, require_cost_limit=True):
    validate_hours(opts.monitor_hours)
    if opts.monitor_hours is not None and not opts.sample_interval:
        raise ValueError("监控时段仅用于定时采样任务")
    for name in ("max_tokens", "rounds", "concurrency"):
        value = getattr(opts, name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(name + " 必须是正整数")
    for name in ("timeout", "interval", "sample_interval"):
        value = getattr(opts, name)
        if not math.isfinite(value) or value < 0 or (name == "timeout" and value == 0):
            raise ValueError(name + " 必须是有效非负数，超时须大于0")
    for name in ("duration", "budget", "hourly_budget"):
        value = getattr(opts, name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise ValueError(name + " 必须是有限正数")
    if opts.max_requests is not None and (not isinstance(opts.max_requests, int) or opts.max_requests <= 0):
        raise ValueError("max_requests 必须是正整数")
    if opts.warmup < 0 or opts.retries < 0:
        raise ValueError("预热与重试不能为负数")
    if opts.order not in ("interleaved", "sequential", "parallel"):
        raise ValueError("未知执行顺序")
    if opts.cache_mode not in ("cold", "warm", "as-is"):
        raise ValueError("未知缓存模式")
    if opts.sample_interval and (not opts.duration or opts.concurrency != 1 or opts.parallel_targets or opts.order != "interleaved"):
        raise ValueError("定点采样需要持续时间、并发1及交替轮询")
    if opts.duration and opts.duration >= 3600 and (not opts.sample_interval or (require_cost_limit and opts.max_requests is None and opts.budget is None and opts.hourly_budget is None)):
        raise ValueError("小时级测试必须设置采样间隔，并设置金额预算或最大请求次数")

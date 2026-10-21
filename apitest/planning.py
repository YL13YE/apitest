"""No-network, conservative cost previews using the runtime reservation policy."""
from __future__ import annotations

import math
from dataclasses import replace

from .budget import RunControl
from .scheduling import sample_round_bound


def observed_usage_bounds(runs, targets, opts, preset):
    """Use only comparable requests; share conservative maxima across a model's efforts."""
    from .evaluation import scenario, scenario_metadata, row_effort
    meta = scenario_metadata(opts, preset, targets)

    def condition(info, run_id, label):
        context = (info.get("target_contexts") or {}).get(label)
        if context:
            info = dict(info, scenario_key=str(info.get("scenario_key", run_id)) + ":" + context)
        return scenario(info, run_id)[0]

    def family(name, model, provider, effort):
        base, sep, suffix = name.rpartition("@")
        return (base if sep and suffix in (effort, "default") else name, model, provider)

    expected, target_families = {}, {}
    for target in targets:
        key = family(target.name, target.model, target.provider, target.effort)
        target_families[target.label] = key
        expected.setdefault(key, set()).add(condition(meta, "preview", target.label))
    maxima, count = {}, 0
    for run in runs:
        for row in run.get("results") or []:
            if row.get("tokens_estimated"):
                continue
            name, model, provider = str(row.get("target") or ""), str(row.get("model") or ""), str(row.get("provider") or "")
            key = family(name, model, provider, row_effort(row))
            if key not in expected or condition(run.get("meta") or {}, str(run.get("id")), name + "/" + model) not in expected[key]:
                continue
            nin, nout = row.get("input_tokens"), row.get("output_tokens")
            if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in (nin, nout)) or not (nin or nout):
                continue
            old = maxima.setdefault(key, {"input": 0, "output": 0})
            old["input"], old["output"] = max(old["input"], nin), max(old["output"], nout)
            count += 1
    return {label: dict(maxima[key]) for label, key in target_families.items() if key in maxima}, count


def monitor_estimate(targets, opts, start, *, usage_bounds=None, observed_samples=0):
    rounds = sample_round_bound(start, opts.duration, opts.sample_interval, opts.monitor_hours)
    attempts = rounds * (opts.retries + 1) + opts.warmup if rounds else 0
    # The preview describes the full observation before applying the user's budget.
    control = RunControl(targets, replace(opts, budget=None, hourly_budget=None, usage_bounds=usage_bounds or {}))
    missing = len(targets) - len(control.priced_labels)
    cost = None if missing else sum(control.reservations.values()) * attempts
    if cost is not None and not math.isfinite(cost):
        raise ValueError("费用估算超出有效范围，请检查价格")
    return {"rounds": rounds, "requests": attempts * len(targets), "cost": cost,
            "currency": control.currency, "missing_prices": missing,
            "max_tokens": opts.max_tokens, "observed_samples": observed_samples,
            "method": "observed_usage" if observed_samples else "configured_tokens"}

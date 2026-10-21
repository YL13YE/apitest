"""Validate user-supplied per-million prices and price reported token usage."""
from __future__ import annotations

import math
import copy


def validate_price_multiplier(value=1):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("计费倍率必须是有限非负数字") from None
    if isinstance(value, bool) or not math.isfinite(result) or result < 0:
        raise ValueError("计费倍率必须是有限非负数字")
    return result


def apply_price_catalogs(endpoints, catalogs):
    """Defaults stay separate from overrides and are bound to the imported destination."""
    endpoints = copy.deepcopy(endpoints)
    for name, ep in endpoints.items():
        ep.pop("price_defaults", None)
        catalog = (catalogs or {}).get(name)
        if not catalog:
            continue
        destination = (ep.get("base_url", ep.get("url", "")), ep.get("provider", ""))
        if destination != (catalog.get("base_url"), catalog.get("provider", "")):
            continue
        currency = str(ep.get("currency") or catalog["currency"]).upper()
        if currency != catalog["currency"]:
            continue
        ep["currency"] = currency
        ep["price_defaults"] = copy.deepcopy(catalog["price"])
    return endpoints


def parse_price_table(table):
    if not isinstance(table, dict) or table.get("version") != 1 or not isinstance(table.get("models"), list):
        raise ValueError("定价表需要 version: 1 和 models 列表")
    prices = {}
    fields = {"input": "inputCostPerMillion", "output": "outputCostPerMillion",
              "cache_read": "cacheReadCostPerMillion", "cache_write": "cacheCreationCostPerMillion"}
    for row in table["models"]:
        if not isinstance(row, dict) or not isinstance(row.get("modelId"), str) or not row["modelId"].strip():
            raise ValueError("模型 ID 不能为空")
        model = row["modelId"].strip()
        if model in prices:
            raise ValueError("定价表包含重复模型 ID: " + model)
        price = {}
        for key, field in fields.items():
            raw = row.get(field)
            if raw is None and key.startswith("cache_"):
                continue
            try:
                value = float(raw)
            except (ValueError, TypeError):
                raise ValueError(model + " 的价格必须是有限非负数字") from None
            if isinstance(raw, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(model + " 的价格必须是有限非负数字")
            price[key] = value
        prices[model] = price
    if not prices:
        raise ValueError("定价表不能为空")
    return prices


def usage_cost(target, input_tokens, output_tokens, cache_read=0, cache_write=0):
    """Input count includes cached tokens. Missing cache rates keep cost unknown."""
    if any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for n in (input_tokens, output_tokens, cache_read, cache_write)):
        return None
    if cache_read + cache_write > input_tokens:
        return None
    parts = [(input_tokens - cache_read - cache_write, target.price_in),
             (output_tokens, target.price_out), (cache_read, target.price_cache_read),
             (cache_write, target.price_cache_write)]
    if target.price_in is None or target.price_out is None:
        return None
    if any(rate is None or not math.isfinite(float(rate)) or float(rate) < 0 for count, rate in parts if count):
        return None
    cost = sum(count * float(rate) for count, rate in parts if count) / 1e6 * target.price_multiplier
    return cost if math.isfinite(cost) else None

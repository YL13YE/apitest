"""配置文件加载：apitest.yaml / ~/.config/apitest/config.yaml，支持 ${ENV} 展开。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .providers import PROVIDERS, Target, detect_provider

CONFIG_CANDIDATES = [
    Path("apitest.yaml"),
    Path("apitest.yml"),
    Path.home() / ".config" / "apitest" / "config.yaml",
    Path.home() / ".apitest" / "config.yaml",
]

EXAMPLE_CONFIG = """# apitest 配置文件
# api_key 支持 ${ENV_VAR} 形式引用环境变量

defaults:
  rounds: 3          # 每个模型请求次数
  concurrency: 1     # 并发数
  max_tokens: 512
  timeout: 120
  # prompt_preset: medium   # short / medium / long / code / long-input
  # system: 你是一个乐于助人的助手。

endpoints:
  openai:
    provider: openai              # openai / anthropic / gemini（省略则自动识别）
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}
    models: [gpt-4o-mini, gpt-4o]
    price:                        # 可选：USD / 1M tokens [输入, 输出]，用于估算费用
      gpt-4o-mini: [0.15, 0.60]
      gpt-4o: [2.50, 10.00]

  anthropic:
    provider: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-5, claude-haiku-4-5]

  gemini:
    provider: gemini
    base_url: https://generativelanguage.googleapis.com
    api_key: ${GEMINI_API_KEY}
    models: [gemini-2.5-flash]

  # 中转 / 代理站示例（OpenAI 兼容）
  # relay:
  #   base_url: https://api.example.com/v1
  #   api_key: sk-xxx
  #   models: [gpt-4o-mini, deepseek-chat]
  #   headers:                    # 可选：附加请求头
  #     X-Custom: value
  #   extra_body:                 # 可选：合并进请求体
  #     temperature: 0.7
"""

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any) -> Any:
    if isinstance(value, str):
        def repl(m):
            return os.environ.get(m.group(1), m.group(2) if m.group(2) is not None else "")
        return _ENV_RE.sub(repl, value)
    if isinstance(value, dict):
        return {k: expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env(v) for v in value]
    return value


def find_config(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {p}")
        return p
    for c in CONFIG_CANDIDATES:
        if c.exists():
            return c
    return None


def load_config(path: Optional[Path]) -> Dict[str, Any]:
    if path is None:
        return {"defaults": {}, "endpoints": {}}
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data = expand_env(data)
    data.setdefault("defaults", {})
    data.setdefault("endpoints", {})
    return data


def _split_models(spec: Any) -> List[str]:
    if spec is None:
        return []
    if isinstance(spec, str):
        return [m.strip() for m in spec.split(",") if m.strip()]
    return [str(m).strip() for m in spec if str(m).strip()]


def endpoint_targets(name: str, ep: Dict[str, Any], models: Optional[List[str]] = None) -> List[Target]:
    base_url = str(ep.get("base_url") or ep.get("url") or "")
    api_key = str(ep.get("api_key") or ep.get("key") or "")
    provider = str(ep.get("provider") or detect_provider(base_url, api_key)).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"端点 {name} 的 provider 非法: {provider}（可选 {', '.join(PROVIDERS)}）")
    model_list = models or _split_models(ep.get("models") or ep.get("model"))
    if not model_list:
        raise ValueError(f"端点 {name} 未配置 models")
    prices = ep.get("price") or {}
    out = []
    for m in model_list:
        p = prices.get(m) if isinstance(prices, dict) else None
        pin = pout = None
        if isinstance(p, (list, tuple)) and len(p) >= 2:
            pin, pout = float(p[0]), float(p[1])
        elif isinstance(p, dict):
            pin, pout = p.get("input"), p.get("output")
        out.append(
            Target(
                name=name,
                provider=provider,
                base_url=base_url,
                api_key=api_key,
                model=m,
                headers=dict(ep.get("headers") or {}),
                extra_body=dict(ep.get("extra_body") or {}),
                price_in=pin,
                price_out=pout,
            )
        )
    return out


def resolve_targets(
    cfg: Dict[str, Any],
    endpoint_specs: List[str],
    url: Optional[str],
    key: Optional[str],
    models: Optional[str],
    provider: Optional[str],
    use_all: bool,
    headers: Optional[Dict[str, str]] = None,
    extra_body: Optional[Dict[str, Any]] = None,
    name: Optional[str] = None,
) -> List[Target]:
    targets: List[Target] = []
    endpoints: Dict[str, Any] = cfg.get("endpoints") or {}

    if url:
        api_key = key or os.environ.get("APITEST_API_KEY") or ""
        prov = (provider or "auto").lower()
        if prov == "auto":
            prov = detect_provider(url, api_key)
        if not api_key:
            api_key = os.environ.get({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "gemini": "GEMINI_API_KEY"}[prov], "")
        model_list = _split_models(models)
        if not model_list:
            raise ValueError("使用 --url 时必须通过 -m/--model 指定模型（可逗号分隔多个）")
        ep = {"provider": prov, "base_url": url, "api_key": api_key, "models": model_list, "headers": headers or {}, "extra_body": extra_body or {}}
        targets.extend(endpoint_targets(name or _name_from_url(url), ep))

    if use_all:
        for n, ep in endpoints.items():
            targets.extend(endpoint_targets(n, ep, _split_models(models) or None))

    for spec in endpoint_specs:
        n, _, ms = spec.partition(":")
        n = n.strip()
        if n not in endpoints:
            avail = ", ".join(endpoints) or "（无，请先 apitest init 创建配置）"
            raise ValueError(f"配置中没有端点 '{n}'。可用: {avail}")
        ep = dict(endpoints[n])
        if headers:
            ep["headers"] = {**(ep.get("headers") or {}), **headers}
        if extra_body:
            ep["extra_body"] = {**(ep.get("extra_body") or {}), **extra_body}
        targets.extend(endpoint_targets(n, ep, _split_models(ms) or _split_models(models) or None))

    # 去重
    seen, uniq = set(), []
    for t in targets:
        if t.label not in seen:
            seen.add(t.label)
            uniq.append(t)
    return uniq


def _name_from_url(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url)
    hostport = m.group(1) if m else url
    host = hostport.split(":")[0]
    if host == "localhost" or re.match(r"^\d+\.\d+\.\d+\.\d+$", host):
        return hostport
    parts = host.split(".")
    return parts[-2] if len(parts) >= 2 else host

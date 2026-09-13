"""统计：分位数、均值、标准差、迷你图、按目标汇总。"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Sequence

from .runner import RequestResult

SPARK = "▁▂▃▄▅▆▇█"


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def sparkline(values: Sequence[float], width: int = 24) -> str:
    if not values:
        return ""
    vals = list(values)[-width:]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7.999))] for v in vals)


@dataclass
class Stats:
    n: int = 0
    min: float = 0.0
    mean: float = 0.0
    p50: float = 0.0
    p90: float = 0.0
    p99: float = 0.0
    max: float = 0.0
    std: float = 0.0

    @classmethod
    def of(cls, values: Sequence[float]) -> "Stats":
        if not values:
            return cls()
        return cls(
            n=len(values),
            min=min(values),
            mean=mean(values),
            p50=percentile(values, 50),
            p90=percentile(values, 90),
            p99=percentile(values, 99),
            max=max(values),
            std=stdev(values),
        )

    @property
    def cv(self) -> float:
        """变异系数（std/mean），越小越稳定。"""
        return self.std / self.mean if self.mean > 0 else 0.0


@dataclass
class Summary:
    target: str
    model: str
    provider: str
    n: int = 0
    ok: int = 0
    fail: int = 0
    success_rate: float = 0.0
    ttfb: Stats = field(default_factory=Stats)
    ttft: Stats = field(default_factory=Stats)
    ttft_text: Stats = field(default_factory=Stats)  # 首个可见文本 token
    total: Stats = field(default_factory=Stats)
    tps: Stats = field(default_factory=Stats)
    out_tokens: Stats = field(default_factory=Stats)
    in_tokens_mean: float = 0.0
    reasoning_tokens_mean: float = 0.0
    max_gap: Stats = field(default_factory=Stats)
    tokens_estimated: bool = False
    cost_total: Optional[float] = None
    retried: int = 0  # 经过重试才成功/失败的请求数
    tokens_source: str = "usage"  # usage / estimated / mixed
    text_chars_mean: float = 0.0
    truncated: int = 0  # 因 max_tokens 被截断的次数
    stop_reasons: Dict[str, int] = field(default_factory=dict)
    text_hashes: int = 0  # 输出文本去重后的种类数（=1 说明每次输出完全一致）
    baseline: bool = False
    ttft_vs_base: Optional[float] = None  # 相对基线的 TTFT 增量（秒）
    total_vs_base: Optional[float] = None
    tps_vs_base: Optional[float] = None  # 相对基线的吞吐差（tok/s）
    errors: Dict[str, int] = field(default_factory=dict)
    ttft_series: List[float] = field(default_factory=list)
    tps_series: List[float] = field(default_factory=list)
    stream: bool = True

    @property
    def label(self) -> str:
        return f"{self.target}/{self.model}"

    def to_dict(self) -> Dict:
        d = asdict(self)
        for k in ("ttfb", "ttft", "ttft_text", "total", "tps", "out_tokens", "max_gap"):
            d[k] = {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in d[k].items()}
        d["ttft_series"] = [round(x, 4) for x in self.ttft_series]
        d["tps_series"] = [round(x, 2) for x in self.tps_series]
        return d

    @classmethod
    def from_dict(cls, d: Dict) -> "Summary":
        s = cls(target=d["target"], model=d["model"], provider=d.get("provider", ""))
        for k in ("n", "ok", "fail", "success_rate", "in_tokens_mean", "reasoning_tokens_mean", "tokens_estimated", "cost_total", "retried", "errors", "ttft_series", "tps_series", "stream",
                  "tokens_source", "text_chars_mean", "truncated", "stop_reasons", "text_hashes", "baseline", "ttft_vs_base", "total_vs_base", "tps_vs_base"):
            if k in d:
                setattr(s, k, d[k])
        for k in ("ttfb", "ttft", "ttft_text", "total", "tps", "out_tokens", "max_gap"):
            if isinstance(d.get(k), dict):
                setattr(s, k, Stats(**{kk: d[k].get(kk, 0) for kk in Stats.__dataclass_fields__}))
        return s


def summarize(results: List[RequestResult]) -> List[Summary]:
    groups: Dict[str, List[RequestResult]] = {}
    order: List[str] = []
    for r in results:
        if r.warmup:
            continue
        key = f"{r.target}\x00{r.model}"
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    out: List[Summary] = []
    for key in order:
        rs = groups[key]
        oks = [r for r in rs if r.ok]
        s = Summary(target=rs[0].target, model=rs[0].model, provider=rs[0].provider, stream=rs[0].stream)
        s.n, s.ok, s.fail = len(rs), len(oks), len(rs) - len(oks)
        s.success_rate = s.ok / s.n if s.n else 0.0
        s.ttfb = Stats.of([r.ttfb for r in oks if r.ttfb > 0])
        s.ttft = Stats.of([r.ttft for r in oks if r.ttft > 0])
        s.ttft_text = Stats.of([r.ttft_text for r in oks if r.ttft_text > 0])
        s.total = Stats.of([r.total for r in oks])
        s.tps = Stats.of([r.tps for r in oks if r.tps > 0])
        s.out_tokens = Stats.of([float(r.output_tokens) for r in oks])
        s.in_tokens_mean = mean([r.input_tokens for r in oks])
        s.reasoning_tokens_mean = mean([r.reasoning_tokens for r in oks])
        s.max_gap = Stats.of([r.max_gap for r in oks if r.chunk_times])
        s.tokens_estimated = any(r.tokens_estimated for r in oks)
        est = sum(1 for r in oks if r.tokens_estimated)
        s.tokens_source = "usage" if est == 0 else ("estimated" if est == len(oks) else "mixed")
        s.text_chars_mean = mean([r.text_chars for r in oks])
        s.truncated = sum(1 for r in oks if r.truncated)
        s.stop_reasons = dict(Counter(r.stop_reason or "?" for r in oks))
        s.text_hashes = len({r.text_hash for r in oks if r.text_hash})
        costs = [r.cost for r in oks if r.cost is not None]
        s.cost_total = sum(costs) if costs else None
        s.retried = sum(1 for r in rs if r.attempt > 1)
        s.errors = dict(Counter(_short_err(r.error) for r in rs if not r.ok))
        s.ttft_series = [r.ttft for r in rs]
        s.tps_series = [r.tps for r in rs]
        out.append(s)
    return out


def apply_baseline(summaries: List[Summary], baseline_label: Optional[str]) -> Optional[Summary]:
    """把某个目标设为基线（如直连上游），给其余目标算出 TTFT / 总耗时 / 吞吐相对基线的增量。"""
    for s in summaries:
        s.baseline = False
        s.ttft_vs_base = s.total_vs_base = s.tps_vs_base = None
    if not baseline_label:
        return None
    base = next((s for s in summaries if s.label == baseline_label), None)
    if not base or base.ok == 0:
        return None
    base.baseline = True
    for s in summaries:
        if s is base or s.ok == 0:
            continue
        if base.ttft.n and s.ttft.n:
            s.ttft_vs_base = s.ttft.mean - base.ttft.mean
        if base.total.n and s.total.n:
            s.total_vs_base = s.total.mean - base.total.mean
        if base.tps.n and s.tps.n:
            s.tps_vs_base = s.tps.mean - base.tps.mean
    return base


def comparability_notes(summaries: List[Summary]) -> List[str]:
    """对比是否可信的提示：输出长度差异、截断、估算 token、样本量。"""
    notes: List[str] = []
    valid = [s for s in summaries if s.ok > 0]
    if len(valid) >= 2:
        lens = [s.text_chars_mean for s in valid if s.text_chars_mean > 0]
        if lens and max(lens) > min(lens) * 1.3:
            notes.append(f"各目标输出长度差异较大（{min(lens):.0f}~{max(lens):.0f} 字），总耗时与吞吐对比仅供参考")
    trunc = [s.label for s in valid if s.truncated]
    if trunc:
        notes.append("以下目标有请求因 max_tokens 截断：" + "、".join(trunc))
    est = [s.label for s in valid if s.tokens_source != "usage"]
    if est:
        notes.append("以下目标的 token 数为估算（接口未返回 usage），吞吐不参与排名：" + "、".join(est))
    if valid and min(s.ok for s in valid) < 5:
        notes.append("样本少于 5 次，均值差异可能只是波动，请看 ± 与 P90")
    return notes


def _short_err(msg: str, n: int = 80) -> str:
    msg = " ".join(msg.split())
    return msg if len(msg) <= n else msg[: n - 1] + "…"


def rank(summaries: List[Summary]) -> Dict[str, Optional[Summary]]:
    """给出各维度冠军。"""
    valid = [s for s in summaries if s.ok > 0]
    res: Dict[str, Optional[Summary]] = {"ttft": None, "tps": None, "total": None, "stable": None, "reliable": None}
    if not valid:
        return res
    with_ttft = [s for s in valid if s.ttft.n > 0]
    if with_ttft:
        res["ttft"] = min(with_ttft, key=lambda s: s.ttft.mean)
        res["stable"] = min(with_ttft, key=lambda s: s.ttft.cv)
    with_tps = [s for s in valid if s.tps.n > 0 and s.tokens_source == "usage"]
    if with_tps:
        res["tps"] = max(with_tps, key=lambda s: s.tps.mean)
    res["total"] = min(valid, key=lambda s: s.total.mean)
    res["reliable"] = max(valid, key=lambda s: (s.success_rate, -s.total.mean))
    return res

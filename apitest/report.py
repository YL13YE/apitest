"""终端展示（rich）与导出（json / csv / markdown）。"""
from __future__ import annotations

import csv
import io
import json
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

from rich import box
from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from .metrics import Summary, rank, sparkline
from .runner import RequestResult

# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------


def fmt_s(x: Optional[float]) -> str:
    if x is None or x <= 0:
        return "-"
    return f"{x * 1000:.0f}ms" if x < 1 else f"{x:.2f}s"


def fmt_n(x: float, digits: int = 1) -> str:
    if x <= 0:
        return "-"
    return f"{x:.{digits}f}"


def fmt_cost(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"${x:.5f}" if x < 0.01 else f"${x:.4f}"


def color_ttft(x: float) -> str:
    if x <= 0:
        return "dim"
    return "green" if x < 0.6 else "yellow" if x < 1.5 else "red"


def color_tps(x: float) -> str:
    if x <= 0:
        return "dim"
    return "green" if x >= 60 else "yellow" if x >= 25 else "red"


def color_rate(r: float) -> str:
    return "green" if r >= 0.99 else "yellow" if r >= 0.9 else "red"


def styled(text: str, style: str) -> Text:
    return Text(text, style=style)


# ---------------------------------------------------------------------------
# 实时看板
# ---------------------------------------------------------------------------


class LiveBoard:
    def __init__(self, per_target_total: Optional[int], console: Console, verbose: bool = False, show_output: bool = False):
        self.console = console
        self.verbose = verbose
        self.show_output = show_output
        self.per_target_total = per_target_total
        self.results: List[RequestResult] = []
        self.by_target: Dict[str, List[RequestResult]] = defaultdict(list)
        self.last_error: Dict[str, str] = {}
        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=24),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=False,
        )
        self.tasks: Dict[str, int] = {}
        self.order: List[str] = []

    def start_target(self, label: str) -> None:
        if label in self.tasks:
            return
        self.order.append(label)
        self.tasks[label] = self.progress.add_task(label, total=self.per_target_total)

    def on_result(self, r: RequestResult) -> None:
        label = f"{r.target}/{r.model}"
        self.start_target(label)
        if r.warmup:
            if self.verbose:
                self.console.log(f"[dim]预热 {label}: {'OK' if r.ok else r.error}[/]")
            return
        self.results.append(r)
        self.by_target[label].append(r)
        self.progress.advance(self.tasks[label], 1)
        if not r.ok:
            self.last_error[label] = r.error
        if self.verbose:
            self.console.print(self._line(r))
        if self.show_output and r.ok:
            preview = " ".join(r.text.split())
            t = Text(f"  ↳ {label} #{r.index + 1}: ", style="dim")
            t.append(preview[:160] + ("…" if len(preview) > 160 else ""), style="dim italic")
            self.console.print(t)

    def _line(self, r: RequestResult) -> Text:
        t = Text()
        t.append(f"#{r.index + 1:<3} ", style="dim")
        t.append(f"{r.target}/{r.model} ", style="bold")
        if r.ok:
            t.append("OK ", style="green")
            t.append(f"TTFT {fmt_s(r.ttft)} ", style=color_ttft(r.ttft))
            t.append(f"总 {fmt_s(r.total)} ", style="cyan")
            t.append(f"{r.output_tokens}{'~' if r.tokens_estimated else ''} tok ", style="magenta")
            t.append(f"{r.tps:.1f} tok/s", style=color_tps(r.tps))
        else:
            t.append("FAIL ", style="red bold")
            t.append(r.error[:120], style="red")
        return t

    def table(self) -> Table:
        tb = Table(box=box.SIMPLE_HEAD, expand=False, pad_edge=False, show_edge=False)
        tb.add_column("目标", style="bold")
        tb.add_column("完成", justify="right")
        tb.add_column("成功", justify="right")
        tb.add_column("TTFT 均值", justify="right")
        tb.add_column("总耗时 均值", justify="right")
        tb.add_column("吞吐 均值", justify="right")
        tb.add_column("TTFT 趋势")
        tb.add_column("最近错误", max_width=40, no_wrap=True, overflow="ellipsis")
        for label in self.order:
            rs = self.by_target.get(label, [])
            oks = [r for r in rs if r.ok]
            ttft = sum(r.ttft for r in oks) / len(oks) if oks else 0
            total = sum(r.total for r in oks) / len(oks) if oks else 0
            tps_vals = [r.tps for r in oks if r.tps > 0]
            tps = sum(tps_vals) / len(tps_vals) if tps_vals else 0
            rate = len(oks) / len(rs) if rs else 0
            tb.add_row(
                label,
                str(len(rs)),
                styled(f"{len(oks)}/{len(rs)}", color_rate(rate) if rs else "dim"),
                styled(fmt_s(ttft), color_ttft(ttft)),
                fmt_s(total),
                styled(f"{fmt_n(tps)} tok/s" if tps else "-", color_tps(tps)),
                Text(sparkline([r.ttft for r in rs if r.ok]), style="cyan"),
                Text(self.last_error.get(label, ""), style="red"),
            )
        return tb

    def render(self) -> RenderableType:
        return Group(self.progress, self.table())


# ---------------------------------------------------------------------------
# 汇总表
# ---------------------------------------------------------------------------

SORT_KEYS = {
    "ttft": lambda s: (s.ttft.mean if s.ttft.n else float("inf")),
    "tps": lambda s: -(s.tps.mean if s.tps.n else 0),
    "total": lambda s: (s.total.mean if s.total.n else float("inf")),
    "success": lambda s: (-s.success_rate, s.total.mean),
    "cost": lambda s: (s.cost_total if s.cost_total is not None else float("inf")),
    "none": lambda s: 0,
}


def sort_summaries(summaries: List[Summary], key: str) -> List[Summary]:
    fn = SORT_KEYS.get(key, SORT_KEYS["none"])
    return sorted(summaries, key=fn) if key != "none" else list(summaries)


def summary_table(summaries: List[Summary], title: str = "测速汇总") -> Table:
    show_cost = any(s.cost_total is not None for s in summaries)
    show_gap = any(s.max_gap.n > 0 for s in summaries)
    multi_round = any(s.n > 1 for s in summaries)
    show_text = any(s.ttft_text.n and abs(s.ttft_text.mean - s.ttft.mean) > 0.05 for s in summaries) or any(s.reasoning_tokens_mean > 0 for s in summaries)
    show_think = any(s.reasoning_tokens_mean > 0 for s in summaries)
    show_trunc = any(s.truncated for s in summaries)
    show_base = any(s.baseline for s in summaries)
    tb = Table(title=title, box=box.ROUNDED, title_style="bold", header_style="bold cyan", show_lines=False, pad_edge=False)
    tb.add_column("#", justify="right", style="dim")
    tb.add_column("目标 / 模型", style="bold", no_wrap=True)
    tb.add_column("成功", justify="right", no_wrap=True)
    tb.add_column("TTFB", justify="right", no_wrap=True)
    tb.add_column("TTFT ±波动" if multi_round else "TTFT", justify="right", no_wrap=True)
    if show_text:
        tb.add_column("TTFT文本", justify="right", no_wrap=True)
    if multi_round:
        tb.add_column("TTFT P90", justify="right", no_wrap=True)
    if show_base:
        tb.add_column("TTFT vs基线", justify="right", no_wrap=True)
    tb.add_column("总耗时", justify="right", no_wrap=True)
    if multi_round:
        tb.add_column("总 P90", justify="right", no_wrap=True)
    if show_base:
        tb.add_column("总 vs基线", justify="right", no_wrap=True)
    tb.add_column("输出tok", justify="right", no_wrap=True)
    if show_think:
        tb.add_column("思考tok", justify="right", no_wrap=True)
    tb.add_column("字数", justify="right", no_wrap=True)
    if show_trunc:
        tb.add_column("截断", justify="right", no_wrap=True)
    tb.add_column("tok/s", justify="right", no_wrap=True)
    if multi_round:
        tb.add_column("tok/s 范围", justify="right", no_wrap=True)
        tb.add_column("稳定CV", justify="right", no_wrap=True)
    if show_gap:
        tb.add_column("卡顿", justify="right", no_wrap=True)
    if show_cost:
        tb.add_column("费用", justify="right", no_wrap=True)
    for i, s in enumerate(summaries, 1):
        est = "~" if s.tokens_estimated else ""
        cv = s.ttft.cv
        cv_style = "green" if cv < 0.15 else "yellow" if cv < 0.4 else "red"
        def delta(v: Optional[float]) -> Any:
            if v is None:
                return styled("基线", "bold cyan") if s.baseline else "-"
            return styled(("+" if v >= 0 else "-") + fmt_s(abs(v)), "red" if v > 0 else "green")

        ttft_cell = fmt_s(s.ttft.mean) + (f" ±{fmt_s(s.ttft.std)}" if multi_round and s.ttft.n > 1 else "")
        row: List[Any] = [
            str(i),
            f"{s.target}/[dim]{s.model}[/]" + (" [cyan]基线[/]" if s.baseline else ""),
            styled(f"{s.ok}/{s.n}" + (f" ↻{s.retried}" if s.retried else ""), color_rate(s.success_rate)),
            fmt_s(s.ttfb.mean),
            styled(ttft_cell, color_ttft(s.ttft.mean)),
        ]
        if show_text:
            row.append(fmt_s(s.ttft_text.mean))
        if multi_round:
            row.append(fmt_s(s.ttft.p90))
        if show_base:
            row.append(delta(s.ttft_vs_base))
        row.append(fmt_s(s.total.mean))
        if multi_round:
            row.append(fmt_s(s.total.p90))
        if show_base:
            row.append(delta(s.total_vs_base))
        row.append((f"{est}{s.out_tokens.mean:.0f}" + (" [yellow]估[/]" if s.tokens_source != "usage" else "")) if s.out_tokens.n else "-")
        if show_think:
            row.append(f"{s.reasoning_tokens_mean:.0f}" if s.reasoning_tokens_mean else "-")
        row.append(f"{s.text_chars_mean:.0f}" if s.text_chars_mean else "-")
        if show_trunc:
            row.append(styled(str(s.truncated), "red") if s.truncated else "-")
        row.append(styled(fmt_n(s.tps.mean) + (" 估" if s.tokens_source != "usage" else ""), color_tps(s.tps.mean) if s.tokens_source == "usage" else "yellow"))
        if multi_round:
            row.append(f"{fmt_n(s.tps.min)}~{fmt_n(s.tps.max)}" if s.tps.n else "-")
            row.append(styled(f"{cv * 100:.0f}%" if s.ttft.n > 1 else "-", cv_style if s.ttft.n > 1 else "dim"))
        if show_gap:
            row.append(fmt_s(s.max_gap.max) if s.max_gap.n else "-")
        if show_cost:
            row.append(fmt_cost(s.cost_total))
        tb.add_row(*row)
    return tb


def details_table(results: List[RequestResult], show_output: bool = False) -> Table:
    tb = Table(title="逐次请求明细", box=box.SIMPLE_HEAD, header_style="bold cyan", pad_edge=False)
    tb.add_column("#", justify="right", style="dim", min_width=3)
    tb.add_column("目标 / 模型", no_wrap=True, max_width=28, overflow="ellipsis")
    tb.add_column("状态", no_wrap=True, min_width=4)
    tb.add_column("TTFB", justify="right", no_wrap=True, min_width=6)
    tb.add_column("TTFT", justify="right", no_wrap=True, min_width=6)
    tb.add_column("总耗时", justify="right", no_wrap=True, min_width=6)
    tb.add_column("输入", justify="right", no_wrap=True, min_width=5)
    tb.add_column("输出", justify="right", no_wrap=True, min_width=5)
    tb.add_column("思考", justify="right", no_wrap=True, min_width=4)
    tb.add_column("tok/s", justify="right", no_wrap=True, min_width=6)
    tb.add_column("chunk", justify="right", no_wrap=True, min_width=5)
    tb.add_column("卡顿", justify="right", no_wrap=True, min_width=5)
    tb.add_column("输出预览 / 错误", max_width=44, no_wrap=True, overflow="ellipsis")
    for r in results:
        if r.warmup:
            continue
        status = styled("OK", "green") if r.ok else styled(f"FAIL{' (重试' + str(r.attempt) + ')' if r.attempt > 1 else ''}", "red")
        preview = " ".join(r.error.split()) if not r.ok else ((" ".join(r.text.split())[:80]) if show_output else "")
        tb.add_row(
            str(r.index + 1),
            f"{r.target}/{r.model}",
            status,
            fmt_s(r.ttfb),
            styled(fmt_s(r.ttft), color_ttft(r.ttft)),
            fmt_s(r.total),
            str(r.input_tokens) if r.input_tokens else "-",
            f"{'~' if r.tokens_estimated else ''}{r.output_tokens}" if r.output_tokens else "-",
            str(r.reasoning_tokens) if r.reasoning_tokens else "-",
            styled(fmt_n(r.tps), color_tps(r.tps)),
            str(r.chunks) if r.chunks else "-",
            fmt_s(r.max_gap) if r.chunk_times else "-",
            Text(preview, style="red" if not r.ok else "dim"),
        )
    return tb


def errors_panel(summaries: List[Summary]) -> Optional[Panel]:
    lines = []
    for s in summaries:
        for msg, n in s.errors.items():
            lines.append(f"[bold]{s.label}[/] ×{n}: [red]{msg}[/]")
    if not lines:
        return None
    return Panel("\n".join(lines), title="错误汇总", border_style="red", expand=False)


def ranking_panel(summaries: List[Summary]) -> Optional[Panel]:
    if len(summaries) < 2:
        return None
    r = rank(summaries)
    rows = []
    if r["ttft"]:
        rows.append(f"⚡ 最快首字   [bold]{r['ttft'].label}[/]  {fmt_s(r['ttft'].ttft.mean)}")
    if r["tps"]:
        rows.append(f"🚀 最高吞吐   [bold]{r['tps'].label}[/]  {r['tps'].tps.mean:.1f} tok/s")
    if r["total"]:
        rows.append(f"⏱  最短总耗时 [bold]{r['total'].label}[/]  {fmt_s(r['total'].total.mean)}")
    if r["stable"] and r["stable"].ttft.n > 1:
        rows.append(f"📈 最稳定     [bold]{r['stable'].label}[/]  TTFT CV {r['stable'].ttft.cv * 100:.0f}%")
    if r["reliable"]:
        rows.append(f"✅ 最可靠     [bold]{r['reliable'].label}[/]  成功率 {r['reliable'].success_rate * 100:.0f}%")
    return Panel("\n".join(rows), title="排行", border_style="green", expand=False)


def legend_text() -> Text:
    return Text.from_markup(
        "[dim]TTFB=收到响应头  TTFT=首个 token  吞吐=输出 tokens ÷ 首字后的生成时长  "
        "TTFT文本=首个可见文本 token（思考片段不计）  吞吐分母用首个文本 token 之后的时长  ±=标准差  "
        "CV=标准差/均值(越小越稳)  卡顿=流式相邻 chunk 最长间隔  估=token 数为估算(不参与吞吐排名)  vs基线=相对基线目标的增量  ↻n=经重试的请求数[/]"
    )


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def build_payload(meta: Dict[str, Any], summaries: List[Summary], results: List[RequestResult]) -> Dict[str, Any]:
    return {
        "meta": meta,
        "summaries": [s.to_dict() for s in summaries],
        "results": [r.to_dict() for r in results if not r.warmup],
    }


def to_csv(results: List[RequestResult]) -> str:
    buf = io.StringIO()
    cols = [
        "index", "target", "model", "provider", "ok", "status_code", "error_kind", "error", "ttfb", "ttft", "ttft_text", "total",
        "gen_time", "input_tokens", "output_tokens", "reasoning_tokens", "tokens_estimated", "tps", "chunks", "max_gap", "cost", "started_at", "attempt",
    ]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in results:
        d = r.to_dict() if hasattr(r, "to_dict") else dict(r)
        if d.get("warmup"):
            continue
        for k in ("ttfb", "ttft", "ttft_text", "total"):
            d[k] = round(d.get(k) or 0, 4)
        w.writerow(d)
    return buf.getvalue()


def to_markdown(meta: Dict[str, Any], summaries: List[Summary]) -> str:
    lines = [f"# AI API 测速报告", ""]
    lines.append(f"- 时间: {meta.get('time', '')}")
    lines.append(f"- 预设/提示词: {meta.get('preset') or '自定义'}  max_tokens={meta.get('max_tokens')}  流式={'是' if meta.get('stream') else '否'}")
    lines.append(f"- 轮次: {meta.get('rounds')}  并发: {meta.get('concurrency')}  预热: {meta.get('warmup')}")
    if meta.get("tag"):
        lines.append(f"- 标签: {meta['tag']}")
    lines.append("")
    lines.append("| # | 目标 | 模型 | 成功率 | TTFT 均值 | TTFT P90 | 总耗时 均值 | 输出 tok | 吞吐 tok/s | TTFT CV | 费用 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, s in enumerate(summaries, 1):
        est = "~" if s.tokens_estimated else ""
        lines.append(
            f"| {i} | {s.target} | {s.model} | {s.ok}/{s.n} | {fmt_s(s.ttft.mean)} | {fmt_s(s.ttft.p90)} | {fmt_s(s.total.mean)} | "
            f"{est}{s.out_tokens.mean:.0f} | {fmt_n(s.tps.mean)} | {s.ttft.cv * 100:.0f}% | {fmt_cost(s.cost_total)} |"
        )
    errs = [(s.label, m, n) for s in summaries for m, n in s.errors.items()]
    if errs:
        lines += ["", "## 错误", ""]
        for label, m, n in errs:
            lines.append(f"- **{label}** ×{n}: {m}")
    lines.append("")
    return "\n".join(lines)


def export(path: str, meta: Dict[str, Any], summaries: List[Summary], results: List[RequestResult]) -> None:
    p = path.lower()
    if p.endswith(".json"):
        content = json.dumps(build_payload(meta, summaries, results), ensure_ascii=False, indent=2)
    elif p.endswith(".csv"):
        content = to_csv(results)
    elif p.endswith(".md") or p.endswith(".markdown"):
        content = to_markdown(meta, summaries)
    else:
        raise ValueError(f"不支持的导出格式: {path}（支持 .json / .csv / .md）")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

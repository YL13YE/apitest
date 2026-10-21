"""命令行入口。"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import EXAMPLE_CONFIG, find_config, load_config, resolve_targets
from .history import clear as history_clear, find_run, load_pings, load_runs, make_ping_record, save_ping, save_run
from .metrics import Stats, Summary, summarize
from .netcheck import ping_once
from .prompts import PRESETS, describe_presets
from .providers import PROVIDERS, Target, auth_headers, models_url, parse_models
from .report import (
    LiveBoard,
    color_rate,
    color_tps,
    color_ttft,
    details_table,
    errors_panel,
    export,
    fmt_cost,
    fmt_n,
    fmt_s,
    legend_text,
    now_str,
    ranking_panel,
    sort_summaries,
    styled,
    summary_table,
)
from .runner import RunOptions, run_all
from .budget import RunControl, validate_options
from .evaluation import scenario_metadata
from .security import private_write, redact
from .pricing import parse_price_table

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def _add_target_args(p: argparse.ArgumentParser, need_model: bool = True) -> None:
    g = p.add_argument_group("目标选择")
    g.add_argument("-e", "--endpoint", action="append", default=[], metavar="NAME[:m1,m2]", help="配置文件中的端点名，可多次；冒号后可指定模型")
    g.add_argument("--all", action="store_true", help="测试配置文件中的全部端点")
    g.add_argument("--url", help="临时指定 base_url（如 https://api.openai.com/v1）")
    g.add_argument("--key", help="临时指定 API Key（也可用环境变量 APITEST_API_KEY）")
    if need_model:
        g.add_argument("-m", "--model", help="模型名，逗号分隔可多个；对 -e 端点则覆盖其 models")
    g.add_argument("-p", "--provider", choices=("auto",) + PROVIDERS, default=None, help="API 风格，默认自动识别")
    g.add_argument("--name", help="--url 目标的显示名称")
    g.add_argument("-H", "--header", action="append", default=[], metavar="K:V", help="附加请求头，可多次")
    g.add_argument("--extra-body", metavar="JSON", help="合并进请求体的 JSON，如 '{\"temperature\":0}'")
    g.add_argument("--proxy", help="HTTP 代理，如 http://127.0.0.1:7890")
    g.add_argument("--insecure", action="store_true", help="不校验 TLS 证书")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apitest",
        description="AI API 测速工具：测量首字延迟(TTFT)、吞吐(tok/s)、总耗时、成功率与稳定性，支持多端点多模型对比。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  apitest run --url https://api.openai.com/v1 --key sk-xxx -m gpt-4o-mini -n 5\n"
            "  apitest run -e openai -e claude:claude-sonnet-5 -n 5 -c 2 --preset long\n"
            "  apitest run --all --duration 60 -c 4 --export report.md\n"
            "  apitest models --url https://api.example.com/v1 --key sk-xxx\n"
            "  apitest ping -e openai\n"
            "  apitest history --last 10\n"
        ),
    )
    p.add_argument("-V", "--version", action="version", version=f"apitest {__version__}")
    p.add_argument("--config", help="配置文件路径（默认查找 ./apitest.yaml, ~/.config/apitest/config.yaml）")
    sub = p.add_subparsers(dest="cmd", required=True)

    # run
    r = sub.add_parser("run", help="执行测速", formatter_class=argparse.RawDescriptionHelpFormatter, epilog="提示词预设:\n" + describe_presets())
    _add_target_args(r)
    g = r.add_argument_group("负载")
    g.add_argument("-n", "--rounds", type=int, help="每个模型请求次数（默认 3）")
    g.add_argument("-c", "--concurrency", type=int, help="并发数（默认 1）")
    g.add_argument("--warmup", type=int, help="预热请求数，不计入统计（默认 0）")
    g.add_argument("--duration", type=float, metavar="SEC", help="持续压测秒数，设置后忽略 -n")
    g.add_argument("--hours", type=float, help="持续小时数，与 --duration 二选一")
    g.add_argument("--sample-minutes", type=float, help="每轮所有目标的采样周期（分钟），跳过错过的周期")
    g.add_argument("--budget", type=float, help="总预算，币种同端点价格；未知用量保留预留费用")
    g.add_argument("--hourly-budget", type=float, help="每个运行小时的预算上限")
    g.add_argument("--max-requests", type=int, help="整个任务的最大请求数，包含预热及重试")
    g.add_argument("--cache-mode", choices=("cold", "warm", "as-is"), help="冷缓存随机前缀 / 固定前缀复用 / 原请求")
    g.add_argument("--plan", action="store_true", help="仅显示请求与预算计划，不调用 API")
    g.add_argument("--interval", type=float, help="每次请求之间的间隔秒数")
    g.add_argument("--timeout", type=float, help="单次请求超时秒数（默认 120）")
    g.add_argument("--retries", type=int, help="失败重试次数（默认 0）")
    g.add_argument("--order", choices=("interleaved", "sequential", "parallel"), default=None, help="多目标执行顺序：interleaved 交替轮询（默认，摊平时间漂移）/ sequential 逐个 / parallel 全部并行")
    g.add_argument("--parallel-targets", action="store_true", help="等价于 --order parallel")
    g.add_argument("--baseline", metavar="端点/模型", help="把某个目标设为基线（如直连上游），报告中给出其余目标相对它的 TTFT / 总耗时增量")
    g.add_argument("--http2", action="store_true", help="启用 HTTP/2（需安装 h2）")
    g = r.add_argument_group("请求内容")
    g.add_argument("--preset", choices=list(PRESETS), help="提示词预设（默认 medium）")
    g.add_argument("--prompt", help="自定义提示词")
    g.add_argument("--prompt-file", help="从文件读取提示词")
    g.add_argument("--system", help="system 提示词")
    g.add_argument("--max-tokens", type=int, help="最大输出 tokens")
    g.add_argument("--temperature", type=float)
    g.add_argument("--no-stream", action="store_true", help="非流式请求（无法测 TTFT）")
    g = r.add_argument_group("输出")
    g.add_argument("--sort", choices=("ttft", "tps", "total", "success", "cost", "none"), default="none", help="汇总表排序")
    g.add_argument("-d", "--details", action="store_true", help="显示逐次请求明细")
    g.add_argument("-v", "--verbose", action="store_true", help="实时打印每次请求结果")
    g.add_argument("--show-output", action="store_true", help="显示模型输出预览")
    g.add_argument("--export", action="append", default=[], metavar="FILE", help="导出结果，按扩展名 .json/.csv/.md，可多次")
    g.add_argument("--json", action="store_true", help="以 JSON 输出到 stdout（便于脚本处理）")
    g.add_argument("-q", "--quiet", action="store_true", help="不显示实时看板")
    g.add_argument("--tag", help="给本次测试打标签（写入历史）")
    g.add_argument("--no-history", action="store_true", help="不写入历史记录")

    # models
    m = sub.add_parser("models", help="列出端点可用模型")
    _add_target_args(m, need_model=False)
    m.add_argument("-f", "--filter", help="按子串过滤模型名")
    m.add_argument("--json", action="store_true")

    # ping
    pg = sub.add_parser("ping", help="连通性诊断：DNS / TCP / TLS / HTTP 各阶段耗时")
    _add_target_args(pg, need_model=False)
    pg.add_argument("-n", "--rounds", type=int, default=3)
    pg.add_argument("--timeout", type=float, default=10.0)
    pg.add_argument("--no-history", action="store_true", help="不写入诊断历史")
    pg.add_argument("--history", action="store_true", help="查看诊断历史（可用 -e 按端点筛选）")
    pg.add_argument("--last", type=int, default=20, help="--history 时显示最近 N 条")

    # history
    h = sub.add_parser("history", help="查看历史测速记录")
    h.add_argument("--last", type=int, default=15, help="显示最近 N 条")
    h.add_argument("--show", metavar="ID", help="显示某次记录的详细汇总（ID 可只写前缀）")
    h.add_argument("--json", action="store_true")
    h.add_argument("--clear", action="store_true", help="清空历史")

    # init / presets
    i = sub.add_parser("init", help="在当前目录生成示例配置 apitest.yaml")
    i.add_argument("--force", action="store_true", help="覆盖已有文件")
    sub.add_parser("presets", help="列出内置提示词预设")

    prices = sub.add_parser("import-prices", help="导入中转站模型定价 JSON（不发起请求）")
    prices.add_argument("file", help="version: 1 的模型定价表")
    prices.add_argument("-e", "--endpoint", action="append", required=True, help="应用到指定端点，可多次")
    prices.add_argument("--currency", required=True, choices=("USD", "CNY"), help="定价表币种，价格单位为百万 token")

    # web
    w = sub.add_parser("web", help="启动本地 Web 图形界面")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8787)
    w.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    w.add_argument("--password-file", help="非回环监听的访问密码文件；默认 ~/.apitest/web-password，不存在时自动生成")
    w.add_argument("--ssl-certfile", help="可选 HTTPS 证书文件")
    w.add_argument("--ssl-keyfile", help="可选 HTTPS 私钥文件")
    return p


def parse_headers(items: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for it in items:
        if ":" in it:
            k, v = it.split(":", 1)
        elif "=" in it:
            k, v = it.split("=", 1)
        else:
            raise ValueError(f"请求头格式应为 K:V — {it}")
        out[k.strip()] = v.strip()
    return out


def parse_extra_body(s: Optional[str]) -> Dict[str, Any]:
    if not s:
        return {}
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as e:
        raise ValueError(f"--extra-body 不是合法 JSON: {e}")
    if not isinstance(obj, dict):
        raise ValueError("--extra-body 必须是 JSON 对象")
    return obj


def get_targets(args: argparse.Namespace, cfg: Dict[str, Any], need_model: bool = True) -> List[Target]:
    models = getattr(args, "model", None)
    if not need_model and args.url and not models:
        models = "-"
    targets = resolve_targets(
        cfg,
        endpoint_specs=args.endpoint,
        url=args.url,
        key=args.key,
        models=models,
        provider=args.provider,
        use_all=args.all,
        headers=parse_headers(args.header),
        extra_body=parse_extra_body(getattr(args, "extra_body", None)),
        name=args.name,
    )
    if not targets:
        raise ValueError("未指定测试目标。用 --url + -m，或 -e 端点名，或 --all（先 apitest init 生成配置）")
    if not need_model:
        # 每个端点只保留一个代表
        seen, uniq = set(), []
        for t in targets:
            if t.name not in seen:
                seen.add(t.name)
                uniq.append(t)
        targets = uniq
    return targets


def pick(*vals, default=None):
    for v in vals:
        if v is not None:
            return v
    return default


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


def cmd_run(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    d = cfg.get("defaults") or {}
    targets = get_targets(args, cfg)
    if args.hours is not None and args.duration is not None:
        raise ValueError("--hours 和 --duration 不能同时设置")

    preset_name = pick(args.preset, d.get("prompt_preset"), default="medium")
    preset = PRESETS[preset_name]
    if args.prompt:
        prompt, preset_label = args.prompt, None
    elif args.prompt_file:
        prompt, preset_label = Path(args.prompt_file).read_text(encoding="utf-8"), f"file:{args.prompt_file}"
    elif d.get("prompt"):
        prompt, preset_label = str(d["prompt"]), "config"
    else:
        prompt, preset_label = preset["prompt"], preset_name

    opts = RunOptions(
        prompt=prompt,
        system=pick(args.system, d.get("system")),
        max_tokens=int(pick(args.max_tokens, d.get("max_tokens") if preset_label != preset_name or d.get("max_tokens") else None, preset["max_tokens"])),
        temperature=pick(args.temperature, d.get("temperature")),
        stream=not args.no_stream and bool(pick(d.get("stream"), default=True)),
        rounds=int(pick(args.rounds, d.get("rounds"), default=3)),
        concurrency=int(pick(args.concurrency, d.get("concurrency"), default=1)),
        warmup=int(pick(args.warmup, d.get("warmup"), default=0)),
        timeout=float(pick(args.timeout, d.get("timeout"), default=120)),
        interval=float(pick(args.interval, d.get("interval"), default=0)),
        duration=args.hours * 3600 if args.hours is not None else pick(args.duration, d.get("duration")),
        retries=int(pick(args.retries, d.get("retries"), default=0)),
        parallel_targets=args.parallel_targets or bool(d.get("parallel_targets")),
        order=pick(args.order, d.get("order"), default="interleaved"),
        verify_ssl=not args.insecure,
        proxy=pick(args.proxy, d.get("proxy"), os.environ.get("APITEST_PROXY")),
        http2=args.http2,
        sample_interval=float(pick(args.sample_minutes, d.get("sample_minutes"), default=0)) * 60,
        budget=pick(args.budget, d.get("budget")),
        hourly_budget=pick(args.hourly_budget, d.get("hourly_budget")),
        max_requests=pick(args.max_requests, d.get("max_requests")),
        cache_mode=pick(args.cache_mode, d.get("cache_mode"), default="cold"),
    )
    validate_options(opts)
    planned = RunControl(targets, opts)
    if args.plan:
        import math
        rounds = math.ceil(opts.duration / opts.sample_interval) if opts.duration and opts.sample_interval else (None if opts.duration else opts.rounds)
        attempts = (rounds * (opts.retries + 1) + opts.warmup) * len(targets) if rounds is not None else opts.max_requests
        if attempts is not None and opts.max_requests is not None:
            attempts = min(attempts, opts.max_requests)
        reservations = {t.label: (planned.reservations[t.label] if t.price_in is not None and t.price_out is not None else None) for t in targets}
        print(json.dumps({"api_calls_made": 0, "targets": [t.label for t in targets], "duration_seconds": opts.duration, "planned_attempts_upper_bound": attempts, "per_request_reservation": reservations, "pricing_complete": all(v is not None for v in reservations.values()), "limits": planned.snapshot(), "note": "null 费用表示未配置价格，不能视为免费。预留采用保守字节上界；实际账单由服务商决定；高思考档位需足够输出上限。"}, ensure_ascii=False, indent=2))
        return 0
    if opts.rounds <= 0 and not opts.duration:
        raise ValueError("-n 必须大于 0")

    ui = not args.json
    if ui:
        _print_run_header(targets, opts, preset_label)

    board = LiveBoard(
        per_target_total=None if opts.duration else opts.rounds,
        console=console if ui else err_console,
        verbose=args.verbose and ui,
        show_output=args.show_output and ui,
    )
    started = time.time()
    interrupted = False
    checkpoint_id = None

    def on_result(result):
        nonlocal checkpoint_id
        board.on_result(result)
        if not args.no_history and board.attempts:
            checkpoint_id = save_run({"time": now_str(), "status": "running", "targets": [t.label for t in targets], "budget": opts.control.snapshot() if opts.control else {}, **scenario_metadata(opts, preset_label, targets)}, [s.to_dict() for s in summarize(board.attempts)], [r.to_dict() for r in board.attempts], checkpoint_id)

    async def go():
        return await run_all(targets, opts, on_result=on_result, on_target_start=lambda t: board.start_target(t.label))

    try:
        if ui and not args.quiet:
            with Live(board.render(), console=console, refresh_per_second=8, get_renderable=board.render):
                asyncio.run(go())
        else:
            asyncio.run(go())
    except KeyboardInterrupt:
        interrupted = True
        err_console.print("\n[yellow]已中断，展示已完成部分的结果…[/]")

    order = {t.label: i for i, t in enumerate(targets)}
    results = sorted(board.attempts, key=lambda r: (order.get(f"{r.target}/{r.model}", 999), r.index))
    summaries = sort_summaries(summarize(results), args.sort)
    from .metrics import apply_baseline, comparability_notes

    if args.baseline and not apply_baseline(summaries, args.baseline):
        err_console.print(f"[yellow]基线目标 {args.baseline} 不在结果中或全部失败，未计算增量[/]")
    elapsed = time.time() - started
    meta = {
        "time": now_str(),
        "elapsed": round(elapsed, 2),
        "preset": preset_label,
        "prompt_chars": len(prompt),
        "system": bool(opts.system),
        "max_tokens": opts.max_tokens,
        "stream": opts.stream,
        "rounds": opts.rounds if not opts.duration else None,
        "duration": opts.duration,
        "concurrency": opts.concurrency,
        "warmup": opts.warmup,
        "parallel_targets": opts.parallel_targets,
        "order": "parallel" if opts.parallel_targets else opts.order,
        "baseline": args.baseline,
        "tag": args.tag,
        "interrupted": interrupted,
        "targets": [t.label for t in targets],
        "version": __version__,
        "sample_interval": opts.sample_interval,
        "cache_mode": opts.cache_mode,
        "budget": opts.control.snapshot() if opts.control else {},
        "status": "stopped" if interrupted else "done",
        **scenario_metadata(opts, preset_label, targets),
    }

    if not results:
        reason = opts.control.stop_reason if opts.control else "unknown"
        err_console.print(f"[red]没有发出请求：{reason}。请检查单次费用预留、预算和时限。[/]")
        if args.json:
            from .report import build_payload
            print(json.dumps(build_payload(meta, [], []), ensure_ascii=False, indent=2))
        return 1

    if ui:
        console.print()
        console.print(summary_table(summaries, title=f"测速汇总  ({len(results)} 次请求，用时 {elapsed:.1f}s)"))
        console.print(legend_text())
        for note in comparability_notes(summaries):
            console.print(f"[yellow]⚠ {note}[/]")
        rp = ranking_panel(summaries)
        if rp:
            console.print(rp)
        ep = errors_panel(summaries)
        if ep:
            console.print(ep)
        if args.details:
            console.print(details_table(results, show_output=args.show_output))

    for path in args.export:
        export(path, meta, summaries, results)
        if ui:
            console.print(f"[green]已导出:[/] {path}")

    if not args.no_history:
        try:
            run_id = save_run(meta, [s.to_dict() for s in summaries], [r.to_dict() for r in results], checkpoint_id)
            if ui:
                console.print(f"[dim]历史记录 ID: {run_id}（apitest history --show {run_id[:15]}）[/]")
        except OSError as e:
            err_console.print(f"[yellow]写入历史失败: {e}[/]")

    if args.json:
        from .report import build_payload

        print(json.dumps(build_payload(meta, summaries, results), ensure_ascii=False, indent=2))

    any_fail = any(s.ok == 0 for s in summaries)
    return 2 if any_fail or not summaries else 0


def _print_run_header(targets: List[Target], opts: RunOptions, preset_label: Optional[str]) -> None:
    tb = Table(box=box.SIMPLE, show_header=False, pad_edge=False)
    tb.add_column(style="bold cyan")
    tb.add_column()
    tb.add_row("目标", "\n".join(f"{t.label}  [dim]({t.provider} · {t.base_url or '默认'})[/]" for t in targets))
    load = f"持续 {opts.duration:g}s" if opts.duration else f"{opts.rounds} 次/模型"
    order_name = {"interleaved": "交替轮询", "sequential": "逐个目标", "parallel": "全部并行"}.get("parallel" if opts.parallel_targets else opts.order, opts.order)
    tb.add_row("负载", f"{load}，并发 {opts.concurrency}，预热 {opts.warmup}，超时 {opts.timeout:g}s" + ("，重试 " + str(opts.retries) if opts.retries else "") + (f"，{order_name}" if len(targets) > 1 else ""))
    tb.add_row("请求", f"预设 {preset_label or '自定义'}，max_tokens={opts.max_tokens}，{'流式' if opts.stream else '非流式'}" + (f"，temperature={opts.temperature}" if opts.temperature is not None else "") + ("，含 system" if opts.system else ""))
    tb.add_row("提示词", f"[dim]{opts.prompt[:100].replace(chr(10), ' ')}{'…' if len(opts.prompt) > 100 else ''}[/]")
    console.print(Panel(tb, title=f"apitest {__version__}", border_style="cyan", expand=False))


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------


def cmd_models(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    import httpx

    targets = get_targets(args, cfg, need_model=False)
    all_out = {}
    rc = 0
    for t in targets:
        url = models_url(t)
        try:
            with httpx.Client(timeout=30, verify=not args.insecure, follow_redirects=False, trust_env=False, **({"proxy": args.proxy} if args.proxy else {})) as c:
                t0 = time.perf_counter()
                resp = c.get(url, headers=auth_headers(t))
                dt = time.perf_counter() - t0
            if resp.status_code >= 300:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            models = parse_models(t.provider, resp.json())
        except Exception as e:  # noqa: BLE001
            err_console.print(f"[red]{t.name}[/] ({url}) 获取失败: {e}")
            rc = 1
            continue
        if args.filter:
            models = [m for m in models if args.filter.lower() in m["id"].lower()]
        models.sort(key=lambda m: m["id"])
        all_out[t.name] = models
        if args.json:
            continue
        tb = Table(title=f"{t.name}  [dim]{url}  ({len(models)} 个模型，{dt * 1000:.0f}ms)[/]", box=box.SIMPLE_HEAD, header_style="bold cyan")
        tb.add_column("模型 ID")
        tb.add_column("名称 / 所有者", style="dim")
        tb.add_column("创建", style="dim")
        for m in models:
            created = m["created"]
            if isinstance(created, (int, float)) and created > 0:
                created = time.strftime("%Y-%m-%d", time.localtime(created))
            tb.add_row(m["id"], str(m["name"]), str(created or ""))
        console.print(tb)
    if args.json:
        print(json.dumps(all_out, ensure_ascii=False, indent=2))
    return rc


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


def cmd_ping_history(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    names = [spec.partition(":")[0].strip() for spec in args.endpoint]
    recs = load_pings(None, None)
    if names:
        recs = [r for r in recs if r.get("endpoint") in names]
    recs = recs[-args.last:]
    if not recs:
        console.print("[dim]暂无诊断历史[/]")
        return 0
    tb = Table(title=f"诊断历史（最近 {len(recs)} 条）", box=box.ROUNDED, header_style="bold cyan")
    for col, just in (("时间", "left"), ("端点", "left"), ("IP", "left"), ("DNS", "right"), ("TCP", "right"), ("TLS", "right"), ("HTTP 均值", "right"), ("HTTP 最大", "right"), ("成功", "right"), ("状态", "left")):
        tb.add_column(col, justify=just)
    for r in recs:
        a = r.get("avg") or {}
        status = styled(f"HTTP {r['status']}", "green" if r["status"] < 400 else "yellow") if r.get("status") else styled((r.get("error") or "")[:40], "red")
        tb.add_row(r["time"], r["endpoint"], r.get("ip", ""), fmt_s(a.get("dns", 0)), fmt_s(a.get("tcp", 0)), fmt_s(a.get("tls", 0)), styled(fmt_s(a.get("http", 0)), color_ttft(a.get("http", 0))), fmt_s(r.get("http_max", 0)), styled(f"{r['ok']}/{r['n']}", color_rate(r["ok"] / r["n"] if r["n"] else 0)), status)
    console.print(tb)
    return 0


def cmd_ping(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    if args.history:
        return cmd_ping_history(args, cfg)
    targets = get_targets(args, cfg, need_model=False)
    tb = Table(title="连通性诊断", box=box.ROUNDED, header_style="bold cyan")
    for col, just in (("端点", "left"), ("主机", "left"), ("IP", "left"), ("#", "right"), ("DNS", "right"), ("TCP", "right"), ("TLS", "right"), ("HTTP(models)", "right"), ("状态", "left"), ("服务器", "left")):
        tb.add_column(col, justify=just)
    rc = 0
    for t in targets:
        rows = []
        for i in range(args.rounds):
            r = ping_once(t, timeout=args.timeout, proxy=args.proxy, verify=not args.insecure)
            rows.append(r)
            status = styled(f"HTTP {r.status}", "green" if r.status < 400 else "yellow") if r.status else styled(r.error[:60], "red")
            if r.error:
                rc = 1
            tb.add_row(
                t.name if i == 0 else "",
                r.host if i == 0 else "",
                r.ip if i == 0 else "",
                str(i + 1),
                fmt_s(r.dns),
                fmt_s(r.tcp),
                fmt_s(r.tls),
                styled(fmt_s(r.http), color_ttft(r.http)),
                status,
                r.server[:24] if i == 0 else "",
            )
        oks = [r for r in rows if r.ok]
        if not args.no_history:
            try:
                save_ping(make_ping_record(t.name, models_url(t), [{"host": r.host, "ip": r.ip, "dns": r.dns, "tcp": r.tcp, "tls": r.tls, "http": r.http, "status": r.status, "error": r.error, "server": r.server} for r in rows], source="cli", proxy=args.proxy))
            except OSError:
                pass
        if len(rows) > 1:
            def avg(attr):
                vals = [getattr(r, attr) for r in oks if getattr(r, attr) > 0]
                return sum(vals) / len(vals) if vals else 0
            tb.add_row("", "", "", "均值", fmt_s(avg("dns")), fmt_s(avg("tcp")), fmt_s(avg("tls")), styled(fmt_s(avg("http")), "bold"), f"{len(oks)}/{len(rows)} 成功", "", style="dim")
    console.print(tb)
    console.print("[dim]HTTP(models) = 对 /models 接口一次完整 GET 的耗时（含鉴权），可视为该端点的网络 + 网关基础延迟。[/]")
    return rc


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def _summary_from_dict(d: Dict[str, Any]) -> Summary:
    return Summary.from_dict(d)


def cmd_history(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    if args.clear:
        history_clear()
        console.print("[green]历史已清空[/]")
        return 0
    if args.show:
        run = find_run(args.show)
        if not run:
            err_console.print(f"[red]未找到记录: {args.show}[/]")
            return 1
        if args.json:
            print(json.dumps(run, ensure_ascii=False, indent=2))
            return 0
        meta = run["meta"]
        console.print(f"[bold]{run['id']}[/]  {meta.get('time')}  标签: {meta.get('tag') or '-'}  预设: {meta.get('preset')}  max_tokens={meta.get('max_tokens')}  "
                      f"轮次={meta.get('rounds') or ('持续 ' + str(meta.get('duration')) + 's')}  并发={meta.get('concurrency')}")
        summaries = [_summary_from_dict(s) for s in run["summaries"]]
        console.print(summary_table(summaries, title="历史汇总"))
        ep = errors_panel(summaries)
        if ep:
            console.print(ep)
        return 0

    runs = load_runs(args.last)
    if args.json:
        print(json.dumps(runs, ensure_ascii=False, indent=2))
        return 0
    if not runs:
        console.print("[dim]暂无历史记录[/]")
        return 0
    tb = Table(title=f"最近 {len(runs)} 次测速", box=box.ROUNDED, header_style="bold cyan")
    tb.add_column("ID")
    tb.add_column("时间")
    tb.add_column("标签")
    tb.add_column("预设")
    tb.add_column("目标 / 模型")
    tb.add_column("成功率", justify="right")
    tb.add_column("TTFT 均值", justify="right")
    tb.add_column("吞吐 均值", justify="right")
    tb.add_column("总耗时 均值", justify="right")
    for run in runs:
        meta = run.get("meta", {})
        sums = run.get("summaries", [])
        first = True
        for s in sums:
            rate = s.get("success_rate", 0)
            ttft = (s.get("ttft") or {}).get("mean", 0)
            tps = (s.get("tps") or {}).get("mean", 0)
            total = (s.get("total") or {}).get("mean", 0)
            tb.add_row(
                run["id"] if first else "",
                meta.get("time", "") if first else "",
                (meta.get("tag") or "") if first else "",
                str(meta.get("preset") or "") if first else "",
                f"{s['target']}/{s['model']}",
                styled(f"{s.get('ok', 0)}/{s.get('n', 0)}", color_rate(rate)),
                styled(fmt_s(ttft), color_ttft(ttft)),
                styled(fmt_n(tps), color_tps(tps)),
                fmt_s(total),
            )
            first = False
        tb.add_section()
    console.print(tb)
    return 0


# ---------------------------------------------------------------------------
# init / presets
# ---------------------------------------------------------------------------


def cmd_init(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    p = Path("apitest.yaml")
    if p.exists() and not args.force:
        err_console.print(f"[yellow]{p} 已存在，使用 --force 覆盖[/]")
        return 1
    private_write(p, EXAMPLE_CONFIG)
    console.print(f"[green]已生成 {p}[/]，请填入 base_url / api_key / models 后运行: apitest run --all")
    return 0


def cmd_presets(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    tb = Table(title="内置提示词预设", box=box.SIMPLE_HEAD, header_style="bold cyan")
    tb.add_column("名称", style="bold")
    tb.add_column("说明")
    tb.add_column("max_tokens", justify="right")
    tb.add_column("提示词", style="dim", max_width=60, no_wrap=True, overflow="ellipsis")
    for k, v in PRESETS.items():
        tb.add_row(k, v["desc"], str(v["max_tokens"]), v["prompt"].replace("\n", " ")[:80])
    console.print(tb)
    return 0


# ---------------------------------------------------------------------------


def cmd_web(args: argparse.Namespace, cfg: Dict[str, Any]) -> int:
    from .web import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_open, config_path=args.config, password_file=args.password_file, ssl_certfile=args.ssl_certfile, ssl_keyfile=args.ssl_keyfile)
    return 0


def cmd_import_prices(args, cfg):
    import yaml
    path = find_config(args.config)
    if path is None:
        raise ValueError("请先配置端点，再导入定价表")
    # Read the raw config so ${ENV} credentials stay references after saving.
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    endpoints = raw.get("endpoints") or {}
    prices = parse_price_table(json.loads(Path(args.file).read_text(encoding="utf-8-sig")))
    for name in args.endpoint:
        if name not in endpoints:
            raise ValueError("配置中没有端点: " + name)
        ep = endpoints[name]
        if ep.get("price") and str(ep.get("currency") or "USD").upper() != args.currency:
            raise ValueError("端点原有价格币种不同，请先清理或转换原有价格")
    for name in dict.fromkeys(args.endpoint):
        ep = endpoints[name]
        raw.setdefault("price_catalogs", {})[name] = {"base_url": ep.get("base_url", ep.get("url", "")),
            "provider": ep.get("provider", ""), "currency": args.currency, "price": prices}
        ep["currency"] = args.currency
    private_write(path, yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    console.print(f"已导入 {len(prices)} 个模型价格到 {len(set(args.endpoint))} 个端点，币种 {args.currency} / 百万 token；仅按模型 ID 精确匹配。")
    return 0


def main(argv: Optional[List[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "models": cmd_models, "ping": cmd_ping, "history": cmd_history, "init": cmd_init, "presets": cmd_presets, "web": cmd_web, "import-prices": cmd_import_prices}
    try:
        cfg = load_config(find_config(args.config)) if args.cmd not in ("init", "web", "import-prices") else {"defaults": {}, "endpoints": {}}
        rc = handlers[args.cmd](args, cfg)
    except (ValueError, FileNotFoundError) as e:
        err_console.print(f"[red]错误:[/] {e}")
        rc = 1
    except KeyboardInterrupt:
        err_console.print("\n[yellow]已取消[/]")
        rc = 130
    sys.exit(rc)


if __name__ == "__main__":
    main()

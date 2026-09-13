<div align="center">

<img src="apitest/static/favicon.svg" width="72" alt="apitest">

# apitest

**AI API 测速与对比工具** — 量化每个模型、每家网关的首字延迟、吞吐与稳定性

[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/liningbest/apitest/actions/workflows/ci.yml/badge.svg)](https://github.com/liningbest/apitest/actions)
[![Providers](https://img.shields.io/badge/providers-OpenAI%20%7C%20Anthropic%20%7C%20Gemini%20%7C%20%E4%B8%AD%E8%BD%AC%E7%AB%99-555)](#支持的接口)

[English](README_EN.md) · [快速开始](#快速开始) · [Web 界面](#web-界面) · [指标口径](#指标口径) · [对比实验怎么做才可信](#对比实验怎么做才可信)

</div>

---

同一个模型，直连和走中转站差多少？两家网关谁的首字更快、谁半夜掉速？"感觉慢"能不能变成一张可复现的报告？

`apitest` 就是为这个做的：一条命令或一个本地网页，对任意 OpenAI 兼容 / Anthropic / Gemini 接口发起真实的流式请求，测出 **TTFT、吞吐、总耗时、成功率、卡顿**，多轮统计后给出分位数、波动和排行，并把每次结果存入历史，随时回看趋势。

<p align="center"><img src="docs/screenshots/run.png" width="880" alt="测速页：实时进度卡片、流式输出、TTFT 与吞吐曲线"></p>

## 特性

- **三类接口一套口径**：OpenAI 兼容（含各类中转 / 代理站）、Anthropic Messages、Google Gemini，按 URL 或 Key 前缀自动识别，流式与非流式都支持。
- **测得准**：TTFB、TTFT、首个可见文本 token、总耗时、输出 tok/s、流式最大卡顿、思考 tokens、结束原因（是否被 `max_tokens` 截断）。吞吐分母用首个文本 token 之后的时长，不受思考片段是否透传影响。
- **统计而不是单次**：均值 / P50 / P90 / P99 / 最小最大 / 标准差 / 变异系数；样本不足、输出长度不一致、token 为估算时自动提示"这个对比不可信"。
- **对比实验设计**：多目标默认交替轮询摊平时间漂移；可指定基线目标（如直连上游）直接算出网关开销；估算 token 的目标不参与吞吐排名。
- **负载模式**：轮次、并发、持续压测、预热、重试、请求间隔，Ctrl+C 随时中断并保留已完成部分。
- **本地 Web 界面**：端点管理（拉取模型、测试连接、一键快速测试）、实时曲线与流式输出、排行、可排序汇总、逐次明细、历史回看与对比、趋势图、连通诊断、深浅主题。
- **可留档**：历史记录含每次请求明细；导出 JSON / CSV / Markdown；`--json` 便于脚本处理。
- **连通诊断**：拆解 DNS / TCP / TLS / HTTP 各阶段延迟并记录历史，先分清是网络慢还是模型慢。

## 支持的接口

| 类型 | 说明 | 示例 |
|---|---|---|
| OpenAI 兼容 | `/v1/chat/completions`，包括 OpenAI、DeepSeek、Moonshot、OpenRouter、one-api / new-api 等中转站 | `https://api.openai.com/v1` |
| Anthropic | `/v1/messages`，支持思考片段与 usage | `https://api.anthropic.com` |
| Gemini | `generateContent` / `streamGenerateContent`，支持 thoughts | `https://generativelanguage.googleapis.com` |

## 安装

需要 Python 3.9+。推荐用 [uv](https://github.com/astral-sh/uv)：

```bash
git clone https://github.com/liningbest/apitest.git
cd apitest
uv sync
uv run apitest --help
```

或者 `pip install -e .`。下文的 `apitest` 等价于 `uv run apitest`。

## 快速开始

**30 秒测一个接口**（不写配置）：

```bash
apitest run --url https://api.openai.com/v1 --key sk-xxx -m gpt-4o-mini -n 5
```

**同一端点对比多个模型**：

```bash
apitest run --url https://api.example.com/v1 --key sk-xxx -m gpt-4o-mini,deepseek-chat -n 5 --sort tps
```

**多家网关长期对比**（推荐写配置）：

```bash
apitest init          # 生成 ./apitest.yaml，填入端点
apitest run --all     # 所有端点所有模型，交替轮询
```

```yaml
# apitest.yaml
defaults:
  rounds: 5
  max_tokens: 512

endpoints:
  openai:
    base_url: https://api.openai.com/v1
    api_key: ${OPENAI_API_KEY}            # 支持引用环境变量
    models: [gpt-4o-mini, gpt-4o]
    price: { gpt-4o-mini: [0.15, 0.60] }  # USD / 1M tokens [输入, 输出]，可选，用于估算费用
  claude:
    provider: anthropic
    base_url: https://api.anthropic.com
    api_key: ${ANTHROPIC_API_KEY}
    models: [claude-sonnet-5]
  relay:
    base_url: https://api.some-relay.com/v1
    api_key: sk-xxx
    models: [gpt-4o-mini]
    headers: { X-Custom: value }          # 可选：附加请求头
```

终端输出示例：

```
                                测速汇总  (10 次请求，用时 41.2s)
╭──┬────────────────────┬──────┬───────┬─────────────┬──────────┬─────────────┬────────┬─────────┬───────┬────────┬──────╮
│# │ 目标 / 模型        │ 成功 │  TTFB │  TTFT ±波动 │ TTFT P90 │ TTFT vs基线 │ 总耗时 │ 输出tok │ tok/s │ 稳定CV │ 卡顿 │
├──┼────────────────────┼──────┼───────┼─────────────┼──────────┼─────────────┼────────┼─────────┼───────┼────────┼──────┤
│1 │ openai/gpt-4o-mini │  5/5 │ 412ms │ 480ms ±61ms │    556ms │        基线 │  4.21s │     318 │  86.3 │    13% │ 210ms│
│2 │ relay/gpt-4o-mini  │  5/5 │ 690ms │ 812ms ±95ms │    937ms │      +332ms │  4.60s │     311 │  82.9 │    12% │ 640ms│
╰──┴────────────────────┴──────┴───────┴─────────────┴──────────┴─────────────┴────────┴─────────┴───────┴────────┴──────╯
```

## Web 界面

```bash
apitest web               # 自动打开 http://127.0.0.1:8787
apitest web --port 9000 --no-open
```

<p align="center"><img src="docs/screenshots/summary.png" width="880" alt="汇总表、排行与分布图"></p>

| 页面 | 功能 |
|---|---|
| **测速** | 勾选模型，选场景（快速检查 / 标准 / 吞吐 / 稳定性）或自定义参数，`⌘/Ctrl+Enter` 开始。实时 KPI、每目标进度卡片与迷你曲线、流式输出内容、TTFT / 吞吐曲线、平均对比、TTFT 分布；结束后有排行、可排序汇总（含与上次同目标的 Δ、基线增量，点行展开每次请求）、逐次明细、导出 |
| **端点** | 卡片式管理，改动自动保存到 `apitest.yaml`。编辑框里「测试连接」「拉取模型列表」并勾选加入；「快速测试」一键验证 Key |
| **历史** | CLI 与 Web 共用。点开任意记录看完整报告；筛选、两条对比、按目标看 TTFT / 吞吐历史趋势 |
| **模型** | 拉取端点模型，搜索、加入测试、单模型快速测速 |
| **诊断** | 多端点拆解 DNS / TCP / TLS / HTTP 延迟，历史与趋势按端点查看 |
| **设置** | 默认参数、主题、查看配置、清空历史 |

界面只监听本机 `127.0.0.1`，API Key 只存在你本地的配置文件里。图表依赖 cdnjs 上的 Chart.js，CDN 不可用时表格与卡片照常工作。

## 指标口径

| 指标 | 含义 |
|---|---|
| **TTFB** | 发出请求到收到 HTTP 响应头。握手 + 首字节，能立刻区分网络问题和模型问题 |
| **TTFT** | 发出请求到收到第一个 token（文本或思考片段） |
| **TTFT 文本** | 到第一个可见文本 token。思考片段是否透传不影响它 |
| **总耗时** | 发出请求到流结束 |
| **tok/s** | 输出 tokens ÷（总耗时 − TTFT 文本），即真正生成阶段的速度 |
| **±** | 标准差；样本少于 5 次时以 ± 和 P90 为准，不要只看均值 |
| **CV** | 标准差 / 均值，越小越稳（<15% 绿，<40% 黄） |
| **卡顿** | 流式响应相邻两个 chunk 的最长间隔，对 coding 场景很敏感 |
| **截断** | 结束原因为 `length` / `max_tokens` 的次数 |
| **估** | 接口未返回 usage，token 数按字符估算，不参与吞吐排名 |
| **vs 基线** | 相对基线目标的增量，正数表示更慢 |

## 对比实验怎么做才可信

1. **交替轮询**（默认）：第 1 轮 A、B、C 各一次，再第 2 轮……把供应商负载随时间的漂移摊平。`--order sequential` 逐个跑完；`--order parallel` 全部并行会互相抢带宽，只适合压测。
2. **设基线**：`--baseline 端点/模型` 把直连上游设为基线，报告直接给出每个网关的 TTFT / 总耗时增量。
3. **固定请求**：同一预设、同一 `max_tokens`；需要完全可复现时加 `--temperature 0`（部分模型不接受该参数）。每次输出的字数与文本哈希都会记录，输出长度差异超过 30% 会被标记为不可比。
4. **看分布不看均值**：至少 5 轮，看 P90、± 和 CV；稳定性场景用 `--duration 60 -c 4`。
5. **先诊断再测速**：`apitest ping` 能告诉你 800ms 里有多少是 TLS 握手。

```bash
apitest run -e openai -e relay -m gpt-4o-mini -n 10 --baseline openai/gpt-4o-mini --export compare.md
```

## 更多用法

```bash
apitest run -e openai --preset long -n 3            # 长输出看吞吐
apitest run -e openai --preset long-input           # 长输入看 prefill
apitest run -e relay --duration 60 -c 4 --sort ttft # 稳定性压测
apitest run -e openai --prompt "翻译成英文：今天天气很好" --system "你是翻译" --max-tokens 100
apitest run --all -n 5 -d --show-output --export result.md --export result.json
apitest run -e openai --json --no-history | jq '.summaries[0].ttft.p90'

apitest models -e relay -f gpt        # 列出模型
apitest ping -e relay -n 5            # 连通诊断
apitest ping --history -e relay       # 诊断历史
apitest history --last 10             # 测速历史
apitest history --show 20260908-1530  # 某次详情
apitest presets                       # 内置提示词预设
```

内置预设：`short`（首字延迟）、`medium`（默认）、`long`（吞吐）、`code`、`long-input`（约 3000 token 输入，测 prefill）、`json`。

## 本地验证（不需要真实 Key）

仓库自带一个同时模拟三种接口的服务器：

```bash
python tests/mock_server.py 8765 &
apitest run --url http://127.0.0.1:8765/v1 -m mock-fast,mock-slow -n 3 --no-history
apitest run --url http://127.0.0.1:8765 -p anthropic -m mock-fast -n 2 --no-history
apitest run --url http://127.0.0.1:8765 -p gemini -m gemini-mock -n 2 --no-history
```

## 配置与数据

| 项目 | 位置 |
|---|---|
| 配置文件 | `--config` → `./apitest.yaml` → `~/.config/apitest/config.yaml` → `~/.apitest/config.yaml` |
| 测速历史 | `~/.apitest/history.jsonl`（`APITEST_HISTORY` 可改） |
| 诊断历史 | `~/.apitest/ping_history.jsonl`（`APITEST_PING_HISTORY` 可改） |
| 环境变量 | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY`、`APITEST_API_KEY`、`APITEST_PROXY` |

## 项目结构

```
apitest/
├── providers.py   三类接口的请求构造与 SSE 解析
├── runner.py      异步执行：计时、并发、交替轮询、持续压测
├── metrics.py     分位数、稳定性、基线增量、可比性提示
├── report.py      终端表格、导出 JSON / CSV / Markdown
├── history.py     测速与诊断历史（JSONL）
├── netcheck.py    DNS / TCP / TLS / HTTP 分阶段诊断
├── web.py         Starlette 后端，SSE 推送实时结果
├── static/        单文件前端（无构建步骤）
└── cli.py         命令行入口
tests/mock_server.py   本地模拟三种接口
```

## 常见问题

**中转站返回的 token 数和官方不一样？** 报告里标"估"的目标说明接口没返回 usage，工具按字符估算，只能参考。两边都有 usage 但数值不同，通常是内容本身不同，看"字数"列和"截断"列。

**tok/s 高得离谱？** 说明网关把整段内容一次性返回、没有真流式。此时工具会改按总耗时计算，同时"chunk"列会是 1。

**Anthropic 报 temperature 错误？** 带思考的新模型不接受该参数，不要加 `--temperature`。

**能测 Azure OpenAI / 自建 vLLM 吗？** 任何 OpenAI 兼容接口都可以，把 `base_url` 指到 `/v1` 即可；需要额外请求头用 `headers` 或 `-H`。

## 参与

欢迎提 issue 和 PR。改动请附上 `tests/mock_server.py` 能复现的验证方式。

## 许可证

[MIT](LICENSE)

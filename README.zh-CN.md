<div align="center">

<img src="apitest/static/favicon.svg" width="72" alt="apitest">

# apitest

**API 中转站测速与监控工具：选择响应快、输出流畅的模型和推理强度。**

[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/YL13YE/apitest/actions/workflows/ci.yml/badge.svg)](https://github.com/YL13YE/apitest/actions)

[English](README.md) · [快速开始](#快速开始) · [实时监控](#实时监控) · [价格与费用](#价格与费用) · [指标口径](#指标口径)

</div>

基于 [liningbest/apitest](https://github.com/liningbest/apitest) 改造，保留原项目的多协议测速、历史导出和连通诊断，增加模型与推理强度对比、按时段持续采样、费用预算、计费倍率和模型表现看板。适合需要观察中转站高峰负载、为团队选择模型的场景，主要比较速度与流畅度，不评价答案质量或验证模型身份。

## 相比原项目的改动

| 功能 | 本 fork 的改动 |
|---|---|
| 模型表现看板 | 顶部推荐模型，随后展示速度图表、全部模型卡片、小时表现和评价依据；卡片右上角显示北京时间的最近测试时间 |
| 推理强度对比 | 提供 `low / medium / high / xhigh` 多选按钮，每个模型与档位独立统计；未指定强度的历史记录保留，但不显示强度标签 |
| 实时监控 | 默认每 60 分钟采样，支持观察 1–7 天，逐小时选择每天的监控时段，可排除低峰期 |
| 临时测速 | 一项监控与一项临时测速可以同时运行，结果、预算和停止操作独立 |
| 费用控制 | 支持导入模型定价、缓存计费、端点倍率、总预算和每运行小时预算；历史记录展示费用与不确定预留 |
| 指标修正 | 区分首个推理片段与首正文，正文速度扣除明确报告的思考用量；排除单块返回、极短分块窗口等无效速度样本 |
| 访问与凭据保护 | 远程访问强制认证、浏览器凭据遮蔽、同源与变更令牌检查、私有文件权限、凭据与目的地址绑定 |
| 前端资源 | Chart.js 随项目提供，图表脚本不依赖外部 CDN |

更详细的行为、评价算法和修复记录见 [增强版说明](LOCAL_CHANGES.md)。

## 快速开始

需要 Python 3.9+，推荐使用 [uv](https://github.com/astral-sh/uv)：

```bash
git clone https://github.com/YL13YE/apitest.git
cd apitest
uv sync
uv run apitest web
```

打开 `http://127.0.0.1:8787`，在“端点”中填写 API 地址、Key 和模型，再到“测速”勾选模型开始测试。也可使用 `python -m pip install -e .` 安装，然后直接运行 `apitest`。

查看模型表现不会发起模型请求；开始测速、预热、重试和监控会向所选服务商发送请求，并可能产生费用。

### 支持的接口

| 配置中的 `provider` | 请求接口 |
|---|---|
| `openai` | OpenAI 兼容的 Chat Completions，包括中转站和自建服务 |
| `responses` | 原生 OpenAI Responses |
| `anthropic` | Anthropic Messages |
| `gemini` | Gemini GenerateContent / StreamGenerateContent |

仅 OpenAI 兼容、Anthropic 和 Gemini 可自动识别；使用 Responses 时请显式选择。流式与非流式均可测试，但非流式无法测量正文持续输出速度。远程 API 地址要求 HTTPS，本地模拟接口可使用 HTTP。

### 页面说明

| 页面 | 用途 |
|---|---|
| 模型表现 | 查看推荐、速度图表、模型与推理强度卡片、小时表现和评价依据；默认筛选短输出，其他测试条件可单独选择 |
| 测速 | 临时测速、实时曲线、正文输出、逐次明细，以及默认收起的“实时监控”区域 |
| 端点 | 管理 API 地址、Key、模型、推理强度、计费倍率和单价；拉取模型列表、测试连接 |
| 历史 | 查看已保存结果、费用、逐次明细、趋势和两次任务对比，导出 JSON / CSV / Markdown |
| 模型 / 诊断 / 设置 | 模型发现、DNS / TCP / TLS / HTTP 分阶段诊断，以及默认参数设置 |

## 实时监控

在测速页展开“实时监控”，设置观察天数、采样间隔、每天的采样时段和金额预算，再点击“开始监控”。默认观察 1 天、每 60 分钟一轮，全天时段均选中。

- 每个小时按钮表示北京时间的一个小时区间，可逐小时取消低峰时段。
- 观察期支持 1–7 天，包含暂停时段；每天重复同一日程，错过的轮次不补发。
- 未选时段不发起新的测速、预热或重试请求；已发出的请求可正常结束。
- 必须设置总预算或每运行小时预算，并为全部选中模型提供完整单价。Web 监控不再使用手动请求次数上限，预计请求数仅用于说明计划。
- 监控固定使用启动时的模型、档位、价格、倍率和请求参数。修改表单或端点只影响新任务。
- 关闭浏览器不会停止后台监控；服务进程退出后不会自动恢复任务，已完成记录保留。

监控列表显示当前任务、采样进度、下次采样时间、费用和预计结束时间。测速结果区显示本轮用时，等待下一轮时停止累计。临时测速与监控分别启动和停止。

## 价格与费用

单价单位统一为**所填计费单位 / 百万 token**。端点“计费倍率”默认为 `1`，输入、输出、缓存读写费用均乘一次倍率；例如 `0.16` 表示按所填单价的 16% 计算。若单价已是折后价格，保持 `1`。

### 单次请求的费用

```text
费用 =（普通输入 token × 输入单价
      + 缓存读取 token × 缓存读取单价
      + 缓存写入 token × 缓存写入单价
      + 总输出 token × 输出单价）
      ÷ 1,000,000 × 计费倍率
```

输入总数包含缓存 token，普通输入为总输入减去缓存读写；总输出包含接口明确报告的思考 token，不再重复加算。单价缺失时显示“无法估算”，已配置的免费价格可显示 `0`。

例如，普通输入 169 token、缓存读取 4224 token、总输出 31 token，对应单价为 4、0.4、20，倍率为 0.16，则费用为：

```text
(169 × 4 + 4224 × 0.4 + 31 × 20) ÷ 1,000,000 × 0.16
= 0.000477696
```

`currency` 只标识计费单位，工具不自动换汇。若中转站以“美元余额”标价、充值规则为 ¥1 = $1 余额，则 0.1 个余额单位对应 ¥0.1 的充值成本，应按该站规则理解金额。

### 历史费用、监控预估与预算

| 金额 | 如何计算 |
|---|---|
| 报告用量估算 | 已完成请求返回的 token 用量，按任务启动时的单价和倍率计算 |
| 不确定预留 | 失败、取消或缺少可核对用量时保留的费用，不代表服务商已实际扣款 |
| 历史总估算费用 | 报告用量估算加不确定预留，包含预热、重试和失败请求；修改当前价格不会重算旧记录 |
| 完整监控计划预估 | 覆盖整个观察期，参考同条件历史较高输入、输出用量，输入按普通输入与缓存读写中的最高单价预留，不假定缓存优惠 |

完整计划预估通常比缓存命中后的费用高，也不会先减去你设置的预算。无同条件历史时按请求参数初估；预估页面只做本地计算，不调用模型。不同请求条件的历史用量不混用，同一模型的不同推理档位共享较高用量作为保守参考。

总预算不足以预留下一次请求时停止，每运行小时预算不足时暂停至下一运行小时。小时预算以任务启动为起点，小时数据表按北京时间自然小时汇总。工具依据服务商报告用量估算费用，不读取真实扣费账单；额外收费或超限报告可能使账单不同，严格限额还应设置服务商侧额度。

### 配置与导入定价

CLI 可参考 [配置示例](apitest.example.yaml)，或使用下面的配置。将示例模型、地址和价格替换为自己的值；推理档位需由目标模型和中转站实际支持。

```yaml
# apitest.yaml
defaults:
  prompt_preset: short
  max_tokens: 64
  concurrency: 1
  timeout: 120

endpoints:
  relay:
    provider: openai
    base_url: https://api.example.com/v1
    api_key: ${RELAY_API_KEY}
    models: [your-model-id]
    efforts: [low, medium, high, xhigh]
    currency: USD
    price_multiplier: 0.16
    price:
      your-model-id:
        input: 2
        output: 10
        cache_read: 0.2
        cache_write: 2.5
```

`${RELAY_API_KEY}` 需要在启动服务的环境中设置。Bash 下可以交互输入，避免把真实 Key 写进命令参数：

```bash
read -rs -p 'API Key: ' RELAY_API_KEY
export RELAY_API_KEY
printf '\n'
```

已有 `version: 1`、包含 `models` 列表的定价 JSON，可按模型 ID 精确匹配导入：

```bash
uv run apitest import-prices pricing.json -e relay --currency USD
```

价格字段为 `inputCostPerMillion`、`outputCostPerMillion`，以及可选的 `cacheReadCostPerMillion`、`cacheCreationCostPerMillion`。导入价作为端点默认价，手动单价优先；更换地址、协议或币种后不再沿用原价表。

## 指标口径

| 指标 | 含义 |
|---|---|
| TTFB | 请求发出到收到 HTTP 响应头 |
| TTFT | 请求发出到首个文本或推理片段 |
| 首正文 | 请求发出到首段可见正文，是看板主要延迟指标 |
| 正文 tok/s | 报告总输出扣除明确报告的思考 token，除以首个到最后一个正文分块之间的时间 |
| 最长停顿 | 相邻正文分块之间的最大间隔，不用思考心跳填补 |
| 完成率 | 非预热尝试中，有正文且未截断的完整响应占比；重试作为单独尝试统计 |
| P50 / P90 / P95 | 中位数及尾延迟；看板 P90 至少需要 20 个有效样本，P95 至少需要 100 个 |

单块返回、正文分块集中在 50 毫秒内到达、缺少可核对用量、无法区分思考用量、截断或报告输出超限的记录，不参与正文速度比较；无有效测量显示“—”，不视为速度为 0。50 毫秒是本工具的测量窗口策略，不是速度上限。

`short` 默认输出上限为 64，适合观察首正文等待。比较持续输出速度时，可选择 `medium` / `long` 并留足输出空间。GPT 的输出上限包含思考 token，不限制输入 token；接口报告输出超过请求上限时保留用量计费，并标记异常。

推荐按完成情况和速度指标排序，每个模型保留最快档位。卡片中的“好用”“流畅”“稳”等评价有不同的样本数、时延和波动要求，具体算法见 [增强版说明](LOCAL_CHANGES.md#模型表现页面)。不同提示词、输出上限、缓存条件、协议和档位分别统计，小样本或单轮结果不代表长期稳定性。

## 常用命令

下列命令使用已配置的 `relay` 端点，真正测速和监控会产生请求。示例预算仅用于演示，单位与端点一致。

```bash
# 先检查 7 天、每小时一轮的计划，不调用模型
uv run apitest run -e relay --hours 168 --sample-minutes 60 --budget 10 --plan

# 去掉 --plan 开始采样；Web 界面还支持选择每天的监控时段
uv run apitest run -e relay --hours 168 --sample-minutes 60 --budget 10

# 临时测速与导出
uv run apitest run -e relay --preset medium --max-tokens 512 -n 3 --export results/compare.json
uv run apitest history --last 10
uv run apitest models -e relay
uv run apitest ping -e relay -n 3
uv run apitest presets
```

## 分享看板

可将指定端点的测速指标导出为静态看板，再由独立脚本自动提交到展示仓库。配置与用法见 [独立仓库分享说明](docs/sharing.md)。

## 远程访问

默认只监听 `127.0.0.1:8787`。需要从其他机器访问时：

```bash
uv run apitest web --host 0.0.0.0 --port 8787 --no-open
```

非回环监听强制 HTTP Basic 认证，用户名为 `apitest`。首次启动会生成密码文件 `~/.apitest/web-password`，也可通过 `APITEST_WEB_PASSWORD` 或 `--password-file` 提供至少 16 个字符的密码。密码文件由服务端账号读取，权限为 `0600`。

公网访问应配置 HTTPS，可使用反向代理或 `--ssl-certfile` / `--ssl-keyfile`；反向代理需保留浏览器访问使用的 Host。完整服务包含 Key 配置、任务管理和历史明细，只向受信任的管理员开放。

服务端把 Key 发送给你选择的 API 服务商进行认证；正常配置读取向浏览器返回占位符。配置与历史采用私有文件权限，请求不自动跟随重定向，图表脚本由本地提供。不要提交真实配置、Key、密码或原始测试记录。

## 配置与数据位置

| 项目 | 位置 |
|---|---|
| 配置 | `--config` → `./apitest.yaml` / `./apitest.yml` → `~/.config/apitest/config.yaml` → `~/.apitest/config.yaml` |
| 测速历史 | `~/.apitest/history.jsonl`，可通过 `APITEST_HISTORY` 修改 |
| 诊断历史 | `~/.apitest/ping_history.jsonl`，可通过 `APITEST_PING_HISTORY` 修改 |
| Web 密码 | `~/.apitest/web-password`，或通过启动参数、环境变量提供 |

## 本地验证

```bash
uv run python -m unittest discover -s tests -v
node tests/test_model_picks.cjs
node tests/test_round_timing.cjs
```

也可启动本地模拟服务，在另一个终端执行测速，不需要真实 Key：

```bash
uv run python tests/mock_server.py 8765
```

```bash
uv run apitest run --url http://127.0.0.1:8765/v1 --key mock-key -m mock-fast -n 1 --no-history
```

## 致谢与许可证

原项目：[liningbest/apitest](https://github.com/liningbest/apitest)，本 fork 基于提交 `dcfcc4b` 开发。感谢原作者提供的测速、界面与多协议基础。

采用 [MIT 许可证](LICENSE)，保留原项目版权声明。

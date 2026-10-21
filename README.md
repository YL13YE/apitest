<div align="center">

<img src="apitest/static/favicon.svg" width="72" alt="apitest">

# apitest

**Benchmark and monitor API relays to find responsive models and fluent output.**

[![Python](https://img.shields.io/badge/python-3.9%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![CI](https://github.com/YL13YE/apitest/actions/workflows/ci.yml/badge.svg)](https://github.com/YL13YE/apitest/actions)

[简体中文](README.zh-CN.md) · [Quick start](#quick-start) · [Monitoring](#monitoring) · [Pricing and costs](#pricing-and-costs) · [Metrics](#metrics)

</div>

A fork of [liningbest/apitest](https://github.com/liningbest/apitest) for comparing model speed and reasoning efforts during the hours that matter. It retains the original multi-provider benchmarks, history exports and network diagnostics, and adds scheduled sampling, spending controls, billing multipliers and a model dashboard. It measures response speed and fluency, rather than answer quality or model identity.

## Changes from upstream

| Area | Changes in this fork |
|---|---|
| Model dashboard | Recommendations, speed charts, model cards, hourly observations and assessment criteria; cards show the latest test time in Beijing time |
| Reasoning efforts | Multi-select buttons for `low / medium / high / xhigh`; each model and effort is measured separately; unspecified efforts remain visible without an effort label |
| Monitoring | Sample every 60 minutes by default, observe for 1–7 days and select active hours to exclude off-peak periods |
| Temporary tests | One monitor and one temporary benchmark can run together, with separate results, budgets and stop controls |
| Cost controls | Import model prices, account for cache usage, apply endpoint billing multipliers and enforce total or hourly budgets; history preserves cost estimates and uncertain reservations |
| Measurement validity | Separate reasoning from visible output latency and usage; exclude single-chunk responses and unreliable short output windows from speed comparisons |
| Access and credentials | Authentication for remote access, masked credentials, same-origin and mutation-token checks, private file permissions and destination-bound credentials |
| Frontend assets | Chart.js is bundled locally; chart scripts do not require a third-party CDN |

See [the detailed enhancement notes](LOCAL_CHANGES.md) for implementation behavior, assessment rules and fixes. These notes are in Chinese.

## Quick start

Requires Python 3.9+. Using [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/YL13YE/apitest.git
cd apitest
uv sync
uv run apitest web
```

Open `http://127.0.0.1:8787`, add an endpoint, API key and models, then select models on the Run page. Alternatively, install with `python -m pip install -e .` and run `apitest` directly.

Viewing the dashboard does not call models. Benchmarks, warmups, retries and monitoring send requests to the selected provider and may incur charges.

### Supported APIs

| `provider` | API |
|---|---|
| `openai` | OpenAI-compatible Chat Completions, including relays and self-hosted services |
| `responses` | Native OpenAI Responses |
| `anthropic` | Anthropic Messages |
| `gemini` | Gemini GenerateContent / StreamGenerateContent |

Automatic detection covers OpenAI-compatible, Anthropic and Gemini APIs. Select Responses explicitly. Both streaming and non-streaming requests are supported, but continuous text throughput requires streaming. Remote API endpoints require HTTPS; local mock endpoints may use HTTP.

### Web pages

| Page | Purpose |
|---|---|
| Model dashboard | Recommendations, charts, model and effort cards, hourly observations and assessment criteria; defaults to short output, with other test conditions available individually |
| Run | Temporary benchmarks, live charts, output, request details and the monitoring panel, which is collapsed by default |
| Endpoints | API addresses, keys, models, reasoning efforts, billing multipliers and prices; connection checks and model discovery |
| History | Saved results, costs, request details, trends, comparisons and JSON / CSV / Markdown exports |
| Models / Diagnostics / Settings | Model discovery, DNS / TCP / TLS / HTTP timing and default settings |

## Monitoring

Expand the monitoring panel on the Run page, choose the observation length, sampling interval, daily active hours and budget, then start monitoring. Defaults are one day, one round every 60 minutes and all daily hours selected.

- Hour buttons represent Beijing time (`Asia/Shanghai`). Deselect hours that do not need testing.
- Observe for 1–7 days, including inactive hours. The daily schedule repeats; missed rounds are not replayed.
- No new benchmark, warmup or retry requests start outside active hours. Requests already sent can finish.
- Web monitoring requires a total or hourly monetary budget and complete prices for all selected models. Its estimated request count describes the plan; it is not a manual request cap.
- Each task retains the models, efforts, prices, multipliers and request settings selected at startup. Later configuration edits apply to new tasks.
- Closing the browser leaves monitoring running. Exiting the service does not automatically resume it later; completed requests remain in history.

The panel shows active tasks, progress, the next sample time, costs and expected completion. The live result view measures the current round and freezes that timer while waiting. Temporary benchmarks and monitoring have independent start and stop controls.

## Pricing and costs

All rates use **the configured billing unit per million tokens**. The endpoint billing multiplier defaults to `1` and applies once to input, output, cache reads and cache writes. Set `0.16` to charge 16% of the configured rates. Leave it at `1` when prices already include the discount.

### Per-request calculation

```text
Cost = (uncached input tokens × input rate
      + cache read tokens × cache read rate
      + cache write tokens × cache write rate
      + total output tokens × output rate)
      / 1,000,000 × billing multiplier
```

Total input includes cached tokens; uncached input subtracts cache reads and writes. Total output includes explicitly reported reasoning tokens, which are not added a second time. Missing prices remain unknown; explicitly configured free prices may produce a valid zero cost.

For example, 169 uncached input tokens, 4224 cache read tokens and 31 output tokens, at rates of 4, 0.4 and 20 with a multiplier of 0.16, cost:

```text
(169 × 4 + 4224 × 0.4 + 31 × 20) / 1,000,000 × 0.16
= 0.000477696
```

`currency` labels the billing unit; the tool does not perform currency conversion. If a relay sells one dollar-denominated balance unit for ¥1, an estimate of 0.1 balance units corresponds to ¥0.1 of recharge cost under that rule.

### History, plan estimates and budgets

| Amount | Meaning |
|---|---|
| Reported-usage estimate | Completed requests priced using reported token usage and the rates and multiplier selected at task startup |
| Uncertain reservation | Retained allowance for failed, cancelled or unverifiable requests; it does not prove that the provider charged this amount |
| Historical total estimate | Reported-usage estimates plus uncertain reservations, including warmups, retries and failures; current prices do not reprice old records |
| Full monitoring plan estimate | A conservative estimate for the entire observation period, using higher comparable historical input and output counts and the highest input/cache rate, without assuming cache discounts |

The full-plan estimate can be much higher than costs after cache hits, and is shown before applying the budget. Without comparable history it uses request settings. Previewing the plan does not call models. Different request conditions are kept separate; efforts for the same model share higher observed usage as a conservative reference.

A task stops when the total budget cannot reserve another request. An exhausted hourly budget pauses sampling until the next running-hour allowance. Budget hours start at task startup; hourly reports use Beijing clock hours. Estimates use provider-reported usage rather than actual billing records. Extra charges or usage beyond requested limits can change the bill; strict limits also require a provider-side spending cap.

### Configuration and price imports

Use [the example configuration](apitest.example.yaml), or adapt the following. Replace the model, URL and prices with your own values. Reasoning efforts must be supported by the selected model and relay.

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

Set `RELAY_API_KEY` in the environment before starting the service. In Bash, read it interactively instead of placing the real key in command arguments:

```bash
read -rs -p 'API Key: ' RELAY_API_KEY
export RELAY_API_KEY
printf '\n'
```

Import a pricing JSON with `version: 1` and a `models` array using exact model IDs:

```bash
uv run apitest import-prices pricing.json -e relay --currency USD
```

Supported price fields are `inputCostPerMillion`, `outputCostPerMillion`, and optional `cacheReadCostPerMillion` and `cacheCreationCostPerMillion`. Imported rates are endpoint defaults; manual model prices take precedence. Changing the destination, protocol or currency stops applying that catalog.

## Metrics

| Metric | Meaning |
|---|---|
| TTFB | Request sent to HTTP response headers |
| TTFT | Request sent to the first text or reasoning chunk |
| First visible text | Request sent to the first visible text chunk; the dashboard's primary latency metric |
| Text tok/s | Reported output minus explicitly reported reasoning tokens, divided by the interval between the first and last visible text chunks |
| Longest stall | Maximum gap between visible text chunks; reasoning heartbeats do not fill those gaps |
| Completion rate | Non-warmup attempts with visible output and no truncation; retries count as separate attempts |
| P50 / P90 / P95 | Median and tail latency; dashboard P90 requires 20 usable samples and P95 requires 100 |

Single-chunk output, output windows shorter than 50 ms, missing verifiable usage, inseparable reasoning usage, truncation and reported output beyond the requested cap are excluded from text-speed comparisons. Missing measurements appear as a dash rather than zero speed. The 50 ms window is a measurement policy, not a speed ceiling.

The `short` preset defaults to an output cap of 64 and is useful for first-text latency. Use `medium` / `long` and sufficient output space for sustained throughput. GPT output caps include reasoning tokens and do not limit input tokens. Reported output above the requested cap is flagged and retained for cost estimation.

Recommendations use completion and speed metrics, retaining the fastest effort for each model. Card assessments have different sample, latency and variability requirements; see [the detailed criteria](LOCAL_CHANGES.md#模型表现页面). Prompts, output limits, cache conditions, protocols and efforts are measured separately. A small sample or a single round does not establish long-term reliability.

## Common commands

These examples use a configured `relay` endpoint. Actual benchmarks and monitoring send requests. The example budget is illustrative and uses the endpoint's billing unit.

```bash
# Inspect a seven-day, hourly plan without calling models
uv run apitest run -e relay --hours 168 --sample-minutes 60 --budget 10 --plan

# Remove --plan to start sampling; the web UI also provides daily active hours
uv run apitest run -e relay --hours 168 --sample-minutes 60 --budget 10

# Temporary benchmark and export
uv run apitest run -e relay --preset medium --max-tokens 512 -n 3 --export results/compare.json
uv run apitest history --last 10
uv run apitest models -e relay
uv run apitest ping -e relay -n 3
uv run apitest presets
```

## Public dashboard

Export a selected endpoint as a static dashboard and use the separate publishing script to commit changed public metrics to a display repository. See [the sharing guide](docs/sharing.md) (Chinese) for setup and automatic updates.

## Remote access

The default listener is `127.0.0.1:8787`. To listen on all interfaces:

```bash
uv run apitest web --host 0.0.0.0 --port 8787 --no-open
```

Non-loopback listeners require HTTP Basic authentication with username `apitest`. The first launch creates `~/.apitest/web-password`, or you can supply a password of at least 16 characters through `APITEST_WEB_PASSWORD` or `--password-file`. The password file is owner-readable with mode `0600`.

Use HTTPS for public access, through a reverse proxy or `--ssl-certfile` / `--ssl-keyfile`. A reverse proxy must preserve the Host used by the browser. The full service includes key configuration, task controls and request history; restrict access to trusted administrators.

The server sends API credentials to your selected provider for authentication. Normal configuration reads return masked placeholders to the browser. Configuration and history use private file permissions, authenticated API requests do not automatically follow redirects, and chart scripts are served locally. Keep real configuration, credentials, passwords and raw test history out of Git.

## Data locations

| Item | Location |
|---|---|
| Configuration | `--config` → `./apitest.yaml` / `./apitest.yml` → `~/.config/apitest/config.yaml` → `~/.apitest/config.yaml` |
| Benchmark history | `~/.apitest/history.jsonl`, override with `APITEST_HISTORY` |
| Diagnostic history | `~/.apitest/ping_history.jsonl`, override with `APITEST_PING_HISTORY` |
| Web password | `~/.apitest/web-password`, or supply it with the startup option/environment variable |

## Local validation

```bash
uv run python -m unittest discover -s tests -v
node tests/test_model_picks.cjs
node tests/test_round_timing.cjs
```

Start the mock API server, then run the benchmark in a second terminal. No real key is required:

```bash
uv run python tests/mock_server.py 8765
```

```bash
uv run apitest run --url http://127.0.0.1:8765/v1 --key mock-key -m mock-fast -n 1 --no-history
```

## Credits and license

Upstream: [liningbest/apitest](https://github.com/liningbest/apitest). This fork builds on commit `dcfcc4b`; thanks to the original author for the benchmark, UI and multi-provider foundations.

[MIT](LICENSE), retaining the original copyright notice.

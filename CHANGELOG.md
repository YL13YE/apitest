# Changelog

## 0.1.0 — 2026-09-14

First public release.

- OpenAI-compatible, Anthropic and Gemini endpoints; streaming and non-streaming
- Metrics: TTFB, TTFT, TTFT to first text token, total time, tok/s, stalls, reasoning tokens, stop reason, cost estimate
- Statistics: mean / P50 / P90 / P99 / std / CV, comparability warnings
- Interleaved multi-target execution, baseline deltas, concurrency and soak tests, warmup, retries
- Terminal live board and summary tables; JSON / CSV / Markdown export
- Local web UI: endpoint management, live charts, ranking, history with comparison and trends, diagnostics, replay deep links
- Web UI in English, 简体中文 and 繁體中文, chosen from the browser language
- Connectivity diagnostics (DNS / TCP / TLS / HTTP) with history
- Mock server for testing without an API key

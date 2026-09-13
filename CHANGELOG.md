# Changelog

## 0.1.0 — 2026-09-13

首个公开版本。

- 支持 OpenAI 兼容 / Anthropic / Gemini 三类接口，流式与非流式
- 指标：TTFB、TTFT、TTFT 文本、总耗时、tok/s、卡顿、思考 tokens、结束原因、费用估算
- 统计：均值 / P50 / P90 / P99 / 标准差 / CV，可比性提示
- 多目标交替轮询、基线增量、并发与持续压测、预热、重试
- 终端实时看板与汇总表，导出 JSON / CSV / Markdown
- 本地 Web 界面：端点管理、实时曲线、排行、历史与对比、趋势、诊断
- 连通诊断（DNS / TCP / TLS / HTTP）及其历史
- 本地模拟服务器，便于无 Key 验证

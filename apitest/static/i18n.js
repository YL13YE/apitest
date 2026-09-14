/* apitest 前端多语言：简体中文为源语言；英文用字典 + 规则，繁体用词组 + 字符映射。
   页面加载时按 ?lang= / 用户选择 / 浏览器语言自动选择，DOM 变化时自动翻译新内容。 */
(function () {
  const TW_CHARS = {"与": "與", "专": "專", "两": "兩", "个": "個", "为": "為", "义": "義", "乐": "樂", "书": "書", "于": "於", "产": "產", "仅": "僅", "从": "從", "价": "價", "会": "會", "传": "傳", "体": "體", "余": "餘", "侧": "側", "关": "關", "内": "內", "写": "寫", "军": "軍", "准": "準", "击": "擊", "则": "則", "创": "創", "删": "刪", "别": "別", "务": "務", "动": "動", "势": "勢", "单": "單", "历": "歷", "压": "壓", "参": "參", "发": "發", "变": "變", "号": "號", "后": "後", "启": "啟", "响": "響", "围": "圍", "图": "圖", "场": "場", "处": "處", "复": "復", "头": "頭", "学": "學", "实": "實", "宽": "寬", "对": "對", "导": "導", "将": "將", "带": "帶", "并": "並", "应": "應", "开": "開", "异": "異", "录": "錄", "态": "態", "总": "總", "执": "執", "扩": "擴", "抢": "搶", "报": "報", "拟": "擬", "择": "擇", "换": "換", "据": "據", "摊": "攤", "数": "數", "断": "斷", "无": "無", "旧": "舊", "时": "時", "显": "顯", "暂": "暫", "术": "術", "机": "機", "条": "條", "来": "來", "极": "極", "构": "構", "标": "標", "样": "樣", "检": "檢", "汇": "匯", "没": "沒", "浅": "淺", "测": "測", "游": "遊", "滤": "濾", "点": "點", "热": "熱", "状": "狀", "环": "環", "画": "畫", "盖": "蓋", "码": "碼", "确": "確", "种": "種", "称": "稱", "稳": "穩", "筛": "篩", "签": "籤", "类": "類", "约": "約", "级": "級", "线": "線", "细": "細", "终": "終", "绍": "紹", "经": "經", "结": "結", "给": "給", "络": "絡", "统": "統", "续": "續", "维": "維", "编": "編", "网": "網", "脚": "腳", "范": "範", "见": "見", "观": "觀", "览": "覽", "计": "計", "认": "認", "记": "記", "论": "論", "设": "設", "证": "證", "识": "識", "诊": "診", "词": "詞", "试": "試", "话": "話", "诞": "誕", "询": "詢", "该": "該", "语": "語", "误": "誤", "说": "說", "请": "請", "读": "讀", "调": "調", "负": "負", "败": "敗", "质": "質", "费": "費", "趋": "趨", "转": "轉", "轮": "輪", "载": "載", "较": "較", "辑": "輯", "输": "輸", "边": "邊", "达": "達", "过": "過", "还": "還", "这": "這", "进": "進", "连": "連", "迟": "遲", "选": "選", "邻": "鄰", "释": "釋", "里": "裡", "销": "銷", "错": "錯", "键": "鍵", "长": "長", "门": "門", "闭": "閉", "间": "間", "阶": "階", "随": "隨", "隐": "隱", "韩": "韓", "顺": "順", "须": "須", "顾": "顧", "顿": "頓", "预": "預", "领": "領", "风": "風", "验": "驗"};
  const TW_PHRASES = {"服务器": "伺服器", "网关": "閘道", "字符": "字元", "数据": "資料", "设置": "設定", "刷新": "重新整理", "搜索": "搜尋", "默认": "預設", "文件": "檔案", "网络": "網路", "信息": "資訊", "软件": "軟體", "界面": "介面", "支持": "支援", "用户": "使用者", "加载": "載入", "程序": "程式", "参数": "參數", "配置": "設定", "质量": "品質", "优化": "最佳化", "端口": "連接埠", "日志": "日誌", "变量": "變數", "脚本": "指令碼", "终端": "終端機", "鉴权": "驗證"};
  const LANGS = { 'zh-CN': '简体中文', 'zh-TW': '繁體中文', 'en': 'English' };

  const EN = {
    exact: {
      // 导航 / 通用
      '测速': 'Run', '端点': 'Endpoints', '历史': 'History', '模型': 'Models', '诊断': 'Diagnostics', '设置': 'Settings',
      '测速中': 'Running', '深色': 'Dark', '浅色': 'Light', '未创建配置文件': 'No config file yet', '确认': 'Confirm', '确定': 'OK', '取消': 'Cancel',
      '保存': 'Save', '删除': 'Delete', '编辑': 'Edit', '复制': 'Duplicate', '刷新': 'Refresh', '清空': 'Clear', '全选': 'Select all', '关闭': 'Close', '× 关闭': '× Close',
      '显示': 'Show', '隐藏': 'Hide', '可选': 'Optional', '无': 'None', '是': 'Yes', '否': 'No', '完成': 'Done', '失败': 'Failed', '成功': 'OK', '进行中': 'Running', '已结束': 'Finished',
      'apitest · AI API 测速': 'apitest · AI API benchmark',
      '首字延迟 TTFT': 'Time to first token', '输出吞吐': 'Output throughput', '第 N 次请求': 'Request #', '平均对比': 'Averages', 'TTFT 分布 (min / P50 / P90 / max)': 'TTFT distribution (min / P50 / P90 / max)', 'TTFT 均值 (ms)': 'TTFT mean (ms)', '吞吐 (tok/s)': 'Throughput (tok/s)', '简体中文': '简体中文',
      // 测速页
      '选择模型与参数，实时观察首字延迟、吞吐与稳定性': 'Pick models and parameters, watch TTFT, throughput and stability in real time',
      '快速检查': 'Quick check', '标准': 'Standard', '吞吐': 'Throughput', '稳定性': 'Stability',
      '1 轮短输出，快速验证 Key 与连通': '1 round of short output to verify the key and connectivity', '3 轮中等输出': '3 rounds of medium output',
      '长输出，看稳定吞吐': 'Long output to measure sustained throughput', '持续 60 秒，并发 4，看 P90 / 抖动': '60-second soak at concurrency 4 for P90 / jitter',
      '1 轮短输出，验证 Key 与连通': '1 round of short output to verify key and connectivity', '3 轮中等输出，兼顾首字与吞吐': '3 rounds of medium output, balances TTFT and throughput',
      '长输出，看稳定吞吐 tok/s': 'Long output for sustained tok/s', '持续 60 秒 · 并发 4，看 P90 / CV / 卡顿': '60-second soak · concurrency 4, for P90 / CV / stalls',
      '开始测速': 'Start', '停止': 'Stop', '测试目标': 'Targets', '搜索模型…': 'Search models…', '管理端点 →': 'Manage endpoints →', '未选择模型': 'No model selected',
      '参数': 'Parameters', '提示词预设': 'Prompt preset', '轮次 / 模型': 'Rounds / model', '并发': 'Concurrency', '按预设': 'per preset',
      '多目标执行顺序': 'Execution order', '交替轮询': 'Interleaved', '逐个目标': 'Sequential', '全部并行': 'Parallel',
      '第 1 轮 A、B、C 各一次，再第 2 轮…把供应商负载随时间的漂移摊平': 'Round 1 hits A, B, C once each, then round 2… cancels provider load drift over time',
      'A 全部跑完再跑 B': 'Finish all of A, then B', '所有目标同时发请求，会互相抢带宽': 'All targets at once; they compete for bandwidth',
      '基线目标': 'Baseline target', '（如直连上游，用于算网关开销）': '(e.g. direct upstream, to measure gateway overhead)', '不设基线': 'No baseline',
      '高级参数': 'Advanced', '持续压测 (秒)': 'Soak duration (s)', '按轮次': 'use rounds', '预热次数': 'Warmup', '失败重试': 'Retries', '超时 (秒)': 'Timeout (s)',
      '请求间隔 (秒)': 'Interval (s)', '不设置': 'unset', '代理': 'Proxy', '标签': 'Tag', '写入历史': 'saved with history', '流式': 'Streaming', '目标并行': 'Parallel targets',
      '忽略证书': 'Skip TLS verify', '不写入历史': 'Do not save to history', '自定义提示词': 'Custom prompt', '（留空使用预设）': '(blank = preset prompt)', 'System 提示词': 'System prompt',
      '还没有结果': 'No results yet', '勾选左侧模型，选择场景后点击「开始测速」，这里会实时显示进度、曲线与排行': 'Select models on the left, pick a scenario and press Start. Progress, charts and ranking will show here in real time.',
      '进度': 'Progress', '实时输出': 'Live output', '流式响应内容': 'streamed response text', '等待首个 token…': 'Waiting for the first token…',
      '汇总': 'Summary', '点击列头排序 · 点击行展开每次请求 · Δ 为与上一次同目标测速的对比': 'Click a header to sort · click a row to expand requests · Δ compares with the previous run of the same target',
      'TTFT=首个 token（文本或思考）· TTFT 文本=首个可见文本 token · tok/s=输出 tokens ÷ 首个文本 token 之后的时长 · ±=标准差 · CV=标准差/均值 · 卡顿=流式相邻 chunk 最长间隔 · 估=token 数为估算，不参与吞吐排名 · vs 基线=相对基线目标的增量（正数=更慢）· ↻n=经重试':
        'TTFT = first token (text or reasoning) · TTFT text = first visible text token · tok/s = output tokens ÷ time after first text token · ± = std dev · CV = std / mean · stall = longest gap between stream chunks · est = token count estimated, excluded from throughput ranking · vs baseline = delta against baseline (positive = slower) · ↻n = retried',
      '逐次明细': 'Requests', '只看失败': 'Failures only', '搜索目标 / 内容…': 'Search target / text…',
      '总请求': 'Requests', '成功率': 'Success rate', '平均 TTFT': 'Avg TTFT', '首个 token': 'first token', '平均吞吐': 'Avg throughput', '用时': 'Elapsed',
      'TTFB': 'TTFB', '文本': 'text', '总耗时': 'Total', '输出': 'Output', '卡顿': 'Stall', '思考': 'Reasoning', '估': 'est', '基线': 'baseline',
      '最快首字': 'Fastest TTFT', '最高吞吐': 'Highest throughput', '最短总耗时': 'Shortest total', '最稳定': 'Most stable', '最可靠': 'Most reliable',
      '目标 / 模型': 'Target / model', 'TTFT 文本': 'TTFT text', 'TTFT vs 基线': 'TTFT vs baseline', '总 P90': 'Total P90', '总 vs 基线': 'Total vs baseline',
      '输出 tok': 'Output tok', '思考 tok': 'Reasoning tok', '字数': 'Chars', '截断': 'Truncated', 'tok/s 范围': 'tok/s range', '稳定 CV': 'CV', '费用': 'Cost', '错误汇总': 'Errors',
      '状态': 'Status', '输入': 'Input', '结束': 'Stop', '输出预览 / 错误': 'Output preview / error', '输出 / 错误': 'Output / error', '点击查看完整内容': 'Click to view full text',
      '无匹配记录': 'No matching records', '完整内容': 'Full text', '复制 Markdown': 'Copy Markdown', '已复制 Markdown 报告': 'Markdown report copied',
      '已停止（展示已完成部分）': 'Stopped (partial results)', '请先勾选至少一个模型': 'Select at least one model first', '已有测速在进行中': 'A run is already in progress',
      '该记录没有逐次明细，无法回放': 'This record has no per-request details to replay', '与上次对比': 'vs last run',
      // 端点页
      '管理 API 服务与模型，改动自动保存到配置文件': 'Manage API services and models; changes are saved to the config file automatically',
      '从示例导入': 'Import samples', '添加端点': 'Add endpoint', '还没有端点': 'No endpoints yet',
      '点击右上角「添加端点」，填入 Base URL 与 Key，即可拉取模型开始测速': 'Click “Add endpoint”, fill in the Base URL and key, then fetch models and start testing',
      '（默认）': '(default)', '无模型': 'No models', '快速测试': 'Quick test', '拉取模型': 'Fetch models', '该端点没有模型': 'This endpoint has no models',
      '删除端点': 'Delete endpoint', '已删除': 'Deleted', '会把示例中的 openai / anthropic / gemini 端点加入（不覆盖同名端点）。': 'Adds the sample openai / anthropic / gemini endpoints (existing names are kept).',
      '已导入示例端点，请填入 Key': 'Sample endpoints imported, fill in the keys', '已保存到配置文件': 'Saved to config file',
      '名称': 'Name', '如 openai / relay': 'e.g. openai / relay', 'API 风格': 'API style', '自动识别': 'Auto-detect', 'OpenAI 兼容': 'OpenAI-compatible',
      '可写 ${ENV_VAR} 引用环境变量': 'use ${ENV_VAR} to reference an environment variable', '测试连接': 'Test connection', '测试中…': 'Testing…', '（Key 可能无效）': '(key may be invalid)',
      '逗号或换行分隔': 'comma or newline separated', '拉取模型列表': 'Fetch model list', '拉取中…': 'Fetching…', '过滤…': 'Filter…', '无匹配': 'No match', '高级': 'Advanced',
      '附加请求头 JSON': 'Extra headers (JSON)', '附加请求体 JSON': 'Extra body (JSON)', '价格 JSON': 'Pricing (JSON)', 'USD / 1M tokens，[输入, 输出]': 'USD per 1M tokens, [input, output]',
      '名称不能为空': 'Name is required', '名称已存在': 'Name already exists', 'Base URL 不能为空': 'Base URL is required', '请先填写 Base URL': 'Fill in the Base URL first',
      '请求头': 'headers', '请求体': 'body', '价格': 'pricing',
      // 历史页
      '所有测速记录（CLI 与 Web 共用），可对比与查看趋势': 'All runs (shared by CLI and web); compare runs and view trends',
      '按标签 / 目标筛选…': 'Filter by tag / target…', '趋势': 'Trend', '暂无数据': 'No data', '时间': 'Time', '预设': 'Preset', '目标': 'Targets', '请求': 'Requests',
      '最快 TTFT': 'Best TTFT', '最高 tok/s': 'Best tok/s', '自定义': 'custom', '暂无历史记录': 'No history yet', '删除记录': 'Delete record', '确定删除这条测速记录？': 'Delete this run?',
      '最多选择 2 条进行对比': 'Select at most 2 runs to compare', '点击行展开每次请求': 'Click a row to expand its requests',
      '该记录由旧版本生成，没有保存逐次明细与实时曲线。': 'This record was created by an older version and has no per-request details or curves.',
      '对比': 'Compare', '该目标暂无成功记录': 'No successful runs for this target yet', '中途停止': 'stopped early',
      // 模型页
      '从端点拉取可用模型，一键加入测试或快速测速': 'Fetch available models from an endpoint, add them to tests or run a quick test',
      '搜索…': 'Search…', '模型列表': 'Model list', '选择端点后点击「拉取模型列表」': 'Pick an endpoint and click “Fetch model list”',
      '模型 ID': 'Model ID', '名称 / 所有者': 'Name / owner', '创建': 'Created', '快速测速结果': 'Quick test result', '快速测速': 'Quick test', '已在列表': 'In list',
      '加入测试': 'Add to tests', '请先添加端点': 'Add an endpoint first', '无匹配模型': 'No matching models',
      // 诊断页
      '连通诊断': 'Diagnostics', '拆解 DNS / TCP / TLS / HTTP 各阶段延迟，判断慢在网络还是网关': 'Break latency into DNS / TCP / TLS / HTTP phases to tell network from gateway',
      '次数': 'Rounds', '开始诊断': 'Start', '暂无端点': 'No endpoints', '请选择端点': 'Select an endpoint', '诊断中…': 'Diagnosing…',
      '主机': 'Host', '耗时': 'Time', '服务器': 'Server', '均值': 'Mean', '诊断历史': 'Ping history', '全部端点': 'All endpoints', '清空全部': 'Clear all',
      '暂无诊断历史': 'No ping history yet', '各端点 HTTP 耗时趋势 (ms)': 'HTTP latency by endpoint (ms)', 'HTTP 均值': 'HTTP avg', 'HTTP 最大': 'HTTP max', '来源': 'Source',
      '确定删除这条诊断记录？': 'Delete this record?', '清空诊断历史': 'Clear ping history', '确定删除全部诊断记录？': 'Delete all ping records?', '已清空': 'Cleared',
      'HTTP(models) = 对 /models 接口完整 GET 一次的耗时（含鉴权），可视为该端点的网络 + 网关基础延迟。': 'HTTP(models) = one full GET of the /models endpoint (with auth); treat it as the endpoint’s base network + gateway latency.',
      // 设置页
      '默认参数、外观与数据': 'Defaults, appearance and data', '默认参数': 'Defaults', '写入配置文件 defaults，CLI 也生效': 'Written to the config file; applies to the CLI as well',
      '轮次': 'Rounds', '默认预设': 'Default preset', '默认 System 提示词': 'Default system prompt', '保存默认参数': 'Save defaults', '已保存默认参数': 'Defaults saved',
      '外观': 'Appearance', '跟随系统': 'System', '数据': 'Data', '配置文件：': 'Config file: ', '历史记录：': 'History: ', '查看配置文件': 'View config file',
      '下载示例配置': 'Download sample config', '清空历史': 'Clear history', '命令行': 'Command line', '同一套配置也可以在终端使用：': 'The same config also works in the terminal:',
      '测所有端点': 'test all endpoints', '导出': 'export', '脚本处理': 'scripting', '（未创建，保存时生成 ./apitest.yaml）': '(not created yet; saved as ./apitest.yaml)',
      '确定删除全部历史测速记录？不可恢复。': 'Delete all run history? This cannot be undone.', '历史已清空': 'History cleared', '配置文件': 'Config file',
      // 预设说明（来自后端）
      '极短输出，主要看首字延迟（TTFT）': 'Very short output, mainly for TTFT', '中等输出（默认），兼顾首字延迟与生成速度': 'Medium output (default), balances TTFT and generation speed',
      '长输出，主要看稳定吞吐（tok/s）': 'Long output, mainly for sustained tok/s', '代码生成场景': 'Code generation', '长输入（约 3000 tokens）短输出，测试 prefill 速度': 'Long input (~3000 tokens), short output, measures prefill speed',
      '结构化 JSON 输出': 'Structured JSON output',
      // 后端提示
      '样本少于 5 次，均值差异可能只是波动，请看 ± 与 P90': 'Fewer than 5 samples; mean differences may be noise, check ± and P90',
      '空响应（无任何内容）': 'Empty response (no content)', '标准差': 'std dev', '显示最近 N 条': 'show last N', '测所有端点 ·': 'test all endpoints ·', '，脚本处理': ', scripting', '。': '.', '，': ',', '：': ':',
      'TTFT=首个 token 延迟 · tok/s=输出 tokens ÷ 首字后的生成时长 · CV=标准差/均值(越小越稳) · 卡顿=流式相邻 chunk 最长间隔 · ~=token 数为估算 · ↻n=经重试的请求数': 'TTFT = first token latency · tok/s = output tokens ÷ generation time after first token · CV = std / mean (lower is steadier) · stall = longest gap between stream chunks · ~ = estimated tokens · ↻n = retried requests', '请至少选择一个模型': 'Select at least one model', '记录不存在': 'Record not found', 'run 不存在': 'Run not found',
    },
    rules: [
      [/^已选 (\d+) 个模型$/, '$1 models selected'], [/^已选 (\d+) \/ (\d+)$/, '$1 / $2 selected'], [/^(\d+) 个模型$/, '$1 models'], [/^(\d+) 个目标$/, '$1 targets'],
      [/^(\d+) 成功 \/ (\d+) 失败$/, '$1 ok / $2 failed'], [/^已完成 (\d+) 次请求$/, '$1 requests done'], [/^运行中… (\d+) 个目标$/, 'Running… $1 targets'],
      [/^完成 · (\d+) 次请求 · (.+)$/, 'Done · $1 requests · $2'], [/^已停止（展示已完成部分） · (\d+) 次请求 · (.+)$/, 'Stopped (partial) · $1 requests · $2'], [/^出错: (.+?) · (\d+) 次请求 · (.+)$/, 'Error: $1 · $2 requests · $3'],
      [/^测速完成 · (\d+) 次请求$/, 'Run finished · $1 requests'], [/^测速已停止（展示已完成部分） · (\d+) 次请求$/, 'Run stopped (partial) · $1 requests'], [/^测速出错: (.+) · (\d+) 次请求$/, 'Run error: $1 · $2 requests'],
      [/^完成 · (.+) · (\d+) tok$/, 'done · $1 · $2 tok'], [/^(\d+) 字 · (.+)$/, '$1 chars · $2'], [/^成功率 (\d+)%$/, 'success $1%'],
      [/^导出 (\w+)$/, 'Export $1'], [/^历史 ID (.+)$/, 'History ID $1'], [/^记录 (.+)$/, 'Run $1'], [/^历史记录 ID: (.+)$/, 'History ID: $1'],
      [/^正在测试 (.+)…$/, 'Testing $1…'], [/^OK · TTFT (.+) · (.+) tok\/s · 总 (.+) \((.+)\)$/, 'OK · TTFT $1 · $2 tok/s · total $3 ($4)'],
      [/^确定删除端点「(.+)」？该操作会写入配置文件。$/, 'Delete endpoint “$1”? This is written to the config file.'], [/^确定删除端点「(.+)」？$/, 'Delete endpoint “$1”?'],
      [/^已复制为 (.+)$/, 'Duplicated as $1'], [/^编辑端点 · (.+)$/, 'Edit endpoint · $1'], [/^(.+) 不是合法 JSON$/, '$1 is not valid JSON'], [/^拉取失败: (.+)$/, 'Fetch failed: $1'],
      [/^保存失败: (.+)$/, 'Save failed: $1'], [/^启动失败: (.+)$/, 'Start failed: $1'], [/^连接中断: (.+)$/, 'Connection lost: $1'], [/^记录不存在: (.+)$/, 'Record not found: $1'],
      [/^加载配置失败: (.+)$/, 'Failed to load config: $1'], [/^复制失败: (.+)$/, 'Copy failed: $1'], [/^未知端点: (.+)$/, 'Unknown endpoint: $1'],
      [/^共 (\d+) 个模型 · (.+)，点击勾选$/, '$1 models · $2, click to select'], [/^(\d+) 个模型 · (.+) · (.+)$/, '$1 models · $2 · $3'], [/^(.+) 的模型$/, 'Models of $1'], [/^已加入 (.+)$/, 'Added $1'],
      [/^对比选中 \((\d)\/2\)$/, 'Compare selected ($1/2)'], [/^成功率 ([AB])$/, 'Success $1'],
      [/^(.+) · (\d+)\/(\d+) 成功 · 平均 (.+)$/, '$1 · $2/$3 ok · avg $4'], [/^(\d+)\/(\d+) 成功$/, '$1/$2 ok'], [/^清空 (.+)$/, 'Clear $1'], [/^(.+) · 各阶段耗时趋势 \(ms\)$/, '$1 · latency by phase (ms)'],
      [/^确定删除端点「(.+)」的全部诊断记录？$/, 'Delete all ping records of “$1”?'], [/^HTTP (\d+) · DNS (.+) · TCP (.+) · TLS (.+) · 请求 (.+)$/, 'HTTP $1 · DNS $2 · TCP $3 · TLS $4 · request $5'],
      [/^超时 \(>(.+)\)$/, 'Timeout (>$1)'], [/^连接失败: (.+)$/, 'Connection failed: $1'], [/^请求失败: (.+)$/, 'Request failed: $1'], [/^HTTP (\d+): (.+)$/, 'HTTP $1: $2'],
      [/^各目标输出长度差异较大（(.+?)~(.+?) 字），总耗时与吞吐对比仅供参考$/, 'Output lengths differ a lot across targets ($1–$2 chars); total time and throughput comparisons are indicative only'],
      [/^以下目标有请求因 max_tokens 截断：(.+)$/, 'Some requests were truncated by max_tokens: $1'],
      [/^以下目标的 token 数为估算（接口未返回 usage），吞吐不参与排名：(.+)$/, 'Token counts are estimated (no usage in response), excluded from throughput ranking: $1'],
      [/^与 (.+) 相比$/, 'vs $1'], [/^上次 (.+) \((.+)\)$/, 'last $1 ($2)'], [/^(.+?) · max_tokens (\d+)$/, '$1 · max_tokens $2'],
    ],
    frags: {
      '标签': 'Tag', '预设': 'Preset', '并发': 'Concurrency', '非流式': 'non-streaming', '流式': 'streaming', '中途停止': 'stopped early', '持续': 'soak', ' 轮': ' rounds',
      '成功': 'ok', '平均': 'avg', '代理': 'proxy', '总 ': 'total ', '截断': 'truncated', '基线': 'baseline', '估': 'est', '完成': 'done', '失败': 'failed',
    },
  };

  const EN_FRAGS = Object.entries(EN.frags).sort((a, b) => b[0].length - a[0].length);
  const TW_PHR = Object.entries(TW_PHRASES).sort((a, b) => b[0].length - a[0].length);
  const CJK = /[一-鿿、。，：（）]/;

  function detect() {
    const q = new URLSearchParams(location.search).get('lang') || (location.hash.match(/[?&]lang=([\w-]+)/) || [])[1];
    if (q && LANGS[q]) return q;
    try { const s = JSON.parse(localStorage.getItem('apitest.lang')); if (s && LANGS[s]) return s; } catch (e) {}
    for (const l of (navigator.languages && navigator.languages.length ? navigator.languages : [navigator.language || 'en'])) {
      const x = l.toLowerCase();
      if (x.startsWith('zh')) return /tw|hk|mo|hant/.test(x) ? 'zh-TW' : 'zh-CN';
      if (x.startsWith('en')) return 'en';
    }
    return 'en';
  }
  const LANG = detect();
  function toTW(s) { for (const [a, b] of TW_PHR) s = s.split(a).join(b); return s.replace(/[一-鿿]/g, c => TW_CHARS[c] || c); }
  function toEN(s, depth) {
    depth = depth || 0;
    const k = s.trim(); if (!k) return s;
    const i = s.indexOf(k), lead = s.slice(0, i), trail = s.slice(i + k.length);
    if (EN.exact[k] !== undefined) return lead + EN.exact[k] + trail;
    // 前导符号（如 "⚠ "）单独剥离后再匹配
    const sym = k.match(/^[⚠✓✗×↳↻·\s]+/);
    if (sym && sym[0].length < k.length) return lead + sym[0] + toEN(k.slice(sym[0].length), depth + 1) + trail;
    if (depth < 3) for (const [re, rep] of EN.rules) { const m = k.match(re); if (m) return lead + rep.replace(/\$(\d)/g, (_, g) => { const v = m[+g] || ''; return CJK.test(v) ? toEN(v, depth + 1) : v; }) + trail; }
    let out = k; for (const [a, b] of EN_FRAGS) out = out.split(a).join(b);
    return lead + out + trail;
  }
  function tr(s) { if (typeof s !== 'string' || !CJK.test(s)) return s; return LANG === 'zh-TW' ? toTW(s) : LANG === 'en' ? toEN(s) : s; }

  const SKIP = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'PRE', 'CODE']);
  const state = new WeakMap();
  function skipNode(n) { const p = n.parentElement; return !p || SKIP.has(p.tagName) || p.closest('[data-no-i18n],td[data-full]') !== null; }
  function trText(n) { if (skipNode(n)) return; const st = state.get(n); if (st && st.out === n.data) return; const orig = n.data; if (!CJK.test(orig)) return; const out = tr(orig); state.set(n, { orig, out }); if (out !== orig) n.data = out; }
  function trAttrs(el) { if (!el.getAttribute) return; for (const a of ['placeholder', 'title']) { const v = el.getAttribute(a); if (v && CJK.test(v)) { const key = 'i18n' + a; const orig = el.dataset[key] || v; el.dataset[key] = orig; const out = tr(orig); if (out !== v) el.setAttribute(a, out); } } }
  function walk(root) { if (root.nodeType === 3) { trText(root); return; } if (root.nodeType !== 1) return; trAttrs(root); const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT); let n; while ((n = w.nextNode())) n.nodeType === 3 ? trText(n) : trAttrs(n); }
  function boot() {
    document.documentElement.lang = LANG;
    if (LANG === 'zh-CN') return;
    walk(document.body); document.title = tr(document.title);
    new MutationObserver(muts => { for (const m of muts) { if (m.type === 'childList') m.addedNodes.forEach(walk); else if (m.type === 'characterData') trText(m.target); else if (m.type === 'attributes') trAttrs(m.target); } })
      .observe(document.body, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['placeholder', 'title'] });
  }
  window.LANG = LANG; window.I18N_LANGS = LANGS; window.tr = tr; window.i18nBoot = boot;
  window.setLang = function (l) { try { localStorage.setItem('apitest.lang', JSON.stringify(l)); } catch (e) {} const u = new URL(location.href); u.searchParams.delete('lang'); u.hash = u.hash.replace(/([?&])lang=[\w-]+&?/, '$1').replace(/[?&]$/, ''); history.replaceState(null, '', u); location.reload(); };
})();

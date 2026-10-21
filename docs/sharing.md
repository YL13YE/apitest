# 独立仓库分享看板

监控仍在本机运行，展示站点只读取已导出的统计数据。导出不调用模型，访问页面也不会增加模型请求。展示页复用现有推荐、图表、筛选和卡片，默认短输出，支持最近1小时、6小时、24小时、72小时和7天；时间统一为北京时间。

## 公开范围

每次导出必须明确指定一个端点，还可以指定模型白名单。公开内容包括模型与推理强度、测试时间、样本数、时延、速度、完成率、卡顿、固定分类的错误计数和评价。内部端点名称替换为统一展示标签。

费用、倍率、预算、API 地址、Key、原始提示词、响应正文和服务商原始错误消息不导出。各时间窗口从请求记录重新统计，不对小时中位数求平均。页面提供两种固定评价配置，不提供需要后台重新计算的任意阈值编辑。

## 导出与本地预览

```bash
uv run apitest export-site --endpoint relay --out ../api-status
uv run python -m http.server 8801 --bind 127.0.0.1 --directory ../api-status
```

打开 `http://127.0.0.1:8801`。可使用 `--models model-a,model-b` 限定公开模型，`--history /path/to/history.jsonl` 指定历史文件，`--title "中转站模型表现"` 设置标题。

首次输出必须使用空目录，或仅包含 `.git` 的新展示仓库；后续只更新本工具管理的展示文件，拒绝符号链接。`data.json` 不变时保留原生成时间，因此重复导出不会仅因时钟变化产生提交。新请求、窗口内样本过期或展示资源变化会产生更新。

## 自动提交

建议使用独立公开仓库，并将其 `main` 根目录设置为 GitHub Pages 发布来源。以下为首次初始化示例，替换账号、端点和目录：

```bash
gh auth login
gh repo create YOUR_ACCOUNT/api-status --public

git init -b main ../api-status
git -C ../api-status remote add origin https://github.com/YOUR_ACCOUNT/api-status.git

uv run python scripts/publish_status.py \
  --site-repo ../api-status --endpoint relay --once
```

首次成功提交后，在展示仓库 Settings → Pages 选择 `Deploy from a branch`、`main` 和根目录。完整站点、Chart.js 和许可证都会随展示数据提交。

持续检查并自动发布：

```bash
uv run python scripts/publish_status.py \
  --site-repo ../api-status --endpoint relay --interval 600
```

脚本默认每10分钟读取一次历史，只在生成内容有变化时 commit；此前推送失败的提交会在下一次检查时重试。脚本仅暂存固定展示文件，不强推，也不会把源代码仓库或真实配置推到展示仓库。HTTPS GitHub 远程会使用本机已有 `gh` 登录凭据，凭据不会写入脚本或仓库。展示仓库需使用 `main` 分支，Git 提交姓名和邮箱需已配置。

脚本使用文件锁避免重复运行，适用于 Linux/macOS。可通过用户级 systemd 服务持续运行，示例中的路径需改为实际绝对路径：

```ini
[Unit]
Description=Publish APITest public model metrics

[Service]
Type=simple
WorkingDirectory=/absolute/path/apitest
ExecStart=/absolute/path/apitest/.venv/bin/python /absolute/path/apitest/scripts/publish_status.py --site-repo /absolute/path/api-status --endpoint relay --interval 600
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

保存为 `~/.config/systemd/user/apitest-status-publish.service` 后执行：

```bash
systemctl --user daemon-reload
systemctl --user enable --now apitest-status-publish.service
systemctl --user status apitest-status-publish.service
```

用户服务随用户管理器运行，其开机启动行为取决于该账号的登录与 linger 设置。脚本不创建或恢复监控任务，只发布已有记录。页面每分钟检查轻量更新索引，仅在数据变化时下载完整统计，避免频繁重复传输。页面会展示最后测试距今多久，并可悬停查看完整测试时间与数据更新时间；发布延迟或暂停采样不等于中转站故障。

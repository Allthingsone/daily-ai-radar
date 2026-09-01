# GitHub Pages + DeepSeek + 邮件部署说明

这一版不需要常驻服务器。GitHub Actions 每天在云端启动一台临时运行器，完成采集、验证、筛选和静态页面生成；GitHub Pages 保存并提供最后一次成功生成的页面。电脑和手机都可以关机。

## 需要准备的账号与设置

需要 GitHub 账号和 DeepSeek API 账号；邮件推送还需要已开启 SMTP 的 163 邮箱。新闻 RSS、Hacker News、掘金与 arXiv 采集本身不需要 API Key。知乎、CSDN 和微信公众号本版未启用，因此当前部署不需要新增任何社区账号或 Secret。

推荐创建公开仓库，例如 `daily-ai-radar`。GitHub Free 可以为公开仓库使用 Pages；私有仓库发布 Pages 取决于 GitHub 付费方案。

仓库需要完成以下设置：

1. 打开 `Settings → Pages`，在 `Build and deployment → Source` 选择 `GitHub Actions`。
2. 打开 `Settings → Secrets and variables → Actions → Variables`，新增仓库变量：
   - 名称：`ARXIV_CONTACT_EMAIL`
   - 值：你可以接收联系邮件的地址，例如 `name@example.com`
3. 在同一页切换到 `Secrets`，新增：
   - `DEEPSEEK_API_KEY`：DeepSeek 开放平台生成的 API Key。
   - `DAILY_RADAR_EMAIL_USERNAME`：163 邮箱地址。
   - `DAILY_RADAR_EMAIL_AUTH_CODE`：163 邮箱客户端授权码，不是网页登录密码。

收件地址默认就是 `DAILY_RADAR_EMAIL_USERNAME`，无需再提供第二个邮箱。当前工作流把邮件发送视为每日交付的一部分；若两个邮件 Secret 缺失或 SMTP 发送失败，本次任务不会写入成功标记，后续备用触发会继续重试，并保留上一版成功页面。

`ARXIV_CONTACT_EMAIL` 用于让 arXiv 识别客户端，不是登录凭据。它会出现在发往数据源的 User-Agent 中；如果不希望使用主邮箱，可填写专门的联系邮箱或别名。

不要把 DeepSeek Key、GitHub 密码、Personal Access Token、邮箱密码或 SMTP 授权码写入文件、Issue 或聊天。GitHub Pages 发布使用 Actions 自动提供的短期 `GITHUB_TOKEN`。

## 首次上传

如果从新目录首次上传，可以在本机执行：

```bash
cd /Users/fangzekuan/Desktop/Daily_Paper
git init
git add .
git commit -m "Add Daily AI Radar GitHub Pages preview"
git branch -M main
git remote add origin https://github.com/YOUR_NAME/daily-ai-radar.git
git push -u origin main
```

也可以先运行 `gh auth login`，再使用 GitHub CLI 创建并推送仓库。不要把登录时出现的设备验证码发送给其他人。

推送后进入仓库的 `Actions` 页面，选择 **Daily radar and GitHub Pages**，点击 **Run workflow** 做第一次手动运行。成功后，部署地址通常是：

```text
https://YOUR_NAME.github.io/daily-ai-radar/
```

准确地址会显示在本次工作流的 `deploy` 环境中。

## 每日更新机制

工作流文件是 [`.github/workflows/pages.yml`](../.github/workflows/pages.yml)：

```text
北京时间 03:10 / 03:30 / 03:50 / 04:10 / 04:30（延迟补偿）
北京时间 08:10 / 08:30 / 08:50 / 09:10 / 09:30（正式兜底）
        ↓
检查当日是否已经完整成功；若是则立即退出
        ↓
运行全部离线测试
        ↓
抓取 RSS / Atom / GitHub Releases / HN / 掘金 AI 热榜
并完整分页抓取北京时间当天相关分类的全部 arXiv 新论文
        ↓
校验链接、来源域名和 arXiv ID
        ↓
新闻：DeepSeek V4-Pro Thinking max
论文：V4-Pro 非思考高召回初筛 → 完整摘要 Thinking max 复筛
        ↓
检查当天 Token / 估算费用双限额
        ↓
生成 HTML + JSON + Markdown + RSS
        ↓
发布到 GitHub Pages，并通过 163 SMTP 发送邮件
        ↓
仅在邮件和 Pages 均成功后保存当日成功标记
```

十个时间不是十次重复采集：前五次用于补偿 GitHub 调度器可能出现的长时间延迟，后五次是 arXiv 公告时间之后的正式兜底。成功标记按北京时间日期保存在 GitHub Actions Cache；第一次完整成功后，后续运行会在安装依赖和调用 DeepSeek 前退出。工作流的 `cancel-in-progress` 为 `false`，所以较晚到达的备用事件只会排队，不会取消正在运行的主任务。

arXiv 的正式公告时间是美东时间 20:00，对应北京时间夏令时 08:00、冬令时 09:00。08 点和 09 点两组触发覆盖季节切换；凌晨尝试若被 GitHub 延迟约五小时，也会落入这个可用窗口。若任何尝试在官方公告可用前准时到达，论文就绪检查会在调用 DeepSeek 前失败，不会把上一批论文冒充今日结果，后续时间继续重试。

手动运行默认也遵守当日去重。若确实需要在当天重新采集，可在 **Run workflow** 时勾选 `force`；这会再次调用 DeepSeek 并再次发送邮件，应只在明确需要时使用。

页面上的开关是只读的前端筛选：

- `AI 重大发布与热议 / MLLM-VLA 驾驶论文`
- `精选 / 全部`
- 论文 `今日 / 近 4 日`
- 标题、摘要、作者和标签搜索

其中“今日”按 `Asia/Shanghai` 自然日计算。旧论文可以出现在“近 4 日”，但不会填入“今日”。

社区热议当前来自 Hacker News 官方 API 和掘金人工智能热榜；榜单排名与互动量会显示在条目卡片中。CSDN 适配器因原文日期在 CI 环境中无法稳定核验而默认停用。详细来源矩阵和知乎/公众号接入边界见 [`COMMUNITY_SOURCES.md`](COMMUNITY_SOURCES.md)。

如果全部来源不可用、DeepSeek Key/邮件 Secret 缺失、SMTP 失败、模型响应不合法或预算耗尽，构建会失败，Pages 不会被空页面覆盖，也不会写入当日成功标记；下一个备用时间会继续尝试。部分来源失败时，系统会继续处理可验证结果，并把失败记录展示在“来源健康”区域。

每次 DeepSeek 调用都会保存输入、输出、推理、缓存 Token 和按官方峰谷价估算的费用，并区分新闻、论文初筛和论文复筛。默认上限为每天 500,000 Token 与 1 美元，任一达到即停止；全量论文尚未筛完时不会继续发布或发信。由于 GitHub Runner 每次都是新机器，工作流会同时从现有 Pages 的 `data/latest.json` 与 Actions 当日状态缓存恢复累计量。状态缓存在采集失败后也会执行保存，因此手动重试不会把已计费的失败调用重新当成零用量。

V4-Pro 的 Thinking `max` 会让推理 Token 与最终 JSON 共用输出额度。项目给单次调用最多 32,768 Token；如果仍以 `finish_reason=length` 截断，程序立即把候选批次拆成两半分别处理，而不是原样重试同一请求。每日 Token 与费用双限额仍优先约束实际可用的 `max_tokens`。

## 本地预览

不采集、只用当前数据库生成页面：

```bash
daily-radar build-site --output site
python3 -m http.server 8765 --directory site
```

然后访问 <http://127.0.0.1:8765>。本地 `site/` 被 Git 忽略，因为正式部署会在 Actions 中重新生成。

## 第一版的边界

- Pages 是静态只读站点，不支持收藏、已读或“不相关”反馈写入。
- 每次 Actions 使用临时运行器；这一版发布当前日报和近 4 日论文，不保存完整在线历史数据库。
- 定时运行可能因 GitHub 高负载略有延迟，所以选择了非整点时间。
- GitHub 会在公开仓库连续 60 天没有仓库活动时自动停用定时工作流。若仓库长期不改动，需要在 Actions 页面重新启用；后续版本可增加轻量保活或历史归档策略。
- 费用是根据官方公开单价和返回 Token 估算；最终扣费以 DeepSeek 控制台账单为准。
- Actions 状态缓存只保存 SQLite 数据库中的公开候选、筛选结果和用量记录，不包含 DeepSeek Key、邮箱授权码或 Prompt 正文；DeepSeek 控制台账单仍是最终费用依据。

GitHub 的相关官方说明：

- [使用自定义工作流发布 GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [配置 GitHub Pages 发布源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [GitHub Actions 定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

# GitHub Pages 第一版部署说明

这一版不需要常驻服务器。GitHub Actions 每天在云端启动一台临时运行器，完成采集、验证、筛选和静态页面生成；GitHub Pages 保存并提供最后一次成功生成的页面。电脑和手机都可以关机。

## 需要准备的账号与设置

只需要一个 GitHub 账号。当前采集不需要新闻 API Key、arXiv API Key、LLM Key 或邮箱密码。

推荐创建公开仓库，例如 `daily-ai-radar`。GitHub Free 可以为公开仓库使用 Pages；私有仓库发布 Pages 取决于 GitHub 付费方案。

仓库需要完成两个设置：

1. 打开 `Settings → Pages`，在 `Build and deployment → Source` 选择 `GitHub Actions`。
2. 打开 `Settings → Secrets and variables → Actions → Variables`，新增仓库变量：
   - 名称：`ARXIV_CONTACT_EMAIL`
   - 值：你可以接收联系邮件的地址，例如 `name@example.com`

`ARXIV_CONTACT_EMAIL` 用于让 arXiv 识别客户端，不是登录凭据。它会出现在发往数据源的 User-Agent 中；如果不希望使用主邮箱，可填写专门的联系邮箱或别名。

不要把 GitHub 密码、Personal Access Token 或邮箱密码发给本项目。GitHub Pages 发布使用 Actions 自动提供的短期 `GITHUB_TOKEN`。

## 首次上传

当前文件夹还不是 Git 仓库。准备好 GitHub 仓库后，可以在本机执行：

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
北京时间每天 08:37
        ↓
运行 36+ 个离线测试
        ↓
抓取 RSS / Atom / GitHub Releases / arXiv
        ↓
校验链接、来源域名和 arXiv ID
        ↓
执行新闻发布/成果门槛与论文双轴门槛
        ↓
生成 HTML + JSON + Markdown + RSS
        ↓
发布到 GitHub Pages
```

页面上的开关是只读的前端筛选：

- `AI 发布与成果 / MLLM-VLA 驾驶论文`
- `精选 / 全部`
- 论文 `今日 / 近 4 日`
- 标题、摘要、作者和标签搜索

其中“今日”按 `Asia/Shanghai` 自然日计算。旧论文可以出现在“近 4 日”，但不会填入“今日”。

如果全部来源都不可用，构建会失败，Pages 不会被空页面覆盖，上一版成功页面仍在线。部分来源失败时，系统会继续发布可验证结果，并把失败记录展示在“来源健康”区域。

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
- 定时邮件尚未接入。下一版可在 Pages 构建成功后，用 Resend、SMTP 或其他邮件服务发送同一份日报。

GitHub 的相关官方说明：

- [使用自定义工作流发布 GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [配置 GitHub Pages 发布源](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)
- [GitHub Actions 定时事件](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

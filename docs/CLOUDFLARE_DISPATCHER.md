# Cloudflare 准时触发器部署

GitHub Actions 的 `schedule` 可能延迟数小时，不能保证早晨准时交付。这个项目把 Cloudflare Worker 作为外部闹钟：Worker 只检查状态并触发 GitHub 工作流，采集、DeepSeek、邮件和 Pages 仍全部在 GitHub Actions 中完成。

## 实际时间表

Cloudflare Cron 使用 UTC，仓库中的配置已换算成北京时间：

| 北京时间 | 阶段 | 行为 |
| --- | --- | --- |
| 07:15 / 07:35 / 07:55 | `news` | 采集、验证并用 DeepSeek 预筛新闻；第一次成功后其余检查不再重复运行 |
| 08:05 / 08:20 / 08:35 / 08:50 | `publish` | 美东夏令时的论文发布检查与重试 |
| 09:05 / 09:20 / 09:35 | `publish` | 美东标准时间或 arXiv 索引延迟时的检查与重试 |

Worker 会自动读取 `America/New_York` 的夏令时状态。工作日只有达到 arXiv 当天公告时间后才会触发 `publish`；周末没有新论文公告，会在 08:05 发布真实的新闻日报。它还会检查 Pages 日期、GitHub 中已成功的同阶段任务和正在运行的任务，避免重复调用 DeepSeek 或重复发信。

GitHub 自带的 08:10–09:30 五个定时仍保留作独立兜底，但准时性主要由 Cloudflare 提供。

## 需要新增的凭据

只新增一个 GitHub fine-grained personal access token：

1. 在 GitHub 打开 `Settings → Developer settings → Personal access tokens → Fine-grained tokens`。
2. Resource owner 选择 `Allthingsone`，Repository access 只选择 `daily-ai-radar`。
3. Repository permissions 将 `Actions` 设为 `Read and write`；其他权限保持最小值。
4. 设置合理的到期时间并记录续期日期。Token 到期后，Worker 无法触发任务，但 GitHub 自带的五个定时仍会继续兜底。

这个 Token 只存入 Cloudflare 加密 Secret，不能写入 `wrangler.json`、`.dev.vars.example`、GitHub 仓库、Issue 或聊天。原有的 DeepSeek Key 和邮箱授权码继续只放在 GitHub Actions Secrets，不需要复制到 Cloudflare。

## 首次部署

先注册或登录 Cloudflare，然后在本机执行：

```bash
cd /Users/fangzekuan/Desktop/Daily_Paper/cloudflare-worker
npm install
npm test
npx wrangler login
npx wrangler secret put GITHUB_ACTIONS_TOKEN
npm run deploy
```

执行 `secret put` 后，在终端提示中粘贴上一步生成的 Token。部署完成后，在 Cloudflare Dashboard 的 `Workers & Pages → daily-ai-radar-dispatcher → Triggers` 中应能看到三个 Cron 表达式。

配置文件中的仓库、分支和 Pages 地址已经对应：

```text
Allthingsone/daily-ai-radar
main
https://allthingsone.github.io/daily-ai-radar/data/latest.json
```

如果以后修改仓库名、默认分支或 Pages 地址，需要同步修改 [`cloudflare-worker/wrangler.json`](../cloudflare-worker/wrangler.json) 后重新执行 `npm run deploy`。Cron 修改在 Cloudflare 全球生效可能需要少量传播时间，因此应在次日正式使用前完成部署。

## 验证

部署后先到 GitHub Actions 手动选择 `phase = news` 运行一次，确认工作流能只保存新闻状态而不发邮件；再选择 `phase = publish` 完成页面与邮件。

也可以在本地模拟 Cron。先把示例文件复制为不会被 Git 跟踪的 `.dev.vars`，填入真实 Token，再启动 Wrangler：

```bash
cd /Users/fangzekuan/Desktop/Daily_Paper/cloudflare-worker
cp .dev.vars.example .dev.vars
npm run dev
```

另开终端调用下面的地址会真实触发 GitHub 工作流，因此只在需要端到端测试时执行：

```bash
curl "http://127.0.0.1:8787/cdn-cgi/handler/scheduled?cron=15%2C35%2C55+23+%2A+%2A+%2A"
```

验证完成后，可在 GitHub Actions 中看到运行标题 `Daily radar · news` 或 `Daily radar · publish`。Cloudflare 日志会记录 `dispatch`、`already-published`、`phase-complete`、`workflow-active` 或 `arxiv-not-ready`，但不会记录 Token 内容。

## Secret 轮换

GitHub Token 到期或主动撤销后，生成新 Token 并重新执行：

```bash
cd /Users/fangzekuan/Desktop/Daily_Paper/cloudflare-worker
npx wrangler secret put GITHUB_ACTIONS_TOKEN
```

无需改代码或重新提交仓库。

官方参考：

- [Cloudflare Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/)
- [Cloudflare Workers Secrets](https://developers.cloudflare.com/workers/configuration/secrets/)
- [GitHub：Create a workflow dispatch event](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

# 阿里云函数计算准时触发器部署

GitHub Actions 的 `schedule` 可能延迟，因此本项目使用阿里云函数计算 FC 3.0 作为外部闹钟。FC 只读取当天状态并调用 GitHub `workflow_dispatch`；采集、DeepSeek 筛选、邮件和 Pages 仍全部在 GitHub Actions 中完成。

这是**事件函数 + 定时触发器**，不需要 HTTP 触发器、自定义域名或公开 URL，也不存在 `workers.dev` 子域名注册步骤。电脑和手机均可关机。

## 实际时间表

三个触发器都显式使用 `CRON_TZ=Asia/Shanghai`，无需人工换算 UTC：

| 触发器 | 北京时间 | 触发消息 | 行为 |
| --- | --- | --- | --- |
| `daily-radar-news` | 07:15 / 07:35 / 07:55 | `{"phase":"news"}` | 采集、验证并预筛新闻 |
| `daily-radar-publish-8` | 08:05 / 08:20 / 08:35 / 08:50 | `{"phase":"publish"}` | 美东夏令时的论文发布检查与重试 |
| `daily-radar-publish-9` | 09:05 / 09:20 / 09:35 | `{"phase":"publish"}` | 美东标准时间或 arXiv 索引延迟时的检查与重试 |

调度器会动态读取 `America/New_York` 的夏令时状态。工作日只有达到 arXiv 当天公告时间后才允许触发 `publish`；周末没有新公告，会在第一次发布检查时生成真实的新闻日报。每次检查还会读取 Pages 日期、GitHub 中已成功的同阶段任务和正在运行的任务，以避免重复消耗 DeepSeek Token 或重复发信。

GitHub 自带的 08:10、08:30、08:50、09:10、09:30 五个定时仍保留作独立兜底。

## 需要准备

- 一个已开通函数计算 FC 3.0 的阿里云账号。
- 已创建的 GitHub fine-grained personal access token：只授权 `Allthingsone/daily-ai-radar`，Repository permission 的 `Actions` 为 `Read and write`，`Metadata` 为只读。
- 本机 Node.js 20+；本地仅用于测试和生成 ZIP，不需要保存阿里云 AccessKey。

原有 `DEEPSEEK_API_KEY`、`DAILY_RADAR_EMAIL_USERNAME` 和 `DAILY_RADAR_EMAIL_AUTH_CODE` 继续只保存在 GitHub Actions Secrets，不复制到阿里云。

## 生成部署包

```bash
cd /Users/fangzekuan/Desktop/Daily_Paper/aliyun-fc
npm test
npm run package
```

生成的 `daily-ai-radar-fc.zip` 已被 Git 忽略。代码没有第三方运行时依赖，ZIP 根目录中会直接包含 `index.js`。

## 在控制台创建事件函数

进入[阿里云函数计算控制台](https://fcnext.console.aliyun.com/)，选择 **函数管理 → 函数 → 创建函数 → 事件函数**，按下面配置：

| 配置项 | 值 |
| --- | --- |
| 函数名称 | `daily-ai-radar-dispatcher` |
| 运行环境 | Node.js 20 |
| 代码上传 | 上传 `aliyun-fc/daily-ai-radar-fc.zip` |
| 请求处理程序 | `index.handler` |
| 内存 | 128 MB |
| 执行超时 | 60 秒 |
| 允许访问 VPC | 关闭 |
| 允许默认网卡访问公网 | 开启 |
| 时区 | `Asia/Shanghai` |

该函数必须访问 `api.github.com` 和 GitHub Pages，因此“允许默认网卡访问公网”必须开启。地域可优先选择“中国香港”，减少访问 GitHub 时受跨境网络波动影响的概率；若选择内地地域，务必先完成下面的安全测试。

## 配置环境变量

在函数详情的 **配置 → 高级配置 → 环境变量** 中添加：

```text
GITHUB_ACTIONS_TOKEN=<粘贴 GitHub fine-grained token>
GITHUB_OWNER=Allthingsone
GITHUB_REPO=daily-ai-radar
GITHUB_WORKFLOW=pages.yml
GITHUB_REF=main
PAGES_LATEST_URL=https://allthingsone.github.io/daily-ai-radar/data/latest.json
HTTP_TIMEOUT_MS=15000
```

只有 `GITHUB_ACTIONS_TOKEN` 是秘密；不能把它写入 `deployment-config.json`、Git 仓库、Issue、聊天或函数日志。FC 环境变量会以 AES-256 静态加密，但有控制台配置权限的人仍可能查看配置值；如以后有多人管理阿里云账号，可进一步改用 KMS。当前个人账号场景还应启用 MFA，并保留 Token 的单仓库最小权限。

[`aliyun-fc/deployment-config.json`](../aliyun-fc/deployment-config.json) 是可审计的配置清单，不含 Token，也不是要直接上传到控制台的代码包。

## 先做安全测试

部署代码后，在“测试函数”旁配置下面的测试事件。`dry_run=true` 会真实检查 Pages 与 GitHub Actions 读取权限，但绝不会发送 `POST workflow_dispatch`：

```json
{
  "triggerTime": "2026-09-03T23:15:00Z",
  "triggerName": "manual-safe-test",
  "payload": "{\"phase\":\"news\",\"dry_run\":true}"
}
```

成功结果应为以下之一：

- `dry-run-would-dispatch`：连接、Token 和查重逻辑均正常，正式触发时会启动工作流；
- `already-published`：Pages 已是该北京时间日期；
- `phase-complete`：GitHub 中该阶段当天已经成功；
- `workflow-active`：工作流正在运行。

如果出现 `HTTP 401/403`，先检查 Token 是否仍有效、是否只授权了正确仓库，以及 `Actions: Read and write` 是否保存成功。如果连接超时，确认公网访问已开启，并考虑切换到中国香港地域。

## 创建三个定时触发器

在函数详情选择 **触发器 → 创建触发器 → 定时触发器 → 自定义**，逐个创建：

```text
名称：daily-radar-news
Cron：CRON_TZ=Asia/Shanghai 0 15,35,55 7 * * *
触发消息：{"phase":"news"}
```

```text
名称：daily-radar-publish-8
Cron：CRON_TZ=Asia/Shanghai 0 5,20,35,50 8 * * *
触发消息：{"phase":"publish"}
```

```text
名称：daily-radar-publish-9
Cron：CRON_TZ=Asia/Shanghai 0 5,20,35 9 * * *
触发消息：{"phase":"publish"}
```

创建后检查三个触发器均为启用状态，并核对控制台展示的下一次触发时间确实是北京时间。正式端到端测试时，可以把安全测试事件中的 `dry_run` 改为 `false`；这会真实启动 GitHub 工作流，并可能产生 DeepSeek 用量和邮件，因此只执行一次。

日志只会记录 `dispatch`、`already-published`、`phase-complete`、`workflow-active`、`arxiv-not-ready` 或 `dry-run-would-dispatch` 以及请求 ID，不会打印 Token。

## Cloudflare 迁移收尾

阿里云连续验证成功后，再删除原 Cloudflare Worker 或其中的 `GITHUB_ACTIONS_TOKEN` Secret。若 Token 曾经粘贴到代码、日志或聊天中，应立即在 GitHub 撤销并生成新的 Token；否则可以继续复用同一个最小权限 Token。

## 官方参考

- [阿里云：创建事件函数](https://help.aliyun.com/zh/functioncompute/creating-an-event-function)
- [阿里云：定时触发器与 `CRON_TZ`](https://help.aliyun.com/zh/functioncompute/time-triggers)
- [阿里云：Node.js Handler](https://help.aliyun.com/zh/functioncompute/request-handlers)
- [阿里云：环境变量安全](https://help.aliyun.com/zh/functioncompute/environment-variables)
- [阿里云：函数网络配置](https://help.aliyun.com/zh/functioncompute/configure-network-settings)
- [GitHub：Create a workflow dispatch event](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

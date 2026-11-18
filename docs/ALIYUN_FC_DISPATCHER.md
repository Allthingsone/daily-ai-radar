# 阿里云函数计算准时触发器部署

GitHub Actions 的 `schedule` 可能延迟，因此本项目使用阿里云函数计算 FC 3.0 作为外部闹钟。FC 只读取当天状态并调用 GitHub `workflow_dispatch`；采集、DeepSeek 筛选、邮件和 Pages 仍全部在 GitHub Actions 中完成。

这是**事件函数 + 定时触发器**，不需要 HTTP 触发器、自定义域名或公开 URL，也不存在 `workers.dev` 子域名注册步骤。电脑和手机均可关机。
本次修改需要同时更新仓库工作流与 FC 中的 `aliyun-fc/index.js`；只修改本地文件不会更新已部署的定时函数。


## 实际时间表

只创建一个“每 10 分钟”的间隔触发器，不再依赖控制台对复合 CRON 和时区前缀的解释：

| 触发器 | 周期 | 触发消息 | 行为 |
| --- | --- | --- | --- |
| `daily-radar-watchdog` | 每 10 分钟 | `{"phase":"auto"}` | 由代码按北京时间自动选择新闻、发布或立即跳过 |

北京时间 07:00–10:30 之外立即返回 `outside-window`，不访问网络。07:00–07:59 选择 `news`，08:00 起选择 `publish`，不受美东夏令时影响。论文使用前一日的北京时间公告，周六仍处理周五论文；停刊日及空筛选结果由采集器明确记录，新闻流程保持原样。

每次有效检查读取 Pages 日期、同阶段成功记录和运行中的工作流；当天发布失败达到 3 次后返回 `attempt-limit`。GitHub 原生定时工作流也执行同一限制，因此 FC 和原生备份不会相互绕过。成功或达到上限后不重复调用 DeepSeek、不重复发信。

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

## 更新已有函数

1. 登录[函数计算控制台](https://fcnext.console.aliyun.com/)，切换到原来部署的地域，打开 `daily-ai-radar-dispatcher`。
2. 进入 **代码 → 上传代码 → 上传 ZIP 包**，选择本机 `aliyun-fc/daily-ai-radar-fc.zip`，点击 **保存并部署**；若上传后仍显示待部署，再点击 **部署代码**。
3. 继续使用原有环境变量、`index.handler` 处理程序及每 10 分钟的 `daily-radar-watchdog` 触发器，触发消息为 `{"phase":"auto"}`。本次无需更换 GitHub Token。
4. 部署完成后，代码中应包含 `PUBLISH_START_MINUTE = 8 * 60` 和 `MAX_PUBLISH_ATTEMPTS = 3`。可用以下事件测试读取状态；`dry_run` 不会触发采集或发送邮件。

```json
{
  "payload": "{\"phase\":\"publish\",\"dry_run\":true}"
}
```

返回 `dry-run-would-dispatch`、`already-published`、`phase-complete`、`workflow-active` 或 `attempt-limit` 均表示状态检查已执行；08:00 前返回 `before-publish-window` 正常。代码上传及部署入口参见[官方事件函数文档](https://help.aliyun.com/zh/functioncompute/creating-an-event-function)。

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
- `attempt-limit`：当天发布已失败 3 次，停止自动尝试；排查后可在 GitHub 手动勾选 `force`；
- `before-publish-window`：尚未到北京时间 08:00 的发布窗口；
- `workflow-active`：工作流正在运行。

如果出现 `HTTP 401/403`，先检查 Token 是否仍有效、是否只授权了正确仓库，以及 `Actions: Read and write` 是否保存成功。如果连接超时，确认公网访问已开启，并考虑切换到中国香港地域。

## 创建一个间隔触发器

先上传本版本重新生成的 `daily-ai-radar-fc.zip` 并部署代码，再进入 **触发器 → 创建触发器 → 定时触发器**：

```text
名称：daily-radar-watchdog
触发方式：时间间隔
时间间隔：10 分钟
触发消息：{"phase":"auto"}
启用触发器：开启
```

如果控制台要求直接输入表达式，填写 `@every 10m`。创建后确认状态为“启用”。旧的 `daily-radar-news`、`daily-radar-publish-8`、`daily-radar-publish-9` 可以先禁用；新触发器验证成功后再删除。

在非投递窗口看到 `outside-window` 是正常结果。正式端到端测试时，可以给手动测试事件设置一个 07:00–10:30 内的 `triggerTime` 并保留 `dry_run=true`；预期返回 `dry-run-would-dispatch`、`already-published`、`phase-complete` 或 `workflow-active`，不会产生 DeepSeek 用量或邮件。

日志只会记录 `dispatch`、`already-published`、`phase-complete`、`workflow-active`、`before-publish-window`、`attempt-limit`、`outside-window` 或 `dry-run-would-dispatch` 以及请求 ID，不会打印 Token。

## Cloudflare 迁移收尾

阿里云连续验证成功后，再删除原 Cloudflare Worker 或其中的 `GITHUB_ACTIONS_TOKEN` Secret。若 Token 曾经粘贴到代码、日志或聊天中，应立即在 GitHub 撤销并生成新的 Token；否则可以继续复用同一个最小权限 Token。

## 官方参考

- [阿里云：创建事件函数](https://help.aliyun.com/zh/functioncompute/creating-an-event-function)
- [阿里云：定时触发器与时间间隔表达式](https://help.aliyun.com/zh/functioncompute/time-triggers)
- [阿里云：Node.js Handler](https://help.aliyun.com/zh/functioncompute/request-handlers)
- [阿里云：环境变量安全](https://help.aliyun.com/zh/functioncompute/environment-variables)
- [阿里云：函数网络配置](https://help.aliyun.com/zh/functioncompute/configure-network-settings)
- [GitHub：Create a workflow dispatch event](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)

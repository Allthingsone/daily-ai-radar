# 社区热议来源与真实性边界

社区来源的作用是回答“今天哪些 AI 议题正在被讨论”，不是替代官方公告或论文原文。每条互动量都会以结构化字段保存并展示；DeepSeek 不能根据标题中的“爆火”“重磅”等词自行制造热度。

当前启用端点可直接审计：Hacker News 使用其[官方 Firebase API](https://github.com/HackerNews/API)，国内社区使用掘金[人工智能分类页](https://juejin.cn/ai)对应的[公开热榜端点](https://api.juejin.cn/content_api/v1/content/article_rank?category_id=6809637773935378440&type=hot)。采集后仍会访问每篇原文并读取公开的 `BlogPosting.datePublished`。

## 当前来源矩阵

| 平台 | 当前状态 | 采集依据 | 热度依据 | 事实定位 |
|---|---|---|---|---|
| Hacker News | 已启用 | Y Combinator 官方 Firebase API 的 `topstories` 与 `item` | 当前 Top 30，且 100 points 或 30 评论至少满足一项 | 海外技术社区讨论信号；外链仍需单独验证 |
| 掘金 | 已启用 | 掘金网页使用的人工智能分类热榜 HTTPS 接口，并访问原文公开 Schema.org 元数据 | 当前 Top 20，且 `hot_rank` 300 或 10 评论至少满足一项；同时保存浏览/点赞/收藏 | 国内技术社区讨论信号；原文日期来自 `BlogPosting.datePublished`，绝不以采集时间冒充 |
| CSDN | 适配器已实现、默认停用 | CSDN 公开热榜页使用的 `hot-rank` HTTPS 接口 | 热榜分数、浏览、评论、收藏 | 原文页当前对 CI 类自动化请求返回 HTTP 521，无法稳定取得 `article:published_time`；在恢复可靠日期核验前不进入默认工作流 |
| 知乎 | 未启用 | 知乎官方开放平台提供 `hot_list`，但需要 Access Secret、时间戳鉴权，当前仍是邀测/商务接入 | 官方热榜返回值 | 用户提供官方 `ZHIHU_ACCESS_SECRET` 后再实现；不调用未经授权的内部接口 |
| 微信公众号 | 未启用 | 微信官方 `freepublish/batchget` 只能读取调用方有管理权限的公众号发布记录，不是全平台检索接口 | 官方接口没有任意公众号通用热榜 | 需要明确的公众号清单以及合法的账号权限或自托管订阅桥；不会使用来源不明的公共聚合器 |
| Reddit | 未启用 | 官方 Data API 要求注册与 OAuth；匿名 RSS 在当前网络验证中连接不稳定 | Score / comments 或 Hot rank | 提供合规 Reddit 应用凭据后再接入，不让默认工作流依赖不稳定匿名访问 |

## 程序如何防止“模型猜热度”

1. 采集器保存平台、榜单名次、points/热榜分、评论、浏览、点赞、收藏和讨论链接。
2. 配置文件先计算 `qualified=true/false`。
3. Prompt 会同时看到原始指标和 `has_verifiable_heat_signal`。
4. DeepSeek 返回判断后，程序再次用采集器元数据覆盖热度门槛；如果没有真实达标记录，即使模型返回 `true`，科研成果或社区热议也会被强制拒绝。
5. 来源类型为 `community` 的原帖只能进入 `community-trending`，不能直接作为模型发布、产品发布或科研成果的事实证明；同一事件若另有官方来源，则在去重后由官方条目承载合并的社区热度。
6. Pages 在卡片中显示“社区热度”，并固定注明“讨论信号，不代表帖内事实已获官方证实”。

门槛可在 [`config/sources.yaml`](../config/sources.yaml) 中调整：

```yaml
community_rank_limit: 30
community_min_points: 100
community_min_comments: 30
```

`community_min_points` 与 `community_min_comments` 采用“至少一项达标”，同时必须位于 `community_rank_limit` 以内。修改门槛不需要新增 API Key；修改 Prompt 时仍须递增 `config/settings.yaml` 的 `prompt_version`。

## 单个社区源失败时

社区适配器与 RSS 源遵循相同的失败隔离：单个来源超时只会写入来源健康记录，不会阻止其他来源、论文采集、Pages 构建或邮件发送。若 DeepSeek 或总预算失败，工作流仍会整体停止，保留上一版成功页面。

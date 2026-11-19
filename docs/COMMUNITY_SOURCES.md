# 国内媒体、社区热议来源与真实性边界

社区来源的作用是回答“今天哪些 AI 议题正在被讨论”，不是替代官方公告或论文原文。每条互动量都会以结构化字段保存并展示；DeepSeek 不能根据标题中的“爆火”“重磅”等词自行制造热度。

当前启用端点可直接审计：Hacker News 使用其[官方 Firebase API](https://github.com/HackerNews/API)，国内社区使用掘金[人工智能分类页](https://juejin.cn/ai)对应的[公开热榜端点](https://api.juejin.cn/content_api/v1/content/article_rank?category_id=6809637773935378440&type=hot)。采集后仍会访问每篇原文并读取公开的 `BlogPosting.datePublished`。

国内媒体采用固定名单，当前为**量子位、新智元**。量子位[官网介绍](https://www.qbitai.com/关于我们)披露微信端订阅超过 100 万；新智元[官网介绍](https://www.aiera.com.cn/about.html)披露公众号粉丝 150 万以上。这些是媒体公开的规模说明（2026-11-19 核验），并非本程序统计的实时粉丝数。采集的是它们同名媒体官网的公开文章，不是微信全平台搜索，也不保证覆盖仅在微信发布的内容。

**扩充的是来源，新闻方向和门槛保持不变。** 所有国内候选与海外候选一样，经过 48 小时时间窗口、来源验证、去重和同一个 DeepSeek 新闻 Prompt：只收重大基座模型、重要 AI 产品/工具/硬件、自动驾驶数据集/Benchmark，以及同时重要且有可核验热度的科研成果或有实质价值的热门讨论。融资、人事、传闻、泛科技、营销软文、教程和普通模型更新不会因公众号知名而获得豁免。公众号粉丝数不计入文章的 `community_signals`。

## 当前来源矩阵

| 平台 | 当前状态 | 采集依据 | 热度依据 | 事实定位 |
|---|---|---|---|---|
| 量子位 | 已启用 | [官网公开文章 API](https://www.qbitai.com/wp-json/wp/v2/posts)，读取正文、原文链接、`date_gmt` | 公众号规模仅用于选择来源，不充当单篇热度 | 二级媒体报道，进入原有新闻筛选 |
| 新智元 | 已启用 | [官网公开文章 API](https://aiera.com.cn/wp-json/wp/v2/posts)，读取正文、原文链接、`date_gmt` | 同上 | 二级媒体报道，进入原有新闻筛选 |
| Hacker News | 已启用 | Y Combinator 官方 Firebase API 的 `topstories` 与 `item` | 当前 Top 30，且 100 points 或 30 评论至少满足一项 | 海外技术社区讨论信号；外链仍需单独验证 |
| 掘金 | 已启用 | 掘金网页使用的人工智能分类热榜 HTTPS 接口，并访问原文公开 Schema.org 元数据 | 当前 Top 20，且 `hot_rank` 300 或 10 评论至少满足一项；同时保存浏览/点赞/收藏 | 国内技术社区讨论信号；原文日期来自 `BlogPosting.datePublished`，绝不以采集时间冒充 |
| CSDN | 适配器已实现、默认停用 | CSDN 公开热榜页使用的 `hot-rank` HTTPS 接口 | 热榜分数、浏览、评论、收藏 | 原文页当前对 CI 类自动化请求返回 HTTP 521，无法稳定取得 `article:published_time`；在恢复可靠日期核验前不进入默认工作流 |
| 知乎 | 不接入 | 按用户选择移除采集器及密钥配置 | 不采集 | 国内来源保留量子位、新智元和掘金，无需新增知乎接口授权或额度 |
| 机器之心 | 暂未启用 | 本次官网和 RSS 请求均转到数据服务介绍页，未返回可用文章列表 | 不推测 | 不将 HTTP 200 的介绍页当成成功采集；取得可用官方接入后再增加 |
| 其他公众号 | 未启用 | 不采集任意公众号或使用来源不明的公共聚合器 | 不推测 | 后续按关注规模、AI 专业性与来源可用性逐个核验 |
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

新增来源没有修改新闻或论文 Prompt、规则版本和预算。

## 国内媒体采集

量子位、新智元及掘金采用公开来源，无需新增新闻数据服务订阅、社区账号或密钥。移除知乎后，不会向其接口发送请求，也不会再产生缺少知乎密钥的来源健康提示。阿里云触发器无需调整。

两家媒体使用官网公开的 [WordPress Posts 接口](https://developer.wordpress.org/rest-api/reference/posts/)，单次最多读取各 40 篇公开文章，供现有时间窗筛选。媒体原文发布时间来自 UTC `date_gmt`，不使用修改日期。读取正文而非 RSS 短摘要，避免只有夸张标题而没有事实依据；解析失败或跳转到介绍页会明确记录为来源不可用，不误报为「今日无更新」。

## 单个社区源失败时

社区适配器与 RSS 源遵循相同的失败隔离：单个来源超时只会写入来源健康记录，不会阻止其他来源、论文采集、Pages 构建或邮件发送。若 DeepSeek 或总预算失败，工作流仍会整体停止，保留上一版成功页面。

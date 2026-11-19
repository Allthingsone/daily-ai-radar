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
| 知乎 | 已实现；配置密钥后采集 | [官方搜索 API](https://developer.zhihu.com/docs?key=zhihu_search) 的 AI 查询，读取 `CreateTime`、作者和原链接 | 100 赞同或 30 评论至少满足一项；搜索名次不当作热榜排名 | 社区讨论信号，只能进入 `community-trending`；未配置密钥时明确记录跳过，不发送请求 |
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

知乎没有榜单名次，`community_rank_limit` 保持为 0，只按实际赞同/评论达标。新增来源没有修改新闻或论文 Prompt、规则版本和预算。

## 知乎接入

1. 登录[知乎数据开放平台](https://developer.zhihu.com/)，在个人中心创建 Access Secret。接口权限、额度与价格以平台账户页面为准，程序不会代为开户或购买额度。
2. 在本仓库 `Settings → Secrets and variables → Actions → Secrets` 添加 `ZHIHU_ACCESS_SECRET`。工作流已将它传给早间新闻采集和发布阶段的新闻补采；阿里云函数不需要保存这个密钥，也无需修改触发器。
3. 本机运行时将同名变量放入进程环境。不要把密钥提交到配置文件或写入聊天。

默认检索「人工智能、大模型、AI Agent、具身智能、自动驾驶」，单次采集最多 5 个请求、每个最多 10 条，再进入统一语义筛选。这些查询只用于发现候选，不改变入选方向。请求使用 Bearer 和秒级时间戳鉴权，禁止携带凭据跟随重定向。缺失密钥、鉴权失败或限流不会循环重试；已经取得的有效讨论可以保留，其他新闻来源继续工作。

官方 `hot_list` 不返回发布时间，无法满足项目的新闻时间核验。因此改用 `zhihu_search` 的 `CreateTime`，而不是以抓取时间或可能代表修改时间的 `EditTime` 冒充发布时间。返回缺少创建时间的条目会被丢弃；旧帖即使刚被修改，也不会进入最近 48 小时窗口。

两家媒体使用官网公开的 [WordPress Posts 接口](https://developer.wordpress.org/rest-api/reference/posts/)，单次最多读取各 40 篇公开文章，供现有时间窗筛选。媒体原文发布时间来自 UTC `date_gmt`，不使用修改日期。读取正文而非 RSS 短摘要，避免只有夸张标题而没有事实依据；解析失败或跳转到介绍页会明确记录为来源不可用，不误报为「今日无更新」。

## 单个社区源失败时

社区适配器与 RSS 源遵循相同的失败隔离：单个来源超时只会写入来源健康记录，不会阻止其他来源、论文采集、Pages 构建或邮件发送。若 DeepSeek 或总预算失败，工作流仍会整体停止，保留上一版成功页面。

# 修改筛选 Prompt

四个文件分别控制共同事实边界、新闻筛选、论文高召回初筛和严格复筛：

- `system.md`
- `news_screening.md`
- `paper_triage.md`
- `paper_screening.md`

修改流程：

1. 直接编辑对应 Markdown 文件。
2. 新闻、论文初筛和论文复筛 Prompt 必须保留 `{{schema_json}}` 与 `{{candidates_json}}`。
3. 不要删除 Prompt 要求的 JSON 字段；这些字段会由 `llm.py` 严格校验。
4. 在 `config/settings.yaml` 中递增 `prompt_version`，并同步 `src/daily_radar/config.py` 的默认值与演示数据版本。当前版本为 `2026-09-17-v4`（新增自动驾驶/室内 VLN）。
5. 运行以下离线检查，不会调用 DeepSeek：

```bash
daily-radar validate-prompts
python -m unittest discover -s tests -v
```

6. 推送后先在 GitHub Actions 手动执行一次工作流，观察筛选结果和 Token 用量，再等待下一次定时运行。

论文初筛和严格复筛会分别保存自己的 Prompt SHA-256；每条正式结果都会保存当前 `prompt_version`。页面、导出和邮件只读取当前版本的结果，因此修改规则后不会把旧 Prompt 的判断混入当前 Feed。采集成功记录也保存版本，`publish` 只复用当天且版本相同的结果，避免新规则被同日旧结果跳过；未记录版本的旧缓存会重新筛选。若当天已完整发布，手动工作流仍需勾选 `force` 才能越过当日发送去重，且会重新消耗预算并发送邮件。

论文规则包含两条并列路径：自动驾驶多模态，或自动驾驶/室内 VLN。调整范围时必须同时修改 `paper_triage.md` 与 `paper_screening.md`，避免初筛先淘汰新方向；增加布尔字段或维度时也必须同步 `llm.py` 的 JSON 示例、校验与测试。新闻 Prompt 不受此次 VLN 扩展影响。

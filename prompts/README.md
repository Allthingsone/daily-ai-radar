# 修改筛选 Prompt

三个文件分别控制共同事实边界、新闻筛选和论文筛选：

- `system.md`
- `news_screening.md`
- `paper_screening.md`

修改流程：

1. 直接编辑对应 Markdown 文件。
2. 新闻和论文 Prompt 必须保留 `{{schema_json}}` 与 `{{candidates_json}}`。
3. 不要删除 Prompt 要求的 JSON 字段；这些字段会由 `llm.py` 严格校验。
4. 在 `config/settings.yaml` 中递增 `prompt_version`，例如从 `2026-08-29-v1` 改为 `2026-08-29-v2`。
5. 运行以下离线检查，不会调用 DeepSeek：

```bash
daily-radar validate-prompts
python -m unittest discover -s tests -v
```

6. 推送后先在 GitHub Actions 手动执行一次工作流，观察筛选结果和 Token 用量，再等待下一次定时运行。

每条正式结果都会保存 `prompt_version` 和两个 Prompt 文件合并后的 SHA-256。页面、导出和邮件只读取当前 `prompt_version` 的结果，因此修改规则后不会把旧 Prompt 的判断混入当前 Feed。

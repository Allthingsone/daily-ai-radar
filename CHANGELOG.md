# Changelog

## 0.6.0 - 2026-08-30

- Added five Asia/Shanghai schedule attempts at 07:30, 07:50, 08:10, 08:30, and 08:50, guarded by a date-scoped successful-delivery marker.
- Prevented backup triggers from cancelling active runs, and made SMTP failure eligible for the next scheduled retry.
- Added a manual `force` input for intentional same-day reruns without weakening automatic deduplication.
- Restricted model news to major foundation LLM or multimodal model releases and material upgrades.
- Restricted dataset and benchmark news to autonomous-driving applications.
- Required research-result news to be both important and backed by a collector-verified community heat signal.
- Added a separate `community-trending` route for substantive, highly discussed AI topics.
- Added the official Hacker News API adapter with points, comment counts, rank, and discussion URLs.
- Added a Juejin AI hot-list adapter with rank, heat, views, likes, comments, favorites, and Schema.org publication-time verification.
- Kept the CSDN hot-list adapter available but disabled it by default because CI-style article requests currently cannot reliably verify publication dates.
- Restricted community-originated items to the `community-trending` route so a discussion post cannot become proof of an official release or research result.
- Prevented DeepSeek from inventing heat: collector metadata is the final gate even when the model claims a topic is trending.
- Preserved community metrics through event deduplication and displayed them on Pages as discussion signals rather than proof of fact.

## 0.5.1 - 2026-08-30

- Raised the V4-Pro output allowance from 8,192 to 32,768 tokens and the request timeout from 180 to 600 seconds for max-effort reasoning plus structured JSON.
- Reduced default candidate batches and added recursive splitting when DeepSeek returns `finish_reason=length`, avoiding an identical paid retry.
- Added validation feedback to non-truncation retries instead of sending the same prompt again.
- Printed tracked tokens and estimated cost even when screening fails.
- Added a same-day GitHub Actions state cache that is saved on failure, so billed failed-call usage is restored before another run and remains subject to the project caps.

## 0.5.0 - 2026-08-29

- Replaced deterministic production selection with mandatory `deepseek-v4-pro` semantic screening in Thinking `max` mode.
- Kept URL/domain, publication-time, source provenance, and arXiv identity checks outside the model.
- Excluded entries whose source does not provide a parseable publication timestamp, and guarded against implausible future timestamps.
- Added separately editable system, news, and paper prompts with prompt version and SHA-256 audit metadata.
- Added strict JSON decision validation and fail-closed behavior; there is no automatic Flash or keyword-score fallback.
- Added per-call prompt/completion/reasoning/cache token accounting and estimated peak/off-peak DeepSeek cost.
- Added 250k-token and USD 1 daily caps, including same-day usage carry-forward between fresh GitHub runners.
- Added DeepSeek usage to the Pages snapshot, JSON output, CLI status, and workflow logs.
- Added optional 163 SMTP daily email using one account as sender and recipient with credentials stored only in Actions Secrets.

## 0.4.0 - 2026-08-29

- Added a self-contained static site builder for GitHub Pages.
- Added client-side news/paper, important/all, paper today/recent, and text-search controls.
- Published per-item provenance, collection runs, and source-health evidence in the static snapshot.
- Added a daily GitHub Actions workflow that tests, collects, builds, and deploys at 08:37 Asia/Shanghai.
- Added JSON, Markdown, and RSS links to the Pages site and a configurable RSS channel URL.
- Added `DAILY_RADAR_USER_AGENT` so deployments can identify their arXiv contact without committing an email address.
- Added regression coverage ensuring old papers remain available in the recent view but never enter today's digest.

## 0.3.0 - 2026-08-03

- Changed news scope from broad AI coverage to concrete release/result events across all AI fields.
- Added a hard two-part news gate: release action plus technical artifact, or result action plus research evidence.
- Kept multimodal and autonomous-driving terms as ranking preferences rather than news requirements.
- Added title-anchored checks for editorial/community sources to avoid old events mentioned deep in an article.
- Excluded sponsored, paid and advertorial content from the news feed.
- Added model, research-result, dataset/benchmark, open-source tool, product/API and hardware/robotics event types.
- Versioned news eligibility so records accepted by older broad rules no longer appear in the default feed.

## 0.2.1 - 2026-08-03

- Made the default paper view use the Asia/Shanghai calendar day.
- Kept the 96-hour paper window as an explicit backfill view instead of presenting it as today.
- Added Today / Recent / History period controls and full publication timestamps.
- Made daily exports include only today's papers by default, with explicit CLI overrides.
- Added regression tests for local-day boundaries and stale-paper exclusion.

## 0.2.0 - 2026-08-03

- Physically separated demo data from the real database.
- Removed all preview-only records from the active database.
- Added per-source allowed-domain policies and feed redirect validation.
- Added item URL reachability checks and final-domain verification.
- Added arXiv ID-to-official-URL identity checks.
- Added source health history, source API and dashboard provenance panels.
- Added `verify` and `purge-demo` CLI commands.
- Restricted the default dashboard and exports to verified records.
- Added bounded network retries and sequential recovery for failed feeds.

## 0.1.0 - 2026-08-03

- Initial RSS/Atom and arXiv collectors.
- Transparent news and paper scoring.
- SQLite, CLI, dashboard, feedback and multi-format exports.

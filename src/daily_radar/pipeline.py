from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import List
from zoneinfo import ZoneInfo

from .collectors import (
    ArxivCollector,
    CSDNHotCollector,
    HackerNewsCollector,
    JuejinHotCollector,
    RSSCollector,
)
from .config import Settings, SourceConfig, load_sources
from .db import Database
from .llm import DeepSeekScreener
from .models import CollectionResult, RadarItem, RunSummary
from .processing.dedup import cluster_news, deduplicate_exact
from .verification import arxiv_api_verification, verify_news_items


class RadarPipeline:
    def __init__(self, settings: Settings, database: Database = None) -> None:
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.database.initialize()
        self.screener = DeepSeekScreener(
            settings.llm, self.database, settings.timezone
        )

    def _has_successful_run_today(self, kind: str, now: datetime) -> bool:
        local_now = now.astimezone(ZoneInfo(self.settings.timezone))
        local_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        local_end = local_start + timedelta(days=1)
        return self.database.has_successful_run(
            kind,
            local_start.astimezone(timezone.utc),
            local_end.astimezone(timezone.utc),
        )

    def _news_collector(self, source: SourceConfig):
        if source.adapter == "hackernews":
            return HackerNewsCollector(source, self.settings.network)
        if source.adapter == "csdn-hot":
            return CSDNHotCollector(source, self.settings.network)
        if source.adapter == "juejin-hot":
            return JuejinHotCollector(source, self.settings.network)
        if source.adapter != "rss":
            raise ValueError(f"Unsupported source adapter: {source.adapter}")
        return RSSCollector(source, self.settings.network)

    def collect_news(self) -> RunSummary:
        self.screener.ensure_ready()
        started = datetime.now(timezone.utc)
        sources = load_sources(self.settings.sources_path)
        source_map = {source.id: source for source in sources}
        results: List[CollectionResult] = []
        workers = min(6, max(1, len(sources)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(self._news_collector(source).collect): source
                for source in sources
            }
            for future in as_completed(futures):
                results.append(future.result())

        # Some publishers reset concurrent feed connections sporadically.
        # Retry only failed sources once more, sequentially, before declaring
        # the source unhealthy for this run.
        recovered: List[CollectionResult] = []
        for result in results:
            if not result.error:
                recovered.append(result)
                continue
            source = source_map[result.source_id]
            retry_result = self._news_collector(source).collect()
            recovered.append(retry_result)
        results = recovered

        errors = [f"{result.source_id}: {result.error}" for result in results if result.error]
        raw_items = [item for result in results for item in result.items]
        cutoff = started - timedelta(hours=self.settings.news.lookback_hours)
        future_limit = started + timedelta(hours=2)
        recent = [
            item
            for item in raw_items
            if bool(item.metadata.get("published_at_verified"))
            and cutoff <= item.published_at <= future_limit
        ]
        merged = cluster_news(
            deduplicate_exact(recent), self.settings.news.cluster_similarity
        )
        # Verify every recent clustered candidate before sending its public
        # title/summary to DeepSeek. Keyword scores no longer decide selection.
        verified = verify_news_items(merged, source_map, self.settings.network)
        screened = self.screener.screen(verified, "news")
        accepted = [
            item for item in screened
            if bool(item.metadata.get("llm_screening", {}).get("selected"))
        ]
        local_today = started.astimezone(ZoneInfo(self.settings.timezone)).date()
        ranked = sorted(
            accepted,
            key=lambda item: (
                item.published_at.astimezone(
                    ZoneInfo(self.settings.timezone)
                ).date()
                == local_today,
                item.score,
            ),
            reverse=True,
        )
        important = ranked[: self.settings.news.max_important]
        important_ids = {id(item) for item in important}
        for item in screened:
            item.is_important = id(item) in important_ids
            item.id = self.database.upsert_item(item)

        finished = datetime.now(timezone.utc)
        summary = RunSummary(
            kind="news",
            started_at=started,
            finished_at=finished,
            fetched=len(raw_items),
            accepted=len(accepted),
            important=sum(item.is_important for item in accepted),
            sources_ok=sum(not result.error for result in results),
            sources_failed=sum(bool(result.error) for result in results),
            errors=errors,
            details={
                "raw_items": len(raw_items),
                "within_time_window": len(recent),
                "clustered_candidates": len(merged),
                "verified_candidates": len(verified),
                "llm_screened": len(screened),
                "accepted": len(accepted),
            },
        )
        summary.run_id = self.database.record_run(summary)
        self.database.record_source_checks(summary.run_id, results)
        return summary

    def collect_papers(self) -> RunSummary:
        self.screener.ensure_ready()
        started = datetime.now(timezone.utc)
        collector = ArxivCollector(
            self.settings.papers,
            self.settings.network,
            timezone_name=self.settings.timezone,
        )
        published_since, published_before = collector.daily_window(started)
        result = collector.collect(now=started)
        errors = [f"arxiv: {result.error}"] if result.error else []
        today_new = [
            item
            for item in result.items
            if bool(item.metadata.get("published_at_verified"))
            and bool(item.metadata.get("is_new_submission"))
            and published_since <= item.published_at < published_before
        ]
        verified: List[RadarItem] = []
        for item in deduplicate_exact(today_new):
            provenance = arxiv_api_verification(
                item, result.final_url or result.source_url
            )
            item.metadata["provenance"] = {
                "status": provenance.status,
                "checked_at": provenance.checked_at,
                "original_url": provenance.original_url,
                "final_url": provenance.final_url,
                "http_status": provenance.http_status,
                "domain": provenance.domain,
                "domain_match": provenance.domain_match,
                "source_tier": provenance.source_tier,
                "method": provenance.method,
                "reason": provenance.reason,
            }
            item.metadata["source_record_url"] = item.canonical_url
            if not provenance.usable:
                continue
            verified.append(item)
        # Every verified new paper reaches a compact, high-recall V4-Pro
        # triage.  Only plausible candidates consume full-abstract Thinking-max
        # screening; budget exhaustion aborts rather than publishing a partial
        # daily set.
        screened = self.screener.screen_papers_two_stage(verified)
        accepted = [
            item for item in screened
            if bool(item.metadata.get("llm_screening", {}).get("selected"))
        ]
        local_today = started.astimezone(ZoneInfo(self.settings.timezone)).date()
        ranked = sorted(
            accepted,
            key=lambda item: (
                item.published_at.astimezone(
                    ZoneInfo(self.settings.timezone)
                ).date()
                == local_today,
                item.score,
            ),
            reverse=True,
        )
        important = ranked[: self.settings.papers.max_important]
        important_ids = {id(item) for item in important}
        for item in screened:
            item.is_important = id(item) in important_ids
            item.id = self.database.upsert_item(item)

        finished = datetime.now(timezone.utc)
        summary = RunSummary(
            kind="paper",
            started_at=started,
            finished_at=finished,
            fetched=len(result.items),
            accepted=len(accepted),
            important=len(important),
            sources_ok=0 if result.error else 1,
            sources_failed=1 if result.error else 0,
            errors=errors,
            details={
                "daily_query_items": len(result.items),
                "verified_new_submissions": len(verified),
                "triage_candidates": sum(
                    bool(item.metadata.get("paper_triage", {}).get("candidate"))
                    for item in screened
                ),
                "triage_rejected": sum(
                    not bool(item.metadata.get("paper_triage", {}).get("candidate"))
                    for item in screened
                ),
                "strict_screened": sum(
                    bool(item.metadata.get("paper_triage", {}).get("candidate"))
                    for item in screened
                ),
                "accepted": len(accepted),
                "page_size": self.settings.papers.page_size,
            },
        )
        summary.run_id = self.database.record_run(summary)
        self.database.record_source_checks(summary.run_id, [result])
        return summary

    def collect(self, kind: str = "all") -> List[RunSummary]:
        if kind == "news":
            return [self.collect_news()]
        if kind == "paper":
            return [self.collect_papers()]
        if kind == "all":
            # Paper readiness is checked first because arXiv's 20:00 Eastern
            # announcement becomes available at 08:00 or 09:00 in Shanghai.
            # A too-early backup trigger should fail before spending any news
            # screening tokens; the next scheduled attempt can then retry.
            paper = self.collect_papers()
            if paper.sources_failed:
                return [paper]
            return [paper, self.collect_news()]
        if kind == "publish":
            # The externally scheduled news phase normally finishes before
            # arXiv's 08:00/09:00 Shanghai announcement. Reuse its same-day
            # database state so the final phase only has to screen papers.
            # Successful paper state is reusable too when email or Pages
            # deployment failed after collection and a watchdog retries.
            started = datetime.now(timezone.utc)
            summaries: List[RunSummary] = []
            if not self._has_successful_run_today("paper", started):
                paper = self.collect_papers()
                summaries.append(paper)
                if paper.sources_failed:
                    return summaries
            if not self._has_successful_run_today("news", started):
                summaries.append(self.collect_news())
            return summaries
        raise ValueError("kind must be news, paper, all, or publish")

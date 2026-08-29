from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

from .collectors import ArxivCollector, RSSCollector
from .config import Settings, load_sources
from .db import Database
from .llm import OptionalLLMEnricher
from .models import CollectionResult, RadarItem, RunSummary
from .processing.dedup import cluster_news, deduplicate_exact
from .processing.scoring import score_news, score_paper
from .verification import arxiv_api_verification, verify_news_items


class RadarPipeline:
    def __init__(self, settings: Settings, database: Database = None) -> None:
        self.settings = settings
        self.database = database or Database(settings.database_path)
        self.database.initialize()
        self.enricher = OptionalLLMEnricher(settings.llm)

    def collect_news(self) -> RunSummary:
        started = datetime.now(timezone.utc)
        sources = load_sources(self.settings.sources_path)
        source_map = {source.id: source for source in sources}
        results: List[CollectionResult] = []
        workers = min(6, max(1, len(sources)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(RSSCollector(source, self.settings.network).collect): source
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
            retry_result = RSSCollector(source, self.settings.network).collect()
            recovered.append(retry_result)
        results = recovered

        errors = [f"{result.source_id}: {result.error}" for result in results if result.error]
        raw_items = [item for result in results for item in result.items]
        cutoff = started - timedelta(hours=self.settings.news.lookback_hours)
        recent = [item for item in raw_items if item.published_at >= cutoff]
        merged = cluster_news(
            deduplicate_exact(recent), self.settings.news.cluster_similarity
        )
        preliminary = [
            score_news(item, self.settings.news.personal_keywords, started)
            for item in merged
        ]
        candidates = [
            item
            for item in preliminary
            if bool(item.metadata.get("news_gate", {}).get("passed"))
            and item.component_scores.get("relevance", 0) >= self.settings.news.min_relevance
            and item.score >= max(20.0, self.settings.news.min_score * 0.55)
        ]
        verified = verify_news_items(
            candidates, source_map, self.settings.network
        )
        rescored = [
            score_news(item, self.settings.news.personal_keywords, started)
            for item in verified
        ]
        accepted = [
            item
            for item in rescored
            if bool(item.metadata.get("news_gate", {}).get("passed"))
            and item.component_scores.get("relevance", 0) >= self.settings.news.min_relevance
            and item.score >= max(20.0, self.settings.news.min_score * 0.55)
        ]
        ranked = sorted(accepted, key=lambda item: item.score, reverse=True)
        important = [
            item for item in ranked if item.score >= self.settings.news.min_score
        ][: self.settings.news.max_important]
        important_ids = {id(item) for item in important}
        for item in accepted:
            item.is_important = id(item) in important_ids
        errors.extend(self._enrich_and_store(accepted))

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
        )
        summary.run_id = self.database.record_run(summary)
        self.database.record_source_checks(summary.run_id, results)
        return summary

    def collect_papers(self) -> RunSummary:
        started = datetime.now(timezone.utc)
        result = ArxivCollector(self.settings.papers, self.settings.network).collect()
        errors = [f"arxiv: {result.error}"] if result.error else []
        cutoff = started - timedelta(hours=self.settings.papers.lookback_hours)
        recent = [item for item in result.items if item.published_at >= cutoff]
        accepted: List[RadarItem] = []
        for item in deduplicate_exact(recent):
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
            passed, scored = score_paper(
                item, self.settings.papers.personal_keywords, started
            )
            if passed:
                accepted.append(scored)
        ranked = sorted(accepted, key=lambda item: item.score, reverse=True)
        important = [
            item for item in ranked if item.score >= self.settings.papers.min_score
        ][: self.settings.papers.max_important]
        important_ids = {id(item) for item in important}
        for item in accepted:
            item.is_important = id(item) in important_ids
        errors.extend(self._enrich_and_store(accepted))

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
            return [self.collect_news(), self.collect_papers()]
        raise ValueError("kind must be news, paper, or all")

    def _enrich_and_store(self, items: Iterable[RadarItem]) -> List[str]:
        errors: List[str] = []
        for item in items:
            if item.is_important and self.enricher.enabled:
                error = self.enricher.enrich(item)
                if error:
                    errors.append(error)
            item.id = self.database.upsert_item(item)
        return errors

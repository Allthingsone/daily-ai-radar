from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .eligibility import LLM_SCREENING_RULE_VERSION
from .models import CollectionResult, RadarItem, RunSummary


SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('news', 'paper')),
    external_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_tier INTEGER NOT NULL,
    source_focus REAL NOT NULL,
    published_at TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    category TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    authors_json TEXT NOT NULL,
    score REAL NOT NULL,
    component_scores_json TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    cluster_key TEXT NOT NULL,
    is_important INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(kind, canonical_url)
);

CREATE INDEX IF NOT EXISTS idx_items_kind_published
ON items(kind, published_at DESC);

CREATE INDEX IF NOT EXISTS idx_items_important_score
ON items(is_important, score DESC);

CREATE INDEX IF NOT EXISTS idx_items_cluster
ON items(cluster_key);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    value TEXT NOT NULL CHECK (value IN ('saved', 'not_relevant', 'read')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_item_created
ON feedback(item_id, created_at DESC);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    fetched INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    important INTEGER NOT NULL,
    sources_ok INTEGER NOT NULL,
    sources_failed INTEGER NOT NULL,
    errors_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    final_url TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    http_status INTEGER NOT NULL DEFAULT 0,
    item_count INTEGER NOT NULL DEFAULT 0,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    domain_match INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_source_checks_source_id
ON source_checks(source_id, id DESC);

CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    local_date TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 1,
    request_items INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
    cache_miss_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL NOT NULL DEFAULT 0,
    pricing_tier TEXT NOT NULL DEFAULT '',
    response_id TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    prompt_sha256 TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL CHECK (status IN ('success', 'failed')),
    error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_local_date
ON llm_usage(local_date, id DESC);
"""


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(source_checks)").fetchall()
            }
            if "domain_match" not in columns:
                connection.execute(
                    "ALTER TABLE source_checks ADD COLUMN domain_match INTEGER NOT NULL DEFAULT 0"
                )
            usage_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(llm_usage)").fetchall()
            }
            if "prompt_version" not in usage_columns:
                connection.execute(
                    "ALTER TABLE llm_usage ADD COLUMN prompt_version TEXT NOT NULL DEFAULT ''"
                )
            if "prompt_sha256" not in usage_columns:
                connection.execute(
                    "ALTER TABLE llm_usage ADD COLUMN prompt_sha256 TEXT NOT NULL DEFAULT ''"
                )
            if "call_count" not in usage_columns:
                connection.execute(
                    "ALTER TABLE llm_usage ADD COLUMN call_count INTEGER NOT NULL DEFAULT 1"
                )

    def upsert_item(self, item: RadarItem) -> int:
        now = datetime.now(timezone.utc).isoformat()
        values = (
            item.kind,
            item.external_id,
            item.title,
            item.summary,
            item.url,
            item.canonical_url,
            item.source_id,
            item.source_name,
            item.source_type,
            item.source_tier,
            item.source_focus,
            _iso(item.published_at),
            _iso(item.fetched_at),
            item.category,
            _dumps(item.categories),
            _dumps(item.tags),
            _dumps(item.authors),
            item.score,
            _dumps(item.component_scores),
            _dumps(item.reasons),
            _dumps(item.metadata),
            item.fingerprint,
            item.cluster_key,
            int(item.is_important),
            now,
            now,
        )
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO items (
                    kind, external_id, title, summary, url, canonical_url,
                    source_id, source_name, source_type, source_tier, source_focus,
                    published_at, fetched_at, category, categories_json, tags_json,
                    authors_json, score, component_scores_json, reasons_json,
                    metadata_json, fingerprint, cluster_key, is_important,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(kind, canonical_url) DO UPDATE SET
                    external_id=excluded.external_id,
                    title=excluded.title,
                    summary=excluded.summary,
                    url=excluded.url,
                    source_id=excluded.source_id,
                    source_name=excluded.source_name,
                    source_type=excluded.source_type,
                    source_tier=excluded.source_tier,
                    source_focus=excluded.source_focus,
                    published_at=excluded.published_at,
                    fetched_at=excluded.fetched_at,
                    category=excluded.category,
                    categories_json=excluded.categories_json,
                    tags_json=excluded.tags_json,
                    authors_json=excluded.authors_json,
                    score=excluded.score,
                    component_scores_json=excluded.component_scores_json,
                    reasons_json=excluded.reasons_json,
                    metadata_json=excluded.metadata_json,
                    fingerprint=excluded.fingerprint,
                    cluster_key=excluded.cluster_key,
                    is_important=excluded.is_important,
                    updated_at=excluded.updated_at
                """,
                values,
            )
            row = connection.execute(
                "SELECT id FROM items WHERE kind = ? AND canonical_url = ?",
                (item.kind, item.canonical_url),
            ).fetchone()
            if row is None:
                raise RuntimeError("Unable to resolve upserted item")
            return int(row["id"])

    def record_run(self, summary: RunSummary) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO runs (
                    kind, started_at, finished_at, fetched, accepted, important,
                    sources_ok, sources_failed, errors_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.kind,
                    _iso(summary.started_at),
                    _iso(summary.finished_at),
                    summary.fetched,
                    summary.accepted,
                    summary.important,
                    summary.sources_ok,
                    summary.sources_failed,
                    _dumps(summary.errors),
                ),
            )
            return int(cursor.lastrowid)

    def record_source_checks(
        self, run_id: int, results: List[CollectionResult]
    ) -> None:
        checked_at = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                run_id,
                result.source_id,
                result.source_name or result.source_id,
                result.source_url,
                result.final_url,
                checked_at,
                int(not result.error and result.domain_match),
                result.http_status,
                len(result.items),
                result.elapsed_ms,
                result.error,
                int(result.domain_match),
            )
            for result in results
        ]
        if not rows:
            return
        with self.connect() as connection:
            connection.executemany(
                """
                INSERT INTO source_checks (
                    run_id, source_id, source_name, source_url, final_url,
                    checked_at, success, http_status, item_count, elapsed_ms, error
                    , domain_match
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def record_llm_usage(self, record: Dict[str, Any]) -> int:
        """Persist billable usage without ever storing prompts or API secrets."""

        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO llm_usage (
                    occurred_at, local_date, provider, model, purpose,
                    call_count, request_items, prompt_tokens, completion_tokens,
                    reasoning_tokens, cache_hit_tokens, cache_miss_tokens,
                    total_tokens, estimated_cost_usd, pricing_tier,
                    response_id, prompt_version, prompt_sha256, status, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.get("occurred_at", "")),
                    str(record.get("local_date", "")),
                    str(record.get("provider", "")),
                    str(record.get("model", "")),
                    str(record.get("purpose", "")),
                    max(1, int(record.get("call_count", 1) or 1)),
                    int(record.get("request_items", 0) or 0),
                    int(record.get("prompt_tokens", 0) or 0),
                    int(record.get("completion_tokens", 0) or 0),
                    int(record.get("reasoning_tokens", 0) or 0),
                    int(record.get("cache_hit_tokens", 0) or 0),
                    int(record.get("cache_miss_tokens", 0) or 0),
                    int(record.get("total_tokens", 0) or 0),
                    float(record.get("estimated_cost_usd", 0) or 0),
                    str(record.get("pricing_tier", "")),
                    str(record.get("response_id", "")),
                    str(record.get("prompt_version", "")),
                    str(record.get("prompt_sha256", "")),
                    str(record.get("status", "failed")),
                    str(record.get("error", ""))[:1000],
                ),
            )
            return int(cursor.lastrowid)

    def llm_usage_summary(self, local_date: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(call_count) AS calls,
                    SUM(CASE WHEN status = 'success' THEN call_count ELSE 0 END) AS successful_calls,
                    SUM(CASE WHEN status = 'failed' THEN call_count ELSE 0 END) AS failed_calls,
                    SUM(request_items) AS request_items,
                    SUM(prompt_tokens) AS prompt_tokens,
                    SUM(completion_tokens) AS completion_tokens,
                    SUM(reasoning_tokens) AS reasoning_tokens,
                    SUM(cache_hit_tokens) AS cache_hit_tokens,
                    SUM(cache_miss_tokens) AS cache_miss_tokens,
                    SUM(total_tokens) AS total_tokens,
                    SUM(estimated_cost_usd) AS estimated_cost_usd
                FROM llm_usage
                WHERE local_date = ?
                """,
                (local_date,),
            ).fetchone()
        integer_keys = (
            "calls",
            "successful_calls",
            "failed_calls",
            "request_items",
            "prompt_tokens",
            "completion_tokens",
            "reasoning_tokens",
            "cache_hit_tokens",
            "cache_miss_tokens",
            "total_tokens",
        )
        result: Dict[str, Any] = {
            key: int(row[key] or 0) for key in integer_keys
        }
        result["estimated_cost_usd"] = round(
            float(row["estimated_cost_usd"] or 0), 6
        )
        result["local_date"] = local_date
        return result

    def recent_llm_usage(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM llm_usage ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_feedback(self, item_id: int, value: str, note: str = "") -> None:
        if value not in {"saved", "not_relevant", "read"}:
            raise ValueError("Unsupported feedback value")
        with self.connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"Item {item_id} not found")
            connection.execute(
                "INSERT INTO feedback (item_id, value, note, created_at) VALUES (?, ?, ?, ?)",
                (item_id, value, note[:500], datetime.now(timezone.utc).isoformat()),
            )

    def update_item_metadata(self, item_id: int, metadata: Dict[str, Any]) -> None:
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE items SET metadata_json = ?, updated_at = ? WHERE id = ?",
                (_dumps(metadata), datetime.now(timezone.utc).isoformat(), item_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Item {item_id} not found")

    def purge_demo(self) -> int:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM items WHERE metadata_json LIKE '%\"demo\":true%'"
            )
            return int(cursor.rowcount)

    def list_items(
        self,
        kind: Optional[str] = None,
        important_only: bool = False,
        category: str = "",
        query: str = "",
        limit: int = 100,
        verified_only: bool = False,
        published_since: Optional[datetime] = None,
        eligible_only: bool = False,
        prompt_version: str = "",
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        if important_only:
            clauses.append("is_important = 1")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if query:
            clauses.append("(title LIKE ? OR summary LIKE ? OR tags_json LIKE ?)")
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        if verified_only:
            clauses.append(
                "(metadata_json LIKE '%\"status\":\"verified-%' "
                "OR metadata_json LIKE '%\"status\":\"access-restricted\"%')"
            )
        if published_since is not None:
            clauses.append("published_at >= ?")
            params.append(_iso(published_since))
        if eligible_only:
            clauses.append(
                "(COALESCE(json_extract(metadata_json, '$.llm_screening.selected'), 0) = 1 "
                "AND json_extract(metadata_json, '$.llm_screening.rule_version') = ?)"
            )
            params.append(LLM_SCREENING_RULE_VERSION)
            if prompt_version:
                clauses.append(
                    "json_extract(metadata_json, '$.llm_screening.prompt_version') = ?"
                )
                params.append(prompt_version)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 500)))
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT items.*,
                       (SELECT value FROM feedback
                        WHERE feedback.item_id = items.id
                        ORDER BY feedback.created_at DESC LIMIT 1) AS feedback_value
                FROM items
                """
                + where
                + " ORDER BY published_at DESC, score DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def stats(
        self,
        verified_only: bool = False,
        published_since: Optional[datetime] = None,
        eligible_only: bool = False,
        prompt_version: str = "",
    ) -> Dict[str, int]:
        clauses: List[str] = []
        params: List[Any] = []
        if verified_only:
            clauses.append(
                "(metadata_json LIKE '%\"status\":\"verified-%' "
                "OR metadata_json LIKE '%\"status\":\"access-restricted\"%')"
            )
        if published_since is not None:
            clauses.append("published_at >= ?")
            params.append(_iso(published_since))
        if eligible_only:
            clauses.append(
                "(COALESCE(json_extract(metadata_json, '$.llm_screening.selected'), 0) = 1 "
                "AND json_extract(metadata_json, '$.llm_screening.rule_version') = ?)"
            )
            params.append(LLM_SCREENING_RULE_VERSION)
            if prompt_version:
                clauses.append(
                    "json_extract(metadata_json, '$.llm_screening.prompt_version') = ?"
                )
                params.append(prompt_version)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN kind = 'news' THEN 1 ELSE 0 END) AS news,
                    SUM(CASE WHEN kind = 'paper' THEN 1 ELSE 0 END) AS papers,
                    SUM(CASE WHEN is_important = 1 THEN 1 ELSE 0 END) AS important
                FROM items
                """
                + where,
                params,
            ).fetchone()
        return {key: int(row[key] or 0) for key in ("total", "news", "papers", "important")}

    def recent_runs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["errors"] = _loads(item.pop("errors_json"), [])
            result.append(item)
        return result

    def recent_source_checks(self, latest_only: bool = True) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM source_checks ORDER BY id DESC LIMIT 500"
            ).fetchall()
        result: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            item = dict(row)
            if latest_only and item["source_id"] in seen:
                continue
            seen.add(item["source_id"])
            item["success"] = bool(item["success"])
            result.append(item)
        return result

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        item = dict(row)
        for key, fallback in (
            ("categories_json", []),
            ("tags_json", []),
            ("authors_json", []),
            ("component_scores_json", {}),
            ("reasons_json", []),
            ("metadata_json", {}),
        ):
            clean_key = key.replace("_json", "")
            item[clean_key] = _loads(item.pop(key), fallback)
        item["is_important"] = bool(item["is_important"])
        return item

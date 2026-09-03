from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .config import LLMSettings
from .db import Database
from .eligibility import LLM_SCREENING_RULE_VERSION
from .models import RadarItem
from .processing.normalize import unique_preserving_order


# DeepSeek V4-Pro prices effective 2026-08-16, per one million tokens.
# This is a project-side estimate; DeepSeek's invoice remains authoritative.
# Source: https://api-docs.deepseek.com/quick_start/pricing/
DEEPSEEK_V4_PRO_PRICES: Mapping[str, Mapping[str, float]] = {
    "off_peak": {"cache_hit": 0.022, "cache_miss": 0.66, "output": 1.98},
    "peak": {"cache_hit": 0.044, "cache_miss": 1.32, "output": 3.96},
}

NEWS_CATEGORIES = {
    "model-release",
    "product-tool-release",
    "open-source-tool",
    "dataset-benchmark",
    "research-result",
    "hardware-robotics",
    "community-trending",
    "not-relevant",
}
PAPER_CATEGORIES = {
    "vla-policy",
    "mllm-reasoning",
    "perception-understanding",
    "world-model",
    "planning",
    "benchmark-dataset",
    "other",
}


class LLMConfigurationError(RuntimeError):
    pass


class LLMBudgetExceeded(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    pass


class LLMUsageUnavailable(RuntimeError):
    pass


class LLMResponseError(RuntimeError):
    pass


class LLMOutputTruncated(LLMResponseError):
    """The provider stopped before the final structured response was complete."""


def _bounded_number(value: Any, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LLMResponseError(f"expected a number, got {type(value).__name__}")
    number = float(value)
    if number < minimum or number > maximum:
        raise LLMResponseError(
            f"number {number} is outside the allowed range {minimum}..{maximum}"
        )
    return number


def _required_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise LLMResponseError(f"{field} must be a JSON boolean")
    return value


def _clean_string(value: Any, maximum: int = 1000) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:maximum]


def _clean_string_list(value: Any, maximum_items: int, maximum_length: int) -> List[str]:
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for entry in value:
        cleaned = _clean_string(entry, maximum_length)
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= maximum_items:
            break
    return result


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _community_signals(item: RadarItem) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for raw in item.metadata.get("community_signals", []):
        if not isinstance(raw, dict):
            continue
        platform = _clean_string(raw.get("platform"), 80)
        if not platform:
            continue
        signal = {
            "platform": platform,
            "signal_type": _clean_string(raw.get("signal_type"), 80),
            "rank": _nonnegative_int(raw.get("rank")),
            "points": _nonnegative_int(raw.get("points")),
            "comments": _nonnegative_int(raw.get("comments")),
            "views": _nonnegative_int(raw.get("views")),
            "likes": _nonnegative_int(raw.get("likes")),
            "favorites": _nonnegative_int(raw.get("favorites")),
            "qualified": raw.get("qualified") is True,
            "discussion_url": _clean_string(raw.get("discussion_url"), 1000),
            "period": _clean_string(raw.get("period"), 80),
        }
        result.append(signal)
        if len(result) >= 6:
            break
    return result


def _has_verifiable_heat_signal(item: RadarItem) -> bool:
    return any(signal["qualified"] for signal in _community_signals(item))


def _pricing_tier(at: datetime) -> str:
    current = at.astimezone(timezone.utc)
    is_weekday = current.weekday() < 5
    is_peak_hour = 1 <= current.hour < 4 or 6 <= current.hour < 10
    return "peak" if is_weekday and is_peak_hour else "off_peak"


def estimate_deepseek_cost(
    *,
    at: datetime,
    cache_hit_tokens: int,
    cache_miss_tokens: int,
    completion_tokens: int,
) -> float:
    rates = DEEPSEEK_V4_PRO_PRICES[_pricing_tier(at)]
    cost = (
        max(0, cache_hit_tokens) * rates["cache_hit"]
        + max(0, cache_miss_tokens) * rates["cache_miss"]
        + max(0, completion_tokens) * rates["output"]
    ) / 1_000_000
    return round(cost, 8)


class DeepSeekScreener:
    """DeepSeek-only semantic selector with strict validation and a hard budget.

    Fetching, time windows, URL/domain checks, and arXiv identity checks remain
    deterministic. This class is the sole authority for topical/importance
    selection after those facts have been verified.
    """

    def __init__(
        self,
        settings: LLMSettings,
        database: Database,
        timezone_name: str,
        opener: Optional[Callable[..., Any]] = None,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.timezone_name = timezone_name
        self._opener = opener or urlopen
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    @property
    def enabled(self) -> bool:
        return bool(self.settings.enabled and self.settings.api_key)

    def ensure_ready(self) -> None:
        errors: List[str] = []
        if not self.settings.enabled:
            errors.append("llm.enabled must remain true")
        if not self.settings.api_key:
            errors.append("GitHub Secret DEEPSEEK_API_KEY is missing")
        if self.settings.provider != "deepseek":
            errors.append("provider must be deepseek")
        if self.settings.model != "deepseek-v4-pro":
            errors.append("model must be deepseek-v4-pro")
        if not self.settings.thinking_enabled:
            errors.append("Thinking mode must be enabled")
        if self.settings.reasoning_effort != "max":
            errors.append("reasoning_effort must be max")
        if urlsplit(self.settings.base_url).hostname != "api.deepseek.com":
            errors.append("base_url must point to api.deepseek.com")
        if self.settings.daily_token_limit <= 0:
            errors.append("daily_token_limit must be positive")
        if self.settings.daily_cost_limit_usd <= 0:
            errors.append("daily_cost_limit_usd must be positive")
        if self.settings.paper_triage_batch_size <= 0:
            errors.append("paper_triage_batch_size must be positive")
        if self.settings.paper_triage_abstract_chars <= 0:
            errors.append("paper_triage_abstract_chars must be positive")
        if self.settings.paper_triage_max_output_tokens < 512:
            errors.append("paper_triage_max_output_tokens must be at least 512")
        for label, path in (
            ("system", self.settings.system_prompt_path),
            ("news", self.settings.news_prompt_path),
            ("paper-triage", self.settings.paper_triage_prompt_path),
            ("paper", self.settings.paper_prompt_path),
        ):
            if not path.is_file():
                errors.append(f"{label} prompt file not found: {path}")
        if errors:
            raise LLMConfigurationError("; ".join(errors))
        self.prompt_manifest()

    def prompt_manifest(self) -> Dict[str, Any]:
        if not self.settings.system_prompt_path.is_file():
            raise LLMConfigurationError(
                f"system prompt file not found: {self.settings.system_prompt_path}"
            )
        if not self.settings.system_prompt_path.read_text(encoding="utf-8").strip():
            raise LLMConfigurationError("system prompt cannot be empty")
        hashes: Dict[str, str] = {}
        for kind, path in (
            ("news", self.settings.news_prompt_path),
            ("paper-triage", self.settings.paper_triage_prompt_path),
            ("paper", self.settings.paper_prompt_path),
        ):
            if not path.is_file():
                raise LLMConfigurationError(f"{kind} prompt file not found: {path}")
            content = path.read_text(encoding="utf-8")
            missing = [
                placeholder
                for placeholder in ("{{schema_json}}", "{{candidates_json}}")
                if placeholder not in content
            ]
            if missing:
                raise LLMConfigurationError(
                    f"{kind} prompt is missing: {', '.join(missing)}"
                )
            hashes[kind] = self._prompt_sha256(kind)
        return {
            "prompt_version": self.settings.prompt_version,
            "system_path": str(self.settings.system_prompt_path),
            "news_path": str(self.settings.news_prompt_path),
            "paper_triage_path": str(self.settings.paper_triage_prompt_path),
            "paper_path": str(self.settings.paper_prompt_path),
            "sha256": hashes,
        }

    def screen(self, items: Iterable[RadarItem], kind: str) -> List[RadarItem]:
        self.ensure_ready()
        if kind not in {"news", "paper"}:
            raise ValueError("kind must be news or paper")
        candidates = list(items)
        if not candidates:
            return candidates
        batch_size = (
            self.settings.news_batch_size
            if kind == "news"
            else self.settings.paper_batch_size
        )
        if batch_size <= 0:
            raise LLMConfigurationError("LLM batch size must be positive")
        for offset in range(0, len(candidates), batch_size):
            batch = candidates[offset : offset + batch_size]
            batch_label = f"{offset // batch_size + 1:03d}"
            decisions = self._screen_batch(batch, kind, batch_label)
            for item, decision in zip(batch, decisions):
                self._apply_decision(item, decision, kind)
        return candidates

    def screen_papers_two_stage(
        self, items: Iterable[RadarItem]
    ) -> List[RadarItem]:
        """Triage every daily paper, then strictly screen plausible candidates.

        The first pass deliberately uses the same best V4-Pro model with
        thinking disabled and a compact abstract excerpt.  It is high recall:
        uncertain items proceed to the full-abstract Thinking-max pass.  A
        budget failure aborts the whole pipeline before any partial result is
        published.
        """

        self.ensure_ready()
        papers = list(items)
        if not papers:
            return papers
        batch_size = self.settings.paper_triage_batch_size
        for offset in range(0, len(papers), batch_size):
            batch = papers[offset : offset + batch_size]
            batch_label = f"{offset // batch_size + 1:03d}"
            decisions = self._triage_paper_batch(batch, batch_label)
            for item, decision in zip(batch, decisions):
                self._apply_triage_decision(item, decision)

        final_candidates = [
            item
            for item in papers
            if bool(item.metadata.get("paper_triage", {}).get("candidate"))
        ]
        self.screen(final_candidates, "paper")
        return papers

    def usage_summary(self, at: Optional[datetime] = None) -> Dict[str, Any]:
        current = at or self._clock()
        local_date = current.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()
        summary = self.database.llm_usage_summary(local_date)
        summary["stages"] = self.database.llm_usage_breakdown(local_date)
        summary.update(
            {
                "provider": "deepseek",
                "model": self.settings.model,
                "thinking": "enabled" if self.settings.thinking_enabled else "disabled",
                "reasoning_effort": self.settings.reasoning_effort,
                "paper_triage_thinking": "disabled",
                "daily_token_limit": self.settings.daily_token_limit,
                "daily_cost_limit_usd": self.settings.daily_cost_limit_usd,
            }
        )
        return summary

    def _screen_batch(
        self, batch: Sequence[RadarItem], kind: str, batch_label: str
    ) -> List[Dict[str, Any]]:
        identifiers = [
            f"{kind[0]}{batch_label}-{index:03d}"
            for index in range(len(batch))
        ]
        base_prompt = self._build_prompt(batch, identifiers, kind)
        prompt = base_prompt
        last_error: Optional[Exception] = None
        for attempt in range(self.settings.max_retries + 1):
            purpose = f"screen-{kind}-batch-{batch_label}-attempt-{attempt + 1}"
            try:
                content = self._invoke(prompt, purpose, len(batch), kind)
                decisions = self._parse_decisions(content, identifiers, kind, batch)
                self._validate_evidence(batch, decisions)
                return decisions
            except LLMBudgetExceeded:
                raise
            except LLMOutputTruncated as exc:
                # Retrying an identical max-effort request usually consumes the
                # same allowance and truncates again. Divide the work instead;
                # all usage from the truncated request has already been recorded.
                if len(batch) == 1:
                    raise LLMOutputTruncated(
                        f"DeepSeek output remained truncated for one {kind} "
                        f"candidate: {exc}"
                    ) from exc
                midpoint = len(batch) // 2
                return self._screen_batch(
                    batch[:midpoint], kind, f"{batch_label}a"
                ) + self._screen_batch(
                    batch[midpoint:], kind, f"{batch_label}b"
                )
            except (LLMRequestError, LLMResponseError) as exc:
                last_error = exc
                prompt = (
                    base_prompt
                    + "\n\n重试修正要求：上一次响应未通过程序校验。"
                    + f"错误为：{exc}。请重新检查全部字段、候选 ID 和证据摘录，"
                    + "仅返回完整 JSON。"
                )
        raise LLMResponseError(
            f"DeepSeek could not produce a valid {kind} decision after "
            f"{self.settings.max_retries + 1} attempt(s): {last_error}"
        )

    def _triage_paper_batch(
        self, batch: Sequence[RadarItem], batch_label: str
    ) -> List[Dict[str, Any]]:
        identifiers = [
            f"t{batch_label}-{index:03d}" for index in range(len(batch))
        ]
        base_prompt = self._build_paper_triage_prompt(batch, identifiers)
        prompt = base_prompt
        last_error: Optional[Exception] = None
        for attempt in range(self.settings.max_retries + 1):
            purpose = (
                f"triage-paper-batch-{batch_label}-attempt-{attempt + 1}"
            )
            try:
                content = self._invoke(
                    prompt,
                    purpose,
                    len(batch),
                    "paper-triage",
                    thinking_enabled=False,
                    output_cap=self.settings.paper_triage_max_output_tokens,
                )
                return self._parse_triage_decisions(content, identifiers)
            except LLMBudgetExceeded:
                raise
            except LLMOutputTruncated as exc:
                if len(batch) == 1:
                    raise LLMOutputTruncated(
                        "DeepSeek paper triage remained truncated for one "
                        f"candidate: {exc}"
                    ) from exc
                midpoint = len(batch) // 2
                return self._triage_paper_batch(
                    batch[:midpoint], f"{batch_label}a"
                ) + self._triage_paper_batch(
                    batch[midpoint:], f"{batch_label}b"
                )
            except (LLMRequestError, LLMResponseError) as exc:
                last_error = exc
                prompt = (
                    base_prompt
                    + "\n\n重试修正要求：上一次响应未通过程序校验。"
                    + f"错误为：{exc}。请逐项检查 ID、candidate 和 confidence，"
                    + "仅返回完整 JSON。"
                )
        raise LLMResponseError(
            "DeepSeek could not produce valid paper triage after "
            f"{self.settings.max_retries + 1} attempt(s): {last_error}"
        )

    @staticmethod
    def _validate_evidence(
        batch: Sequence[RadarItem], decisions: Sequence[Dict[str, Any]]
    ) -> None:
        for item, decision in zip(batch, decisions):
            if not decision["selected"]:
                continue
            supplied = " ".join(f"{item.title} {item.summary}".casefold().split())
            unsupported = [
                phrase
                for phrase in decision["evidence"]
                if " ".join(phrase.casefold().split()) not in supplied
            ]
            if unsupported:
                raise LLMResponseError(
                    "DeepSeek evidence is not an exact excerpt of the supplied text"
                )

    def _build_prompt(
        self, batch: Sequence[RadarItem], identifiers: Sequence[str], kind: str
    ) -> str:
        candidates: List[Dict[str, Any]] = []
        for identifier, item in zip(identifiers, batch):
            provenance = item.metadata.get("provenance", {})
            entry: Dict[str, Any] = {
                "id": identifier,
                "title": item.title[:1000],
                "abstract_or_feed_summary": item.summary[:8000],
                "published_at": item.published_at.isoformat(),
                "source_name": item.source_name,
                "source_tier": item.source_tier,
                "source_type": item.source_type,
                "source_tags": item.tags[:12],
                "verified_source_status": provenance.get("status", ""),
            }
            if kind == "news":
                entry.update(
                    {
                        "has_verifiable_heat_signal": _has_verifiable_heat_signal(item),
                        "community_signals": _community_signals(item),
                    }
                )
            if kind == "paper":
                entry.update(
                    {
                        "authors": item.authors[:12],
                        "arxiv_categories": item.categories,
                        "arxiv_id": item.external_id,
                    }
                )
            candidates.append(entry)

        schema_example = {
            "items": [
                {
                    "id": identifiers[0],
                    "selected": False,
                    **(
                        {
                            "is_ai": True,
                            "is_major_foundation_model": False,
                            "is_significant_product_tool_or_hardware": False,
                            "is_autonomous_driving_dataset_or_benchmark": False,
                            "is_important_research_result": False,
                            "is_community_trending": False,
                            "has_verifiable_heat_signal": False,
                        }
                        if kind == "news"
                        else {
                            "is_mllm_vla": True,
                            "is_autonomous_driving": True,
                            "is_substantive_application": False,
                        }
                    ),
                    "importance_score": 0,
                    "confidence": 0.0,
                    "category": "not-relevant" if kind == "news" else "other",
                    "summary_zh": "",
                    "why_important": "",
                    "evidence": [],
                    "tags": [],
                    "dimension_scores": (
                        {
                            "semantic_relevance": 0,
                            "novelty": 0,
                            "impact": 0,
                            "community_heat": 0,
                            "evidence_quality": 0,
                        }
                        if kind == "news"
                        else {
                            "mllm_vla_relevance": 0,
                            "driving_relevance": 0,
                            "method_novelty": 0,
                            "evidence_quality": 0,
                            "reproducibility": 0,
                        }
                    ),
                }
            ]
        }
        template = self._prompt_text(kind)
        if "{{schema_json}}" not in template or "{{candidates_json}}" not in template:
            raise LLMConfigurationError(
                f"{kind} prompt must contain {{{{schema_json}}}} and "
                "{{candidates_json}} placeholders"
            )
        return template.replace(
            "{{schema_json}}",
            json.dumps(schema_example, ensure_ascii=False, separators=(",", ":")),
        ).replace(
            "{{candidates_json}}",
            json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
        )

    def _build_paper_triage_prompt(
        self,
        batch: Sequence[RadarItem],
        identifiers: Sequence[str],
    ) -> str:
        candidates = [
            {
                "id": identifier,
                "title": item.title[:1000],
                "abstract_excerpt": item.summary[
                    : self.settings.paper_triage_abstract_chars
                ],
                "abstract_is_truncated": (
                    len(item.summary) > self.settings.paper_triage_abstract_chars
                ),
                "arxiv_categories": item.categories,
                "arxiv_id": item.external_id,
                "published_at": item.published_at.isoformat(),
            }
            for identifier, item in zip(identifiers, batch)
        ]
        schema_example = {
            "items": [
                {
                    "id": identifiers[0],
                    "candidate": True,
                    "confidence": 0.5,
                }
            ]
        }
        template = self._prompt_text("paper-triage")
        if "{{schema_json}}" not in template or "{{candidates_json}}" not in template:
            raise LLMConfigurationError(
                "paper-triage prompt must contain {{schema_json}} and "
                "{{candidates_json}} placeholders"
            )
        return template.replace(
            "{{schema_json}}",
            json.dumps(schema_example, ensure_ascii=False, separators=(",", ":")),
        ).replace(
            "{{candidates_json}}",
            json.dumps(candidates, ensure_ascii=False, separators=(",", ":")),
        )

    def _prompt_text(self, kind: str) -> str:
        paths = {
            "news": self.settings.news_prompt_path,
            "paper-triage": self.settings.paper_triage_prompt_path,
            "paper": self.settings.paper_prompt_path,
        }
        try:
            path = paths[kind]
        except KeyError as exc:
            raise ValueError(f"Unsupported prompt kind: {kind}") from exc
        return path.read_text(encoding="utf-8").strip()

    def _prompt_sha256(self, kind: str) -> str:
        content = (
            self.settings.system_prompt_path.read_text(encoding="utf-8").strip()
            + "\n"
            + self._prompt_text(kind)
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def _budgeted_max_output(
        self,
        messages: Sequence[Dict[str, str]],
        at: datetime,
        output_cap: Optional[int] = None,
    ) -> int:
        local_date = at.astimezone(ZoneInfo(self.timezone_name)).date().isoformat()
        used = self.database.llm_usage_summary(local_date)
        serialized = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
        # bytes/2 intentionally overestimates both English and Chinese prompts.
        estimated_prompt_tokens = max(1, len(serialized.encode("utf-8")) // 2 + 512)
        remaining_tokens = self.settings.daily_token_limit - int(used["total_tokens"])
        token_allowance = remaining_tokens - estimated_prompt_tokens

        rates = DEEPSEEK_V4_PRO_PRICES[_pricing_tier(at)]
        estimated_input_cost = estimated_prompt_tokens * rates["cache_miss"] / 1_000_000
        remaining_cost = self.settings.daily_cost_limit_usd - float(
            used["estimated_cost_usd"]
        )
        cost_allowance = int(
            max(0.0, remaining_cost - estimated_input_cost)
            * 1_000_000
            / rates["output"]
        )
        max_output = min(
            self.settings.max_output_tokens,
            output_cap if output_cap is not None else self.settings.max_output_tokens,
            token_allowance,
            cost_allowance,
        )
        if max_output < 512:
            raise LLMBudgetExceeded(
                "DeepSeek daily budget reached before this batch: "
                f"used={used['total_tokens']}/{self.settings.daily_token_limit} tokens, "
                f"estimated_cost=${used['estimated_cost_usd']:.6f}/"
                f"${self.settings.daily_cost_limit_usd:.2f}"
            )
        return int(max_output)

    def _invoke(
        self,
        prompt: str,
        purpose: str,
        item_count: int,
        kind: str,
        *,
        thinking_enabled: bool = True,
        output_cap: Optional[int] = None,
    ) -> str:
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        messages = [
            {
                "role": "system",
                "content": self.settings.system_prompt_path.read_text(
                    encoding="utf-8"
                ).strip(),
            },
            {"role": "user", "content": prompt},
        ]
        max_output = self._budgeted_max_output(
            messages, now, output_cap=output_cap
        )
        body = {
            "model": self.settings.model,
            "messages": messages,
            "thinking": {
                "type": "enabled" if thinking_enabled else "disabled"
            },
            "response_format": {"type": "json_object"},
            "max_tokens": max_output,
            "stream": False,
        }
        if thinking_enabled:
            body["reasoning_effort"] = "max"
        endpoint = self.settings.base_url.rstrip("/") + "/chat/completions"
        request = Request(
            endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.settings.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "DailyAIRadar/0.8.1",
            },
        )
        try:
            with self._opener(request, timeout=self.settings.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            self._record_failed_usage(
                now, purpose, item_count, f"HTTP {exc.code}: {exc.reason}"
            )
            raise LLMRequestError(f"DeepSeek API returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            self._record_failed_usage(now, purpose, item_count, type(exc).__name__)
            raise LLMRequestError(
                f"DeepSeek API request failed: {type(exc).__name__}"
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._record_failed_usage(now, purpose, item_count, "invalid response JSON")
            raise LLMRequestError("DeepSeek API returned invalid response JSON") from exc

        if not isinstance(payload, dict):
            self._record_failed_usage(now, purpose, item_count, "response is not an object")
            raise LLMResponseError("DeepSeek response must be a JSON object")
        usage = payload.get("usage")
        if not isinstance(usage, dict) or int(usage.get("total_tokens", 0) or 0) <= 0:
            self._record_failed_usage(now, purpose, item_count, "token usage missing")
            raise LLMUsageUnavailable(
                "DeepSeek response omitted token usage; stopped to avoid untracked spending"
            )
        prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        cache_hit_tokens = int(usage.get("prompt_cache_hit_tokens", 0) or 0)
        cache_miss_tokens = int(
            usage.get(
                "prompt_cache_miss_tokens", max(0, prompt_tokens - cache_hit_tokens)
            )
            or 0
        )
        details = usage.get("completion_tokens_details", {})
        reasoning_tokens = (
            int(details.get("reasoning_tokens", 0) or 0)
            if isinstance(details, dict)
            else 0
        )
        total_tokens = int(
            usage.get("total_tokens", prompt_tokens + completion_tokens) or 0
        )
        try:
            recorded_finish_reason = payload["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError):
            recorded_finish_reason = None
        cost = estimate_deepseek_cost(
            at=now,
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
            completion_tokens=completion_tokens,
        )
        self.database.record_llm_usage(
            {
                "occurred_at": now.astimezone(timezone.utc).isoformat(),
                "local_date": now.astimezone(ZoneInfo(self.timezone_name)).date().isoformat(),
                "provider": "deepseek",
                "model": str(payload.get("model", self.settings.model)),
                "purpose": purpose,
                "request_items": item_count,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "cache_hit_tokens": cache_hit_tokens,
                "cache_miss_tokens": cache_miss_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_usd": cost,
                "pricing_tier": _pricing_tier(now),
                "response_id": str(payload.get("id", "")),
                "prompt_version": self.settings.prompt_version,
                "prompt_sha256": self._prompt_sha256(kind),
                "status": (
                    "success" if recorded_finish_reason == "stop" else "failed"
                ),
                "error": (
                    ""
                    if recorded_finish_reason == "stop"
                    else f"finish_reason={recorded_finish_reason or 'missing'}"
                ),
            }
        )

        try:
            choice = payload["choices"][0]
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise LLMOutputTruncated(
                    "finish_reason=length "
                    f"at max_tokens={max_output}; output or context limit reached"
                )
            if finish_reason != "stop":
                raise LLMResponseError(
                    f"unexpected finish_reason={finish_reason}"
                )
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMResponseError(
                "DeepSeek response is missing message content"
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMResponseError("DeepSeek response content is empty")
        return content

    def _record_failed_usage(
        self, at: datetime, purpose: str, item_count: int, error: str
    ) -> None:
        self.database.record_llm_usage(
            {
                "occurred_at": at.astimezone(timezone.utc).isoformat(),
                "local_date": at.astimezone(ZoneInfo(self.timezone_name)).date().isoformat(),
                "provider": "deepseek",
                "model": self.settings.model,
                "purpose": purpose,
                "request_items": item_count,
                "status": "failed",
                "error": error,
            }
        )

    def _parse_decisions(
        self,
        content: str,
        identifiers: Sequence[str],
        kind: str,
        batch: Sequence[RadarItem],
    ) -> List[Dict[str, Any]]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                "DeepSeek screening output is not valid JSON"
            ) from exc
        entries = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise LLMResponseError("screening JSON must contain an items array")
        by_id: Dict[str, Dict[str, Any]] = {}
        for raw in entries:
            if not isinstance(raw, dict):
                raise LLMResponseError("every screening item must be an object")
            identifier = raw.get("id")
            if not isinstance(identifier, str) or identifier in by_id:
                raise LLMResponseError("screening item id is missing or duplicated")
            by_id[identifier] = raw
        if set(by_id) != set(identifiers):
            raise LLMResponseError("DeepSeek changed, omitted, or added candidate ids")
        return [
            self._normalize_decision(
                by_id[identifier],
                kind,
                verified_heat_signal=(
                    _has_verifiable_heat_signal(item) if kind == "news" else False
                ),
                community_source=(
                    kind == "news" and item.source_type == "community"
                ),
            )
            for identifier, item in zip(identifiers, batch)
        ]

    @staticmethod
    def _parse_triage_decisions(
        content: str, identifiers: Sequence[str]
    ) -> List[Dict[str, Any]]:
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMResponseError(
                "DeepSeek paper triage output is not valid JSON"
            ) from exc
        entries = payload.get("items") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise LLMResponseError("paper triage JSON must contain an items array")
        by_id: Dict[str, Dict[str, Any]] = {}
        for raw in entries:
            if not isinstance(raw, dict):
                raise LLMResponseError("every paper triage item must be an object")
            identifier = raw.get("id")
            if not isinstance(identifier, str) or identifier in by_id:
                raise LLMResponseError("paper triage item id is missing or duplicated")
            if set(raw) != {"id", "candidate", "confidence"}:
                raise LLMResponseError(
                    "paper triage items may only contain id, candidate, confidence"
                )
            by_id[identifier] = raw
        if set(by_id) != set(identifiers):
            raise LLMResponseError(
                "DeepSeek changed, omitted, or added paper triage ids"
            )
        return [
            {
                "candidate": _required_bool(
                    by_id[identifier].get("candidate"), "candidate"
                ),
                "confidence": round(
                    _bounded_number(
                        by_id[identifier].get("confidence"), 0, 1
                    ),
                    4,
                ),
            }
            for identifier in identifiers
        ]

    @staticmethod
    def _normalize_decision(
        raw: Dict[str, Any],
        kind: str,
        *,
        verified_heat_signal: bool = False,
        community_source: bool = False,
    ) -> Dict[str, Any]:
        selected = _required_bool(raw.get("selected"), "selected")
        score = _bounded_number(raw.get("importance_score"), 0, 100)
        confidence = _bounded_number(raw.get("confidence"), 0, 1)
        category = _clean_string(raw.get("category"), 80)
        allowed_categories = NEWS_CATEGORIES if kind == "news" else PAPER_CATEGORIES
        if category not in allowed_categories:
            raise LLMResponseError(f"unsupported {kind} category: {category}")

        if kind == "news":
            flags = {
                "is_ai": _required_bool(raw.get("is_ai"), "is_ai"),
                "is_major_foundation_model": _required_bool(
                    raw.get("is_major_foundation_model"),
                    "is_major_foundation_model",
                ),
                "is_significant_product_tool_or_hardware": _required_bool(
                    raw.get("is_significant_product_tool_or_hardware"),
                    "is_significant_product_tool_or_hardware",
                ),
                "is_autonomous_driving_dataset_or_benchmark": _required_bool(
                    raw.get("is_autonomous_driving_dataset_or_benchmark"),
                    "is_autonomous_driving_dataset_or_benchmark",
                ),
                "is_important_research_result": _required_bool(
                    raw.get("is_important_research_result"),
                    "is_important_research_result",
                ),
                "is_community_trending": _required_bool(
                    raw.get("is_community_trending"),
                    "is_community_trending",
                ),
                # The model must acknowledge the supplied metric, but it cannot
                # create one: collector metadata remains the final authority.
                "has_verifiable_heat_signal": _required_bool(
                    raw.get("has_verifiable_heat_signal"),
                    "has_verifiable_heat_signal",
                )
                and verified_heat_signal,
            }
            route_gate = {
                "model-release": flags["is_major_foundation_model"],
                "product-tool-release": flags[
                    "is_significant_product_tool_or_hardware"
                ],
                "open-source-tool": flags[
                    "is_significant_product_tool_or_hardware"
                ],
                "hardware-robotics": flags[
                    "is_significant_product_tool_or_hardware"
                ],
                "dataset-benchmark": flags[
                    "is_autonomous_driving_dataset_or_benchmark"
                ],
                "research-result": flags["is_important_research_result"]
                and flags["has_verifiable_heat_signal"],
                "community-trending": flags["is_community_trending"]
                and flags["has_verifiable_heat_signal"],
                "not-relevant": False,
            }[category]
            selected = selected and flags["is_ai"] and route_gate
            # A community post proves that a discussion exists, not that the
            # release/result claimed in the post is true. When an official or
            # publisher item is deduplicated with a community item, that item
            # keeps the stronger source and may still use the merged heat data.
            if community_source:
                selected = selected and category == "community-trending"
            dimension_names = (
                "semantic_relevance",
                "novelty",
                "impact",
                "community_heat",
                "evidence_quality",
            )
        else:
            flags = {
                "is_mllm_vla": _required_bool(
                    raw.get("is_mllm_vla"), "is_mllm_vla"
                ),
                "is_autonomous_driving": _required_bool(
                    raw.get("is_autonomous_driving"), "is_autonomous_driving"
                ),
                "is_substantive_application": _required_bool(
                    raw.get("is_substantive_application"),
                    "is_substantive_application",
                ),
            }
            selected = selected and all(flags.values())
            dimension_names = (
                "mllm_vla_relevance",
                "driving_relevance",
                "method_novelty",
                "evidence_quality",
                "reproducibility",
            )

        dimensions_raw = raw.get("dimension_scores")
        if not isinstance(dimensions_raw, dict):
            raise LLMResponseError("dimension_scores must be an object")
        dimensions = {
            name: round(_bounded_number(dimensions_raw.get(name), 0, 100), 2)
            for name in dimension_names
        }
        summary_zh = _clean_string(raw.get("summary_zh"), 800)
        why_important = _clean_string(raw.get("why_important"), 500)
        evidence = _clean_string_list(raw.get("evidence"), 4, 160)
        if selected and (not summary_zh or not why_important):
            raise LLMResponseError(
                "selected items need summary_zh and why_important"
            )
        if selected and not evidence:
            raise LLMResponseError("selected items need evidence from the supplied text")
        if any(len(phrase.split()) > 20 for phrase in evidence):
            raise LLMResponseError("evidence excerpts must not exceed 20 words")
        return {
            "selected": selected,
            "score": round(score, 2),
            "confidence": round(confidence, 4),
            "category": category,
            "summary_zh": summary_zh,
            "why_important": why_important,
            "evidence": evidence,
            "tags": _clean_string_list(raw.get("tags"), 6, 60),
            "dimensions": dimensions,
            "flags": flags,
        }

    def _apply_decision(
        self, item: RadarItem, decision: Dict[str, Any], kind: str
    ) -> None:
        assessed_at = self._clock()
        if assessed_at.tzinfo is None:
            assessed_at = assessed_at.replace(tzinfo=timezone.utc)
        item.score = float(decision["score"])
        item.component_scores = dict(decision["dimensions"])
        item.category = str(decision["category"])
        item.tags = unique_preserving_order(item.tags + list(decision["tags"]))
        item.is_important = False
        item.metadata["summary_zh"] = decision["summary_zh"]
        item.metadata["why_important"] = decision["why_important"]
        item.metadata["llm_screening"] = {
            "rule_version": LLM_SCREENING_RULE_VERSION,
            "provider": "deepseek",
            "model": self.settings.model,
            "thinking": "enabled",
            "reasoning_effort": "max",
            "prompt_version": self.settings.prompt_version,
            "prompt_sha256": self._prompt_sha256(kind),
            "selected": bool(decision["selected"]),
            "importance_score": item.score,
            "confidence": decision["confidence"],
            "category": item.category,
            "flags": decision["flags"],
            "evidence": decision["evidence"],
            "assessed_at": assessed_at.astimezone(timezone.utc).isoformat(),
        }
        reasons: List[str] = []
        if decision["selected"]:
            reasons.append(
                "DeepSeek V4-Pro（Thinking max）语义入选："
                + str(decision["why_important"])
            )
            if decision["evidence"]:
                reasons.append(
                    "给定文本依据：" + "；".join(decision["evidence"][:2])
                )
        else:
            reasons.append("DeepSeek V4-Pro（Thinking max）未入选")
        provenance = item.metadata.get("provenance", {})
        status = provenance.get("status")
        if status == "verified-primary":
            reasons.append("来源验证：链接可访问且匹配一手来源域名")
        elif status == "verified-publisher":
            reasons.append("来源验证：链接可访问且匹配发布媒体域名")
        elif status == "verified-community":
            reasons.append("来源验证：社区原帖可访问；互动量仅作热度信号")
        elif status == "verified-link":
            reasons.append("来源验证：社区发现链接可访问")
        elif status == "verified-arxiv-api":
            reasons.append("来源验证：arXiv API 身份与官方摘要 URL 一致")
        elif status == "access-restricted":
            reasons.append("来源验证：域名匹配，但站点限制自动访问")
        item.reasons = reasons

    def _apply_triage_decision(
        self, item: RadarItem, decision: Dict[str, Any]
    ) -> None:
        assessed_at = self._clock()
        if assessed_at.tzinfo is None:
            assessed_at = assessed_at.replace(tzinfo=timezone.utc)
        candidate = bool(decision["candidate"])
        triage = {
            "provider": "deepseek",
            "model": self.settings.model,
            "thinking": "disabled",
            "prompt_version": self.settings.prompt_version,
            "prompt_sha256": self._prompt_sha256("paper-triage"),
            "candidate": candidate,
            "confidence": decision["confidence"],
            "assessed_at": assessed_at.astimezone(timezone.utc).isoformat(),
        }
        item.metadata["paper_triage"] = triage
        if candidate:
            return

        item.score = 0.0
        item.category = "other"
        item.component_scores = {}
        item.is_important = False
        item.metadata["summary_zh"] = ""
        item.metadata["why_important"] = ""
        item.metadata["llm_screening"] = {
            "rule_version": LLM_SCREENING_RULE_VERSION,
            "provider": "deepseek",
            "model": self.settings.model,
            "stage": "paper-triage",
            "thinking": "disabled",
            "reasoning_effort": "none",
            "prompt_version": self.settings.prompt_version,
            "prompt_sha256": self._prompt_sha256("paper-triage"),
            "selected": False,
            "importance_score": 0.0,
            "confidence": decision["confidence"],
            "category": "other",
            "flags": {},
            "evidence": [],
            "assessed_at": assessed_at.astimezone(timezone.utc).isoformat(),
        }
        item.reasons = ["DeepSeek V4-Pro 非思考高召回初筛未进入严格复筛"]

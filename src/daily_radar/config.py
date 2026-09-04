from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


@dataclass(frozen=True)
class SourceConfig:
    id: str
    name: str
    url: str
    tier: int = 2
    type: str = "rss"
    focus: float = 0.6
    enabled: bool = True
    tags: List[str] = field(default_factory=list)
    allowed_domains: List[str] = field(default_factory=list)
    allow_external_links: bool = False
    adapter: str = "rss"
    max_items: int = 30
    community_platform: str = ""
    community_rank_limit: int = 0
    community_min_points: int = 0
    community_min_comments: int = 0


@dataclass(frozen=True)
class NewsSettings:
    lookback_hours: int = 48
    max_important: int = 15
    cluster_similarity: float = 0.72


@dataclass(frozen=True)
class PaperSettings:
    lookback_hours: int = 96
    # arXiv's max_results is a page size, not a safe total-result limit.  The
    # collector keeps paging until every result in the announcement query is read.
    page_size: int = 200
    page_delay_seconds: float = 3.0
    max_important: int = 20
    categories: List[str] = field(
        default_factory=lambda: [
            "cs.CV",
            "cs.RO",
            "cs.AI",
            "cs.LG",
            "cs.CL",
            "cs.SY",
            "eess.SY",
            "eess.IV",
            "stat.ML",
        ]
    )


@dataclass(frozen=True)
class NetworkSettings:
    timeout_seconds: int = 25
    user_agent: str = "DailyAIRadar/0.8.2 (+https://github.com/your-name/daily-ai-radar)"
    retries: int = 2
    retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool = True
    provider: str = "deepseek"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    model: str = "deepseek-v4-pro"
    thinking_enabled: bool = True
    reasoning_effort: str = "max"
    timeout_seconds: int = 600
    max_output_tokens: int = 32768
    news_batch_size: int = 8
    paper_batch_size: int = 6
    paper_triage_batch_size: int = 80
    paper_triage_abstract_chars: int = 480
    paper_triage_max_output_tokens: int = 8192
    max_retries: int = 1
    daily_token_limit: int = 500_000
    daily_cost_limit_usd: float = 1.0
    prompt_version: str = "2026-08-31-v3"
    system_prompt_path: Path = PROJECT_ROOT / "prompts" / "system.md"
    news_prompt_path: Path = PROJECT_ROOT / "prompts" / "news_screening.md"
    paper_triage_prompt_path: Path = PROJECT_ROOT / "prompts" / "paper_triage.md"
    paper_prompt_path: Path = PROJECT_ROOT / "prompts" / "paper_screening.md"


@dataclass(frozen=True)
class EmailSettings:
    smtp_host: str = "smtp.163.com"
    smtp_port: int = 465
    username: str = ""
    auth_code: str = ""
    recipient: str = ""
    from_name: str = "Daily AI Radar"


@dataclass(frozen=True)
class Settings:
    database_path: Path
    output_dir: Path
    timezone: str
    sources_path: Path
    news: NewsSettings
    papers: PaperSettings
    network: NetworkSettings
    llm: LLMSettings
    email: EmailSettings


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a mapping in {path}")
    return payload


def load_settings(path: str = "") -> Settings:
    config_path = _resolve_project_path(
        path or os.getenv("DAILY_RADAR_CONFIG", str(DEFAULT_CONFIG_PATH))
    )
    raw = _load_yaml(config_path)
    app = raw.get("app", {})
    news_raw = raw.get("news", {})
    paper_raw = raw.get("papers", {})
    network_raw = raw.get("network", {})
    llm_raw = raw.get("llm", {})
    email_raw = raw.get("email", {})

    llm_enabled_env = os.getenv("DAILY_RADAR_LLM_ENABLED")
    llm_enabled = bool(llm_raw.get("enabled", False))
    if llm_enabled_env is not None:
        llm_enabled = llm_enabled_env.lower() in {"1", "true", "yes", "on"}

    database_value = os.getenv(
        "DAILY_RADAR_DB", str(app.get("database_path", "data/daily_radar.db"))
    )
    network_values = dict(network_raw)
    user_agent_env = os.getenv("DAILY_RADAR_USER_AGENT")
    if user_agent_env:
        network_values["user_agent"] = user_agent_env
    return Settings(
        database_path=_resolve_project_path(database_value),
        output_dir=_resolve_project_path(str(app.get("output_dir", "outputs"))),
        timezone=str(app.get("timezone", "Asia/Shanghai")),
        sources_path=_resolve_project_path(
            str(app.get("sources_path", "config/sources.yaml"))
        ),
        news=NewsSettings(**news_raw),
        papers=PaperSettings(**paper_raw),
        network=NetworkSettings(**network_values),
        llm=LLMSettings(
            enabled=llm_enabled,
            provider=str(llm_raw.get("provider", "deepseek")),
            base_url=os.getenv(
                "DAILY_RADAR_LLM_BASE_URL",
                str(llm_raw.get("base_url", "https://api.deepseek.com")),
            ),
            api_key=os.getenv(
                "DEEPSEEK_API_KEY",
                os.getenv(
                    "DAILY_RADAR_LLM_API_KEY", str(llm_raw.get("api_key", ""))
                ),
            ),
            model=os.getenv(
                "DAILY_RADAR_LLM_MODEL",
                str(llm_raw.get("model", "deepseek-v4-pro")),
            ),
            thinking_enabled=str(
                os.getenv(
                    "DAILY_RADAR_LLM_THINKING",
                    str(llm_raw.get("thinking_enabled", True)),
                )
            ).lower()
            in {"1", "true", "yes", "on"},
            reasoning_effort=os.getenv(
                "DAILY_RADAR_LLM_REASONING_EFFORT",
                str(llm_raw.get("reasoning_effort", "max")),
            ),
            timeout_seconds=int(
                os.getenv(
                    "DAILY_RADAR_LLM_TIMEOUT_SECONDS",
                    str(llm_raw.get("timeout_seconds", 600)),
                )
            ),
            max_output_tokens=int(
                os.getenv(
                    "DAILY_RADAR_LLM_MAX_OUTPUT_TOKENS",
                    str(llm_raw.get("max_output_tokens", 32768)),
                )
            ),
            news_batch_size=int(llm_raw.get("news_batch_size", 8)),
            paper_batch_size=int(llm_raw.get("paper_batch_size", 6)),
            paper_triage_batch_size=int(
                llm_raw.get("paper_triage_batch_size", 80)
            ),
            paper_triage_abstract_chars=int(
                llm_raw.get("paper_triage_abstract_chars", 480)
            ),
            paper_triage_max_output_tokens=int(
                llm_raw.get("paper_triage_max_output_tokens", 8192)
            ),
            max_retries=int(llm_raw.get("max_retries", 1)),
            daily_token_limit=int(
                os.getenv(
                    "DAILY_RADAR_LLM_DAILY_TOKEN_LIMIT",
                    str(llm_raw.get("daily_token_limit", 500_000)),
                )
            ),
            daily_cost_limit_usd=float(
                os.getenv(
                    "DAILY_RADAR_LLM_DAILY_COST_LIMIT_USD",
                    str(llm_raw.get("daily_cost_limit_usd", 1.0)),
                )
            ),
            prompt_version=str(llm_raw.get("prompt_version", "2026-08-31-v3")),
            system_prompt_path=_resolve_project_path(
                str(llm_raw.get("system_prompt_path", "prompts/system.md"))
            ),
            news_prompt_path=_resolve_project_path(
                str(llm_raw.get("news_prompt_path", "prompts/news_screening.md"))
            ),
            paper_triage_prompt_path=_resolve_project_path(
                str(
                    llm_raw.get(
                        "paper_triage_prompt_path", "prompts/paper_triage.md"
                    )
                )
            ),
            paper_prompt_path=_resolve_project_path(
                str(llm_raw.get("paper_prompt_path", "prompts/paper_screening.md"))
            ),
        ),
        email=EmailSettings(
            smtp_host=str(email_raw.get("smtp_host", "smtp.163.com")),
            smtp_port=int(email_raw.get("smtp_port", 465)),
            username=os.getenv("DAILY_RADAR_EMAIL_USERNAME", ""),
            auth_code=os.getenv("DAILY_RADAR_EMAIL_AUTH_CODE", ""),
            recipient=os.getenv(
                "DAILY_RADAR_EMAIL_TO",
                os.getenv("DAILY_RADAR_EMAIL_USERNAME", ""),
            ),
            from_name=str(email_raw.get("from_name", "Daily AI Radar")),
        ),
    )


def load_sources(path: Path) -> List[SourceConfig]:
    raw = _load_yaml(path)
    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("sources must be a list")
    result: List[SourceConfig] = []
    seen = set()
    for entry in sources:
        source = SourceConfig(**entry)
        if source.id in seen:
            raise ValueError(f"Duplicate source id: {source.id}")
        seen.add(source.id)
        if source.enabled:
            result.append(source)
    return result

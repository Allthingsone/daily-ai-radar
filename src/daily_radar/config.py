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


@dataclass(frozen=True)
class NewsSettings:
    lookback_hours: int = 48
    min_relevance: float = 8.0
    min_score: float = 45.0
    max_important: int = 15
    cluster_similarity: float = 0.72
    personal_keywords: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PaperSettings:
    lookback_hours: int = 96
    max_results: int = 150
    min_score: float = 42.0
    max_important: int = 20
    categories: List[str] = field(
        default_factory=lambda: ["cs.CV", "cs.RO", "cs.AI", "cs.LG", "cs.CL"]
    )
    personal_keywords: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class NetworkSettings:
    timeout_seconds: int = 25
    user_agent: str = "DailyAIRadar/0.4.0 (+https://github.com/your-name/daily-ai-radar)"
    retries: int = 2
    retry_backoff_seconds: float = 1.0


@dataclass(frozen=True)
class LLMSettings:
    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 45


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
            base_url=os.getenv(
                "DAILY_RADAR_LLM_BASE_URL",
                str(llm_raw.get("base_url", "https://api.openai.com/v1")),
            ),
            api_key=os.getenv(
                "DAILY_RADAR_LLM_API_KEY", str(llm_raw.get("api_key", ""))
            ),
            model=os.getenv(
                "DAILY_RADAR_LLM_MODEL", str(llm_raw.get("model", ""))
            ),
            timeout_seconds=int(llm_raw.get("timeout_seconds", 45)),
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

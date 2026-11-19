"""Network collectors."""

from .arxiv import ArxivCollector
from .community import CSDNHotCollector, HackerNewsCollector, JuejinHotCollector
from .chinese_media import WordPressCollector
from .rss import RSSCollector
from .zhihu import ZhihuSearchCollector

__all__ = [
    "ArxivCollector",
    "CSDNHotCollector",
    "HackerNewsCollector",
    "JuejinHotCollector",
    "RSSCollector",
    "WordPressCollector",
    "ZhihuSearchCollector",
]

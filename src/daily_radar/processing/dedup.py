from __future__ import annotations

from datetime import timedelta
from typing import Dict, List

from ..models import RadarItem
from .normalize import fingerprint_title, title_similarity, unique_preserving_order


def _preferred(left: RadarItem, right: RadarItem) -> RadarItem:
    left_rank = (left.source_tier, -len(left.summary), left.published_at)
    right_rank = (right.source_tier, -len(right.summary), right.published_at)
    return left if left_rank <= right_rank else right


def _merge_community_signals(target: RadarItem, *items: RadarItem) -> None:
    signals = list(target.metadata.get("community_signals", []))
    for item in items:
        signals.extend(item.metadata.get("community_signals", []))
    unique = []
    seen = set()
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        key = (
            str(signal.get("platform", "")),
            str(signal.get("discussion_url", "")),
            str(signal.get("period", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(dict(signal))
    if unique:
        target.metadata["community_signals"] = unique


def deduplicate_exact(items: List[RadarItem]) -> List[RadarItem]:
    by_key: Dict[str, RadarItem] = {}
    for item in items:
        key = item.canonical_url or f"title:{fingerprint_title(item.title)}"
        current = by_key.get(key)
        if current is None:
            by_key[key] = item
            continue
        winner = _preferred(current, item)
        loser = item if winner is current else current
        _merge_community_signals(winner, loser)
        alternates = list(winner.metadata.get("alternate_sources", []))
        alternates.append(
            {
                "source_id": loser.source_id,
                "name": loser.source_name,
                "url": loser.url,
                "tier": loser.source_tier,
            }
        )
        winner.metadata["alternate_sources"] = alternates
        by_key[key] = winner
    return list(by_key.values())


def cluster_news(items: List[RadarItem], threshold: float = 0.72) -> List[RadarItem]:
    clusters: List[List[RadarItem]] = []
    for item in sorted(items, key=lambda value: value.published_at, reverse=True):
        placed = False
        for cluster in clusters:
            representative = cluster[0]
            if abs(item.published_at - representative.published_at) > timedelta(days=3):
                continue
            if title_similarity(item.title, representative.title) >= threshold:
                cluster.append(item)
                if _preferred(representative, item) is item:
                    # Keep every cluster member when the new item becomes the
                    # representative; a direct overwrite would lose the old one.
                    cluster[0], cluster[-1] = item, representative
                placed = True
                break
        if not placed:
            clusters.append([item])

    merged: List[RadarItem] = []
    for cluster in clusters:
        representative = cluster[0]
        _merge_community_signals(representative, *cluster[1:])
        alternates = list(representative.metadata.get("alternate_sources", []))
        for member in cluster:
            if member is representative:
                continue
            alternates.append(
                {
                    "source_id": member.source_id,
                    "name": member.source_name,
                    "url": member.url,
                    "tier": member.source_tier,
                }
            )
            alternates.extend(member.metadata.get("alternate_sources", []))
        unique_alternates = []
        seen_urls = {representative.url}
        for alternate in alternates:
            url = alternate.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_alternates.append(alternate)
        representative.metadata["alternate_sources"] = unique_alternates
        representative.metadata["source_count"] = 1 + len(unique_alternates)
        representative.cluster_key = fingerprint_title(representative.title)
        representative.tags = unique_preserving_order(
            tag for member in cluster for tag in member.tags
        )
        merged.append(representative)
    return merged

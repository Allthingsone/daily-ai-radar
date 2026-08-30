from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import Database
from .processing.scoring import news_category_label, paper_category_label


def _category_label(item: Dict[str, Any]) -> str:
    if item["kind"] == "news":
        return news_category_label(item["category"])
    return paper_category_label(item["category"])


def export_json(
    items: List[Dict[str, Any]],
    path: Path,
    scope: Optional[Dict[str, Optional[datetime]]] = None,
) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "items": items,
    }
    if scope is not None:
        payload["published_since"] = {
            kind: cutoff.isoformat() if cutoff is not None else None
            for kind, cutoff in scope.items()
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_markdown(
    items: List[Dict[str, Any]],
    path: Path,
    generated_at: Optional[datetime] = None,
) -> None:
    generated_at = generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    lines = [
        "# Daily AI Radar",
        "",
        f"> Generated at {generated_at.isoformat()}",
        "",
    ]
    for kind, heading in (("news", "AI 新闻"), ("paper", "MLLM/VLA 自动驾驶论文")):
        lines.extend([f"## {heading}", ""])
        selected = [item for item in items if item["kind"] == kind]
        if not selected:
            lines.extend(["暂无内容。", ""])
            continue
        for item in selected:
            important = "⭐ " if item["is_important"] else ""
            lines.extend(
                [
                    f"### {important}[{item['title']}]({item['url']})",
                    "",
                    f"- DeepSeek 重要性：**{item['score']:.1f}**",
                    f"- 分类：{_category_label(item)}",
                    f"- 来源：{item['source_name']}",
                    f"- 时间：{item['published_at']}",
                ]
            )
            if item.get("reasons"):
                lines.append("- 入选理由：" + "；".join(item["reasons"]))
            provenance = item.get("metadata", {}).get("provenance", {})
            if provenance:
                lines.append(
                    "- 来源验证："
                    f"{provenance.get('status', 'unknown')} · "
                    f"{provenance.get('domain', '')} · "
                    f"{provenance.get('method', '')}"
                )
            if item["kind"] == "paper" and item.get("external_id"):
                lines.append(f"- arXiv ID：`{item['external_id']}`")
            summary_zh = item.get("metadata", {}).get("summary_zh", "")
            if summary_zh:
                lines.extend(["", summary_zh])
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def export_rss(
    items: List[Dict[str, Any]],
    path: Path,
    channel_link: str = "http://127.0.0.1:8000/",
    generated_at: Optional[datetime] = None,
) -> None:
    generated_at = generated_at or datetime.now(timezone.utc)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=timezone.utc)
    rss = ET.Element("rss", version="2.0")
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = "Daily AI Radar"
    ET.SubElement(channel, "description").text = "AI news and VLA4AD paper digest"
    ET.SubElement(channel, "link").text = channel_link
    ET.SubElement(channel, "lastBuildDate").text = format_datetime(
        generated_at.astimezone(timezone.utc), usegmt=True
    )
    for item in items:
        entry = ET.SubElement(channel, "item")
        ET.SubElement(entry, "title").text = item["title"]
        ET.SubElement(entry, "link").text = item["url"]
        ET.SubElement(entry, "guid", isPermaLink="true").text = item["canonical_url"]
        try:
            published_at = datetime.fromisoformat(
                str(item["published_at"]).replace("Z", "+00:00")
            )
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            published_text = format_datetime(
                published_at.astimezone(timezone.utc), usegmt=True
            )
        except (TypeError, ValueError):
            published_text = str(item["published_at"])
        ET.SubElement(entry, "pubDate").text = published_text
        description = item.get("metadata", {}).get("summary_zh") or item.get("summary", "")
        ET.SubElement(entry, "description").text = description
        ET.SubElement(entry, "category").text = _category_label(item)
    tree = ET.ElementTree(rss)
    tree.write(str(path), encoding="utf-8", xml_declaration=True)


def export_all(
    database: Database,
    output_dir: Path,
    published_since_by_kind: Optional[Dict[str, Optional[datetime]]] = None,
    prompt_version: str = "",
) -> List[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if published_since_by_kind is None:
        items = database.list_items(
            limit=500,
            verified_only=True,
            eligible_only=True,
            prompt_version=prompt_version,
        )
    else:
        items = []
        for kind in ("news", "paper"):
            items.extend(
                database.list_items(
                    kind=kind,
                    limit=500,
                    verified_only=True,
                    published_since=published_since_by_kind.get(kind),
                    eligible_only=True,
                    prompt_version=prompt_version,
                )
            )
        items.sort(key=lambda item: item["published_at"], reverse=True)
    paths = [
        output_dir / "latest.json",
        output_dir / "daily.md",
        output_dir / "feed.xml",
    ]
    export_json(items, paths[0], published_since_by_kind)
    export_markdown(items, paths[1])
    export_rss(items, paths[2])
    return paths

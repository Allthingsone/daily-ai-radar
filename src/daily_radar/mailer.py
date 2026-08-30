from __future__ import annotations

import html
import smtplib
import ssl
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database
from .time_windows import build_period_window


class EmailConfigurationError(RuntimeError):
    pass


def _validated_address(value: str, label: str) -> str:
    if "\r" in value or "\n" in value:
        raise EmailConfigurationError(f"{label} contains an invalid newline")
    _, address = parseaddr(value)
    if not address or "@" not in address:
        raise EmailConfigurationError(f"{label} is missing or invalid")
    return address


def _selected_items(
    database: Database, settings: Settings, now: datetime
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    paper_window = build_period_window(
        "paper", "today", settings.timezone, settings.papers.lookback_hours, now
    )
    news = database.list_items(
        kind="news",
        important_only=False,
        verified_only=True,
        eligible_only=True,
        published_since=now - timedelta(hours=24),
        limit=500,
        prompt_version=settings.llm.prompt_version,
    )
    papers = database.list_items(
        kind="paper",
        important_only=False,
        verified_only=True,
        eligible_only=True,
        published_since=paper_window.published_since,
        limit=500,
        prompt_version=settings.llm.prompt_version,
    )
    return (
        sorted(news, key=lambda item: float(item["score"]), reverse=True)[
            : settings.news.max_important
        ],
        sorted(papers, key=lambda item: float(item["score"]), reverse=True)[
            : settings.papers.max_important
        ],
    )


def _text_section(title: str, items: List[Dict[str, Any]]) -> List[str]:
    lines = [title]
    if not items:
        return lines + ["暂无符合条件的新内容。", ""]
    for index, item in enumerate(items, 1):
        summary = item.get("metadata", {}).get("summary_zh") or item.get("summary", "")
        lines.extend(
            [
                f"{index}. {item['title']}",
                f"   {summary}",
                f"   来源：{item['source_name']} | {item['url']}",
            ]
        )
    lines.append("")
    return lines


def _html_section(title: str, items: List[Dict[str, Any]]) -> str:
    if not items:
        body = '<p style="color:#78849a">暂无符合条件的新内容。</p>'
    else:
        cards: List[str] = []
        for item in items:
            metadata = item.get("metadata", {})
            summary = metadata.get("summary_zh") or item.get("summary", "")
            why = metadata.get("why_important", "")
            cards.append(
                '<article style="padding:16px 0;border-bottom:1px solid #e5e9f0">'
                f'<h3 style="margin:0 0 8px;font-size:17px"><a href="{html.escape(item["url"], quote=True)}" '
                'style="color:#155eef;text-decoration:none">'
                f'{html.escape(item["title"])}</a></h3>'
                f'<p style="margin:0 0 8px;color:#344054;line-height:1.65">{html.escape(str(summary))}</p>'
                + (
                    f'<p style="margin:0 0 8px;color:#175cd3"><b>为何重要：</b>{html.escape(str(why))}</p>'
                    if why
                    else ""
                )
                + f'<small style="color:#667085">{html.escape(item["source_name"])} · '
                f'DeepSeek 重要性 {float(item["score"]):.0f}</small></article>'
            )
        body = "".join(cards)
    return (
        f'<section style="margin:24px 0"><h2 style="font-size:20px;margin:0 0 8px">'
        f'{html.escape(title)}</h2>{body}</section>'
    )


def build_daily_message(
    settings: Settings,
    database: Database,
    site_url: str = "",
    now: Optional[datetime] = None,
) -> Tuple[EmailMessage, Dict[str, Any]]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    local = current.astimezone(ZoneInfo(settings.timezone))
    sender = _validated_address(settings.email.username, "email username")
    recipient = _validated_address(
        settings.email.recipient or settings.email.username, "email recipient"
    )
    news, papers = _selected_items(database, settings, current)
    usage = database.llm_usage_summary(local.date().isoformat())
    subject = f"Daily AI Radar｜{local:%Y-%m-%d}｜新闻 {len(news)} · 论文 {len(papers)}"

    text_lines = [
        f"Daily AI Radar · {local:%Y-%m-%d}",
        "来源经过 URL/域名/arXiv 身份校验，语义筛选使用 DeepSeek V4-Pro Thinking max。",
        "",
    ]
    text_lines.extend(_text_section("AI 新发布与技术成果", news))
    text_lines.extend(_text_section("今日 MLLM/VLA × 自动驾驶论文", papers))
    text_lines.append(
        f"今日 DeepSeek 用量：{usage['total_tokens']} / {settings.llm.daily_token_limit} Token，"
        f"估算 ${usage['estimated_cost_usd']:.4f} / ${settings.llm.daily_cost_limit_usd:.2f}。"
    )
    if site_url:
        text_lines.extend(["", f"完整页面：{site_url}"])

    site_link = (
        f'<p><a href="{html.escape(site_url, quote=True)}" style="display:inline-block;padding:10px 16px;'
        'background:#155eef;color:white;text-decoration:none;border-radius:8px">打开完整日报</a></p>'
        if site_url
        else ""
    )
    html_body = (
        '<div style="max-width:720px;margin:auto;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;'
        'color:#101828"><header style="padding:24px;background:#0b1220;color:white;border-radius:12px">'
        f'<small>DAILY AI RADAR</small><h1 style="margin:8px 0">{local:%Y-%m-%d} AI 情报</h1>'
        '<p style="margin:0;color:#cbd5e1">来源硬校验 · DeepSeek V4-Pro · Thinking max</p></header>'
        + _html_section("AI 新发布与技术成果", news)
        + _html_section("今日 MLLM/VLA × 自动驾驶论文", papers)
        + '<aside style="padding:14px;background:#f2f4f7;border-radius:8px;color:#475467">'
        f'今日 DeepSeek 用量：<b>{usage["total_tokens"]}</b> / {settings.llm.daily_token_limit} Token；'
        f'估算 <b>${usage["estimated_cost_usd"]:.4f}</b> / ${settings.llm.daily_cost_limit_usd:.2f}。'
        '</aside>'
        + site_link
        + '</div>'
    )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.email.from_name, sender))
    message["To"] = recipient
    message.set_content("\n".join(text_lines))
    message.add_alternative(html_body, subtype="html")
    return message, {
        "subject": subject,
        "sender": sender,
        "recipient": recipient,
        "news": len(news),
        "papers": len(papers),
        "llm_usage": usage,
    }


def send_daily_email(
    settings: Settings,
    database: Database,
    site_url: str = "",
    now: Optional[datetime] = None,
    smtp_factory: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    if not settings.email.auth_code:
        raise EmailConfigurationError("163 SMTP authorization code is missing")
    message, result = build_daily_message(settings, database, site_url, now)
    factory = smtp_factory or smtplib.SMTP_SSL
    context = ssl.create_default_context()
    with factory(
        settings.email.smtp_host,
        settings.email.smtp_port,
        timeout=30,
        context=context,
    ) as smtp:
        smtp.login(settings.email.username, settings.email.auth_code)
        smtp.send_message(message)
    return result

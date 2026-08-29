from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from .config import load_settings
from .db import Database
from .exporter import export_all
from .pipeline import RadarPipeline
from .sample import seed_demo
from .static_site import build_static_site
from .time_windows import build_period_window
from .verification import audit_database


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daily-radar",
        description="每日 AI 新闻与 MLLM/VLA 自动驾驶论文雷达",
    )
    parser.add_argument(
        "--config", default="", help="配置文件路径，默认 config/settings.yaml"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="初始化 SQLite 数据库")

    collect = subparsers.add_parser("collect", help="运行真实网络采集")
    collect.add_argument(
        "--kind", choices=("news", "paper", "all"), default="all"
    )

    seed = subparsers.add_parser("seed-demo", help="写入隔离的离线演示数据库")
    seed.add_argument(
        "--database", default="", help="默认写入 data/demo_radar.db，不污染主库"
    )
    subparsers.add_parser("purge-demo", help="从主库删除旧版本遗留的 DEMO 条目")

    verify = subparsers.add_parser("verify", help="重新验证已保存条目的链接和来源域名")
    verify.add_argument("--kind", choices=("news", "paper", "all"), default="all")
    verify.add_argument("--limit", type=int, default=500)
    export = subparsers.add_parser("export", help="导出 JSON、Markdown 和 RSS")
    export.add_argument(
        "--news-period",
        choices=("today", "recent", "all"),
        default="recent",
        help="新闻导出范围，默认按配置的滚动窗口",
    )
    export.add_argument(
        "--paper-period",
        choices=("today", "recent", "all"),
        default="today",
        help="论文导出范围，默认仅北京时间当天",
    )
    build_site = subparsers.add_parser(
        "build-site", help="生成可直接发布到 GitHub Pages 的只读静态站点"
    )
    build_site.add_argument("--output", default="site", help="站点输出目录")
    build_site.add_argument(
        "--site-url", default="", help="部署后的站点根 URL，用于 RSS 频道链接"
    )
    subparsers.add_parser("status", help="查看数据库统计和最近任务")

    serve = subparsers.add_parser("serve", help="启动可视化 Dashboard")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    return parser


def _print_run(summary: object) -> None:
    print(
        f"[{summary.kind}] fetched={summary.fetched} accepted={summary.accepted} "
        f"important={summary.important} sources_ok={summary.sources_ok} "
        f"sources_failed={summary.sources_failed}"
    )
    for error in summary.errors:
        print(f"  warning: {error}")


def main(argv: Iterable[str] = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    settings = load_settings(args.config)
    database = Database(settings.database_path)

    if args.command == "init":
        database.initialize()
        print(f"Database initialized: {settings.database_path}")
        return 0
    if args.command == "seed-demo":
        demo_path = (
            Path(args.database).expanduser().resolve()
            if args.database
            else settings.database_path.with_name("demo_radar.db")
        )
        demo_database = Database(demo_path)
        count = seed_demo(demo_database)
        print(f"Seeded {count} demo items into isolated database {demo_path}")
        return 0
    if args.command == "purge-demo":
        database.initialize()
        count = database.purge_demo()
        print(f"Removed {count} demo items from {settings.database_path}")
        return 0
    if args.command == "verify":
        database.initialize()
        result = audit_database(database, settings, args.kind, args.limit)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result["unverified"] or result["unknown_source"] else 0
    if args.command == "collect":
        summaries = RadarPipeline(settings, database).collect(args.kind)
        for summary in summaries:
            _print_run(summary)
        return 1 if summaries and all(summary.sources_ok == 0 for summary in summaries) else 0
    if args.command == "export":
        database.initialize()
        news_window = build_period_window(
            "news",
            args.news_period,
            settings.timezone,
            settings.news.lookback_hours,
        )
        paper_window = build_period_window(
            "paper",
            args.paper_period,
            settings.timezone,
            settings.papers.lookback_hours,
        )
        paths = export_all(
            database,
            settings.output_dir,
            {
                "news": news_window.published_since,
                "paper": paper_window.published_since,
            },
        )
        print(
            f"Export scope: news={news_window.label}, paper={paper_window.label}"
        )
        for path in paths:
            print(path)
        return 0
    if args.command == "build-site":
        output_dir = Path(args.output).expanduser().resolve()
        paths = build_static_site(
            settings,
            output_dir,
            database=database,
            site_url=args.site_url,
        )
        print(f"GitHub Pages snapshot: {output_dir}")
        for path in paths:
            print(path)
        return 0
    if args.command == "status":
        database.initialize()
        print(
            json.dumps(
                {
                    "stats": database.stats(),
                    "verified_stats": database.stats(verified_only=True),
                    "feed_stats": database.stats(
                        verified_only=True, eligible_only=True
                    ),
                    "sources": database.recent_source_checks(),
                    "runs": database.recent_runs(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print("uvicorn 未安装，请先执行 pip install -e .", file=sys.stderr)
            return 2
        from .web.app import create_app

        uvicorn.run(
            create_app(args.config),
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Optional, Sequence

from requirement_monitor import __version__


DEFAULT_DATABASE_URL = "sqlite+pysqlite:///./.state/pulse.db"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version")
    for name in ("db-init", "seed-demo", "run-once"):
        command = commands.add_parser(name)
        command.add_argument("--database-url")
    api = commands.add_parser("api")
    api.add_argument("--database-url")
    api.add_argument("--host", default="127.0.0.1")
    api.add_argument("--port", type=int, default=8000)
    worker = commands.add_parser("worker")
    worker.add_argument("--database-url")
    worker.add_argument("--config", type=Path, default=Path("config.local.json"))
    worker.add_argument("--poll-seconds", type=int, default=30)
    worker.add_argument("--once", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return 0
    database_url = args.database_url or os.getenv("REQUIREMENT_MONITOR_DATABASE_URL", DEFAULT_DATABASE_URL)
    try:
        from requirement_monitor.database import initialize_database
        initialize_database(database_url)
        if args.command == "db-init":
            print("Database schema is ready.")
        elif args.command == "seed-demo":
            from requirement_monitor.service import seed_demo
            print(json.dumps(seed_demo(database_url), ensure_ascii=False, default=str))
        elif args.command == "run-once":
            from requirement_monitor.worker import run_risk_scan
            print(json.dumps(run_risk_scan(database_url), ensure_ascii=False))
        elif args.command == "api":
            import uvicorn
            from requirement_monitor.api import create_app
            uvicorn.run(create_app(database_url), host=args.host, port=args.port)
        elif args.command == "worker":
            from requirement_monitor.worker import worker_loop
            worker_loop(database_url, args.config.resolve(), poll_seconds=args.poll_seconds, once=args.once)
        return 0
    except Exception as error:
        print(f"Command failed: {error}", file=__import__("sys").stderr)
        return 1


def console_main() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    console_main()

import argparse
import json
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from requirement_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")

    init_table_parser = subparsers.add_parser("init-table")
    init_table_parser.add_argument("--config", type=Path)
    mode = init_table_parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_false", dest="apply")
    mode.add_argument("--apply", action="store_true", dest="apply")
    return parser


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    initialize_schema_fn: Optional[Callable[..., Sequence[Any]]] = None,
    load_settings_fn: Optional[Callable[[Optional[Path]], Any]] = None,
) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return 0

    if args.command == "init-table":
        if initialize_schema_fn is None:
            from requirement_monitor.schema import initialize_schema

            initialize_schema_fn = initialize_schema
        if load_settings_fn is None:
            from requirement_monitor.config import load_settings

            load_settings_fn = load_settings

        settings = load_settings_fn(args.config)
        operations = initialize_schema_fn(
            settings.bitable_url, apply=args.apply
        )
        if not operations:
            print("Schema is up to date.")
            return 0
        for operation in operations:
            print(
                "{} {}".format(
                    operation.kind,
                    json.dumps(
                        operation.payload,
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            )
        return 0
    return 2


def console_main() -> None:
    raise SystemExit(main())

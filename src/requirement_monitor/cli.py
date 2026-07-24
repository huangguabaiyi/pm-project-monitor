import argparse
from typing import Optional, Sequence

from requirement_monitor import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    parser.add_argument("command", choices=["version"])
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "version":
        print(f"requirement-monitor {__version__}")
        return 0
    return 2


def console_main() -> None:
    raise SystemExit(main())

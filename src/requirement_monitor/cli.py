import argparse
from typing import Optional, Sequence

from requirement_monitor import __version__


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="requirement-monitor")
    parser.add_argument("command", choices=["version"])
    args = parser.parse_args(argv)

    if args.command == "version":
        print(f"requirement-monitor {__version__}")
    return 0


def console_main() -> None:
    raise SystemExit(main())

#!/usr/bin/env python3
"""skill-sync: check upstream skill versions and rebuild the affected bundles.

Commands are filled in task by task; this entry point exists from task 1 so the
exit-code contract and the CLI surface are fixed early.
"""
from __future__ import annotations

import argparse
import sys

from core.exitcodes import EXIT_OK

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skill_sync",
        description="Check upstream skill versions and rebuild DSH skill bundles.",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="workspace root containing dsh-plugin-build/ (default: current directory)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="report local vs upstream versions (read-only)")
    sub.add_parser("update", help="rebuild a bundle from upstream into a tarball")
    sub.add_parser("verify", help="check local consistency without network")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    parser.error(f"command not implemented yet: {args.command}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

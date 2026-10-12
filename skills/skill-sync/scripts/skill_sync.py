#!/usr/bin/env python3
"""skill-sync: check upstream skill versions and rebuild the affected bundles.

Commands: ``check`` (read-only), ``update`` (rebuild to a tarball), ``verify``
(local consistency). ``update`` and ``verify`` land in later tasks; the exit-code
contract is fixed here.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from core import registry
from core.exitcodes import EXIT_OK, EXIT_SELFCHECK
from core.registry import RegistryError, resolve_workspace
from core.report import CheckRow, render_check
from core.upstream import GithubClient, local_version

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

    check = sub.add_parser("check", help="report local vs upstream versions (read-only)")
    check.add_argument("packages", nargs="*", metavar="PACKAGE", help="limit to these packages")
    check.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    check.add_argument("--refresh", action="store_true", help="ignore the 10-minute cache")
    check.add_argument("--offline", action="store_true", help="use only cached results")

    sub.add_parser("update", help="rebuild a bundle from upstream into a tarball")
    sub.add_parser("verify", help="check local consistency without network")
    return parser


def default_cache_dir(workspace: Path) -> Path:
    return workspace / "tmpwork" / "skill-sync-cache"


def run_check(
    entries: Sequence,
    client: GithubClient,
    *,
    as_json: bool = False,
    stream=None,
) -> int:
    stream = stream if stream is not None else sys.stdout
    rows: list[CheckRow] = []
    for entry in entries:
        info = client.latest(entry)
        rows.append(
            CheckRow(
                package=entry.package,
                local=local_version(entry),
                remote=info.tag,
                status=info.status,
                reason=info.reason,
                manual_skills=entry.manual_skills,
            )
        )
    print(render_check(rows, as_json=as_json), file=stream)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(Path.cwd(), args.workspace)
        entries = registry.load(workspace)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SELFCHECK

    if args.command == "check":
        try:
            if args.packages:
                entries = registry.select(entries, args.packages)
        except RegistryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SELFCHECK

        client = GithubClient(
            token=os.environ.get("GITHUB_TOKEN"),
            cache_dir=default_cache_dir(workspace),
            refresh=args.refresh,
            offline=args.offline,
        )
        return run_check(entries, client, as_json=args.json)

    parser.error(f"command not implemented yet: {args.command}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

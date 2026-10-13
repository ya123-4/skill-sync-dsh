#!/usr/bin/env python3
"""Download the pinned upstream snapshot for each registry entry.

The golden-regression tests compare a recipe's output with the bundle installed
today, so they need the exact upstream bytes that bundle was built from. Those
bytes live under ``tmpwork/skill-sync-upstream`` and are deliberately not
committed.

Usage:
    python tools/fetch_snapshots.py [--workspace DIR] [--only PACKAGE ...] [--refresh]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "skills" / "skill-sync" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.exitcodes import EXIT_OK, EXIT_SELFCHECK, EXIT_UPSTREAM  # noqa: E402
from core.registry import RegistryError, load, resolve_workspace, select  # noqa: E402
from core.upstream import SnapshotError, fetch_snapshot, local_version, snapshot_slug  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def snapshot_ref(entry) -> str:
    """The tag the installed bundle was built from: prefix + its own version."""
    return f"{entry.tag_prefix}{local_version(entry)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--workspace", default=None, help="workspace root (default: cwd)")
    parser.add_argument("--only", nargs="*", metavar="PACKAGE", help="limit to these packages")
    parser.add_argument("--refresh", action="store_true", help="re-download cached snapshots")
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(Path.cwd(), args.workspace)
        entries = load(workspace)
        if args.only:
            entries = select(entries, args.only)
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SELFCHECK

    cache = workspace / "tmpwork" / "skill-sync-upstream"
    failures = 0
    for entry in entries:
        ref = snapshot_ref(entry)
        if args.refresh:
            shutil.rmtree(cache / "snapshots" / snapshot_slug(entry.repo, ref), ignore_errors=True)
        try:
            checkout = fetch_snapshot(entry, ref, cache)
        except SnapshotError as exc:
            print(f"FAIL {entry.package} @ {ref}: {exc}")
            failures += 1
            continue
        print(f"ok   {entry.package} @ {ref}")
        print(f"     {checkout}")
    return EXIT_UPSTREAM if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

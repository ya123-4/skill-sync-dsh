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
import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "skills" / "skill-sync" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.exitcodes import EXIT_OK, EXIT_SELFCHECK, EXIT_UPSTREAM  # noqa: E402
from core.registry import RegistryError, load, resolve_workspace, select  # noqa: E402
from core.upstream import (  # noqa: E402
    SnapshotError,
    _remove_tree,
    fetch_npm_snapshot,
    fetch_snapshot,
    local_revision,
    local_version,
    npm_snapshot_slug,
    snapshot_slug,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def refresh_snapshot_cache(cache, slug: str) -> Path:
    """Drop one cached snapshot entry before re-downloading it, and name it back.

    ``--refresh`` used to call a plain ``shutil.rmtree``. That call cannot reach a
    descendant past MAX_PATH -- this host runs with ``LongPathsEnabled=0`` and the
    alibabacloud monorepo reaches 292 characters under the real cache root -- so it
    aborted at the first such descendant and left the ``.complete`` marker behind.
    ``_fetch_tarball`` then saw the marker, treated the entry as a cache hit and
    returned the old checkout: ``--refresh`` reported success while serving stale
    bytes. ``_remove_tree`` walks the tree through an extended-length path, which is
    the same helper the fetch path uses to clear a failed attempt.
    """
    root = Path(cache) / "snapshots" / slug
    _remove_tree(root)
    return root


def snapshot_ref(entry) -> str:
    """The upstream revision the installed bundle was built from.

    A GitHub-tagged bundle: tag prefix + its own version. A bundle vendoring an npm
    package: the release its ``optionalDependencies`` pin names, because the
    plugin's own version line is unrelated to the package's. A commit-tracked
    bundle has no tag at all, so it replays the revision its own state file
    records -- or the registry's pinned baseline before the first sync.
    """
    if entry.kind == "npm":
        return local_version(entry)
    if entry.kind == "github-commits":
        return local_revision(entry) or entry.baseline_commit or ""
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
        if not ref:
            print(f"FAIL {entry.package}: no recorded revision to fetch")
            failures += 1
            continue
        slug = (
            npm_snapshot_slug(entry.source, ref)
            if entry.kind == "npm"
            else snapshot_slug(entry.repo, ref)
        )
        if args.refresh:
            refresh_snapshot_cache(cache, slug)
        try:
            checkout = (
                fetch_npm_snapshot(entry, ref, cache)
                if entry.kind == "npm"
                else fetch_snapshot(entry, ref, cache)
            )
        except SnapshotError as exc:
            print(f"FAIL {entry.package} @ {ref}: {exc}")
            failures += 1
            continue
        print(f"ok   {entry.package} @ {ref}")
        print(f"     {checkout}")
    return EXIT_UPSTREAM if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

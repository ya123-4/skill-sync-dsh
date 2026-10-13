#!/usr/bin/env python3
"""Fetch the pinned open-design material that ``recipes.open_design`` consumes.

open-design is a 3.5 GB product repository, so a codeload tarball is the wrong
transport. The curated slice is fetched file by file from jsDelivr at a pinned
commit into ``tmpwork/od-source/<commit>/``, mirroring upstream paths:

    craft/*.md                            (minus FUTURE_SECTIONS.md)
    design-systems/<brand>/<kept files>
    design-templates/<template>/**
    plugins/_official/scenarios/<scenario>/**
    plugins/_official/atoms/<atom>/**

A file that is already cached and non-empty is never re-downloaded, so an
interrupted run resumes.

Usage:
    python tools/fetch_open_design_sources.py [--commit <sha>] [--workspace DIR]
"""
from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.request
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "skills" / "skill-sync" / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from core.exitcodes import EXIT_OK, EXIT_SELFCHECK, EXIT_UPSTREAM  # noqa: E402
from core.registry import RegistryError, load, resolve_workspace  # noqa: E402
from recipes.open_design import (  # noqa: E402
    ATOMS,
    DESIGN_SYSTEMS,
    SCENARIOS,
    SYSTEM_KEEP,
    TEMPLATES,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

CDN = "https://cdn.jsdelivr.net/gh/{repo}@{ref}/{path}"
API_TREE = "https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"
CTX = ssl.create_default_context()
PACKAGE = "open-design-skills-dsh"


def fetch(url: str, dest: Path, attempts: int = 4) -> int:
    """Download to ``dest`` unless it is already cached; returns bytes written."""
    if dest.is_file() and dest.stat().st_size > 0:
        return 0
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "skill-sync-dsh"})
            with urllib.request.urlopen(request, timeout=120, context=CTX) as response:
                data = response.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            return len(data)
        except Exception as exc:  # noqa: BLE001 - retry any transport failure
            last = exc
            if attempt < attempts:
                time.sleep(2.0 * attempt)
    raise RuntimeError(f"{url}: {last}")


def tree_paths(repo: str, ref: str, cache: Path) -> dict[str, int]:
    cached = cache / f"tree-{ref}.json"
    if cached.is_file():
        tree = json.loads(cached.read_text(encoding="utf-8"))
    else:
        request = urllib.request.Request(
            API_TREE.format(repo=repo, ref=ref),
            headers={"User-Agent": "skill-sync-dsh", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(request, timeout=120, context=CTX) as response:
            tree = json.loads(response.read().decode("utf-8"))
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(tree), encoding="utf-8")
    return {item["path"]: item.get("size", 0) for item in tree["tree"] if item["type"] == "blob"}


def wanted_paths(paths: dict[str, int]) -> list[str]:
    wanted: list[str] = []
    prefixes = (
        [f"design-templates/{name}/" for name in TEMPLATES]
        + [f"plugins/_official/scenarios/{name}/" for name in SCENARIOS]
        + [f"plugins/_official/atoms/{name}/" for name in ATOMS]
    )
    for path in sorted(paths):
        if any(path.startswith(prefix) for prefix in prefixes):
            wanted.append(path)
            continue
        if path.startswith("craft/") and path.endswith(".md"):
            if not path.endswith("FUTURE_SECTIONS.md"):
                wanted.append(path)
            continue
        for brand in DESIGN_SYSTEMS:
            prefix = f"design-systems/{brand}/"
            if path.startswith(prefix) and path[len(prefix):] in SYSTEM_KEEP:
                wanted.append(path)
                break
    return wanted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--commit", default=None, help="upstream commit (default: registry pin)")
    parser.add_argument("--workspace", default=None, help="workspace root (default: cwd)")
    args = parser.parse_args(argv)

    try:
        workspace = resolve_workspace(Path.cwd(), args.workspace)
        entries = load(workspace)
        entry = next(item for item in entries if item.package == PACKAGE)
    except (RegistryError, StopIteration) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SELFCHECK

    commit = args.commit or entry.baseline_commit
    if not commit:
        print(f"error: {PACKAGE} has no baselineCommit; pass --commit", file=sys.stderr)
        return EXIT_SELFCHECK

    cache = workspace / "tmpwork" / "od-source"
    root = cache / commit
    try:
        paths = tree_paths(entry.repo, commit, cache)
    except Exception as exc:  # noqa: BLE001
        print(f"error: cannot list the upstream tree: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_UPSTREAM

    targets = wanted_paths(paths)
    if not targets:
        print(f"error: nothing selected from {entry.repo}@{commit}", file=sys.stderr)
        return EXIT_UPSTREAM

    downloaded = cached = failed = 0
    for path in targets:
        destination = root / path
        try:
            written = fetch(CDN.format(repo=entry.repo, ref=commit, path=path), destination)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {path}: {exc}")
            failed += 1
            continue
        if written:
            downloaded += 1
        else:
            cached += 1

    print(f"upstream  : {entry.repo}@{commit}")
    print(f"material  : {root}")
    print(f"selected  : {len(targets)} files")
    print(f"downloaded: {downloaded}   already cached: {cached}   failed: {failed}")
    if failed:
        print("re-run to retry the failed files; cached files are not re-downloaded")
        return EXIT_UPSTREAM
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

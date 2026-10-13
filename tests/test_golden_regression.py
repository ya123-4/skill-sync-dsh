"""Golden regression: every recipe must reproduce the installed bundle exactly.

This is why the recipes are trustworthy. Each test replays a recipe against the
exact upstream tag the installed bundle claims to come from and compares the
result with what is on disk today, byte for byte. It answers the only question
that matters about a recipe: "is this the same transformation that produced the
bundle we already ship?"

The upstream bytes live under ``tmpwork/skill-sync-upstream/snapshots`` and are
never committed. A missing snapshot skips with the command that fetches it,
rather than passing quietly.
"""
import json
from pathlib import Path

import pytest

from core.registry import load
from core.upstream import fetch_snapshot, snapshot_slug
from recipes.base import RecipeContext

PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = PKG_ROOT.parent.parent
SNAPSHOT_CACHE = WORKSPACE / "tmpwork" / "skill-sync-upstream"


def registry_entry(package: str):
    entries = load(WORKSPACE)
    return next(entry for entry in entries if entry.package == package)


def installed_version(entry) -> str:
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    return manifest["version"]


def pinned_ref(entry) -> str:
    return f"{entry.tag_prefix}{installed_version(entry)}"


def snapshot_for(entry) -> Path:
    ref = pinned_ref(entry)
    root = SNAPSHOT_CACHE / "snapshots" / snapshot_slug(entry.repo, ref)
    if not (root / ".complete").is_file():
        pytest.skip(
            f"missing upstream snapshot for {entry.package}@{ref}; "
            f"run: python tools/fetch_snapshots.py --only {entry.package}"
        )
    return fetch_snapshot(entry, ref, SNAPSHOT_CACHE)


def context_for(entry, staging: Path) -> RecipeContext:
    return RecipeContext(
        upstream_dir=snapshot_for(entry),
        upstream_ref=pinned_ref(entry),
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )


def assert_tree_identical(produced: Path, shipped: Path) -> None:
    """Same file set, same bytes — the recipe reproduces the shipped bundle."""
    produced_files = {
        path.relative_to(produced).as_posix(): path
        for path in produced.rglob("*")
        if path.is_file()
    }
    shipped_files = {
        path.relative_to(shipped).as_posix(): path
        for path in shipped.rglob("*")
        if path.is_file()
    }

    missing = sorted(set(shipped_files) - set(produced_files))
    extra = sorted(set(produced_files) - set(shipped_files))
    differing = sorted(
        relative
        for relative in set(produced_files) & set(shipped_files)
        if produced_files[relative].read_bytes() != shipped_files[relative].read_bytes()
    )

    assert not missing, f"recipe produced nothing for {len(missing)} file(s): {missing[:8]}"
    assert not extra, f"recipe produced {len(extra)} unexpected file(s): {extra[:8]}"
    assert not differing, f"{len(differing)} file(s) differ: {differing[:8]}"


def test_superpowers_golden(scratch):
    from recipes.superpowers import assemble

    entry = registry_entry("superpowers-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")

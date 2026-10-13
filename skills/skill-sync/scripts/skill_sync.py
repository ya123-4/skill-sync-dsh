#!/usr/bin/env python3
"""skill-sync: check upstream skill versions and rebuild the affected bundles.

Commands: ``check`` (read-only), ``update`` (rebuild to a tarball), ``verify``
(local consistency). Nothing here ever touches the DSH profile: an update ends
with a tarball and the exact command a human should run next.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from core import registry, state
from core.apply import ApplyFailed, apply_candidate
from core.exitcodes import (
    EXIT_APPLY,
    EXIT_OK,
    EXIT_PACK,
    EXIT_SELFCHECK,
    EXIT_STRUCTURE,
    EXIT_UPSTREAM,
    EXIT_VERIFY,
)
from core.pack import PackFailed, pack
from core.registry import RegistryError, resolve_workspace
from core.report import CheckRow, UpdateRow, render_check, render_update
from core.upstream import (
    STATUS_UPDATE_AVAILABLE,
    GithubClient,
    SnapshotError,
    fetch_snapshot,
    local_version,
)
from core.verify import blocking, verify_candidate
from core.versions import normalize_tag
from recipes.base import AnchorNotFound, RecipeContext, StructureChanged

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

    update = sub.add_parser("update", help="rebuild bundles from upstream into tarballs")
    update.add_argument("packages", nargs="*", metavar="PACKAGE")
    update.add_argument("--all", action="store_true", help="update every managed bundle")
    update.add_argument(
        "--accept-structure-change",
        action="store_true",
        help="proceed when the skill set changed (added, removed or renamed skills)",
    )
    update.add_argument("--dry-run", action="store_true", help="stop after verification")
    update.add_argument("--rebuild", action="store_true", help="rebuild even without a new release")
    update.add_argument("--keep-staging", action="store_true", help="keep the staging directory")
    update.add_argument("--no-pack", action="store_true", help="land the tree without packing")
    update.add_argument("--refresh", action="store_true", help="ignore the 10-minute cache")
    update.add_argument("--offline", action="store_true", help="use only cached results")

    verify = sub.add_parser("verify", help="check local consistency without network")
    verify.add_argument("packages", nargs="*", metavar="PACKAGE")
    verify.add_argument("--all", action="store_true", help="verify every managed bundle")
    verify.add_argument("--profile", default=None, help="DSH profile directory to scan")
    return parser


# ---- paths --------------------------------------------------------------


def default_cache_dir(workspace: Path) -> Path:
    return workspace / "tmpwork" / "skill-sync-cache"


def default_snapshot_dir(workspace: Path) -> Path:
    return workspace / "tmpwork" / "skill-sync-upstream"


def default_staging_root(workspace: Path) -> Path:
    return workspace / "tmpwork" / "skill-sync-staging"


def default_dist_dir(workspace: Path) -> Path:
    return workspace / "dsh-plugin-build" / "dist"


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---- shared helpers -----------------------------------------------------


def load_recipe(name: str):
    """Import ``recipes.<name>``; the registry decides which recipes exist."""
    try:
        return importlib.import_module(f"recipes.{name}")
    except ImportError as exc:
        raise StructureChanged(f"unknown recipe {name!r} in the registry: {exc}") from exc


def default_upstream_provider(workspace: Path) -> Callable[[object, str], Path]:
    """Where an entry's upstream tree comes from.

    Four bundles are codeload tarballs at a tag or commit. open-design is served
    per file from jsDelivr (its repository is 3.5 GB), so it reads the material
    cache that ``tools/fetch_open_design_sources.py`` fills; a missing cache is an
    upstream failure whose message carries the exact command.
    """
    snapshots = default_snapshot_dir(workspace)
    sources = workspace / "tmpwork" / "od-source"

    def _provider(entry, ref: str) -> Path:
        if entry.recipe == "open_design":
            commit = entry.baseline_commit or ref
            root = sources / commit
            if not root.is_dir():
                raise SnapshotError(
                    f"{entry.package}: open-design material for {commit} is not cached; run "
                    f"python tools/fetch_open_design_sources.py --commit {commit}"
                )
            return root
        return fetch_snapshot(entry, ref, snapshots)

    return _provider


def _diff_skills(previous: Path, candidate: Path):
    def digest_map(root: Path) -> dict[str, str]:
        if not root.is_dir():
            return {}
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file()
        }

    before = digest_map(previous)
    after = digest_map(candidate)
    before_skills = {relative.split("/")[0] for relative in before}
    after_skills = {relative.split("/")[0] for relative in after}
    changed = sorted(
        relative for relative in set(before) & set(after) if before[relative] != after[relative]
    )
    return (
        tuple(sorted(after_skills - before_skills)),
        tuple(sorted(before_skills - after_skills)),
        tuple(changed),
    )


def _restore_backup(plugin_dir: Path, backup: Path) -> None:
    """Undo a landed candidate, then drop the backup so nothing is left behind."""
    skills = plugin_dir / "skills"
    if (backup / "skills").is_dir():
        if skills.exists():
            shutil.rmtree(skills, ignore_errors=True)
        shutil.move(str(backup / "skills"), str(skills))
    if (backup / "package.json").is_file():
        shutil.copy2(backup / "package.json", plugin_dir / "package.json")
    shutil.rmtree(backup, ignore_errors=True)


# ---- check --------------------------------------------------------------


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


# ---- update -------------------------------------------------------------


def run_update(
    entries: Sequence,
    client: GithubClient,
    workspace: Path | str,
    *,
    upstream_provider=None,
    pack_runner=None,
    dist_dir: Path | str | None = None,
    staging_root: Path | str | None = None,
    accept_structure_change: bool = False,
    dry_run: bool = False,
    rebuild: bool = False,
    keep_staging: bool = False,
    no_pack: bool = False,
    stream=None,
) -> int:
    stream = stream if stream is not None else sys.stdout
    workspace = Path(workspace)
    provider = upstream_provider or default_upstream_provider(workspace)
    dist = Path(dist_dir) if dist_dir is not None else default_dist_dir(workspace)
    staging_root = (
        Path(staging_root) if staging_root is not None else default_staging_root(workspace)
    )

    rows: list[UpdateRow] = []
    exit_code = EXIT_OK
    for entry in entries:
        row = _update_one(
            entry,
            client,
            provider,
            dist,
            staging_root,
            accept_structure_change=accept_structure_change,
            dry_run=dry_run,
            rebuild=rebuild,
            keep_staging=keep_staging,
            no_pack=no_pack,
            pack_runner=pack_runner,
        )
        rows.append(row)
        if row.code and exit_code == EXIT_OK:
            exit_code = row.code

    print(render_update(rows), file=stream)
    return exit_code


def _update_one(
    entry,
    client,
    provider,
    dist: Path,
    staging_root: Path,
    *,
    accept_structure_change: bool,
    dry_run: bool,
    rebuild: bool,
    keep_staging: bool,
    no_pack: bool,
    pack_runner,
) -> UpdateRow:
    before_version = local_version(entry)
    info = client.latest(entry)

    if info.status == STATUS_UPDATE_AVAILABLE and info.tag:
        ref = info.tag
        target_version = normalize_tag(info.tag, entry.tag_prefix) or before_version
    elif rebuild:
        ref = entry.baseline_commit or f"{entry.tag_prefix}{before_version}"
        target_version = before_version
    else:
        return UpdateRow(entry.package, "skipped", f"already up to date ({before_version})")

    try:
        upstream_dir = provider(entry, ref)
    except SnapshotError as exc:
        return UpdateRow(entry.package, "failed", str(exc), code=EXIT_UPSTREAM)
    except Exception as exc:  # noqa: BLE001 - any provider failure is an upstream failure
        return UpdateRow(
            entry.package, "failed", f"{type(exc).__name__}: {exc}", code=EXIT_UPSTREAM
        )

    staging = Path(staging_root) / f"{entry.package}-{_stamp()}"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)

    try:
        recipe = load_recipe(entry.recipe)
        context = RecipeContext(
            upstream_dir=Path(upstream_dir),
            upstream_ref=ref,
            plugin_dir=entry.plugin_dir,
            staging_dir=staging,
            entry=entry,
        )
        recipe.assemble(context)
    except StructureChanged as exc:
        return UpdateRow(entry.package, "blocked", str(exc), code=EXIT_STRUCTURE, staging=staging)
    except AnchorNotFound as exc:
        return UpdateRow(
            entry.package,
            "blocked",
            f"upstream text moved: {exc}",
            code=EXIT_STRUCTURE,
            staging=staging,
        )

    previous = entry.plugin_dir / "skills"
    manual = (
        state.manual_snapshot(entry.plugin_dir, entry.manual_skills)
        if entry.manual_skills
        else None
    )
    issues = verify_candidate(
        staging,
        entry,
        previous=previous if previous.is_dir() else None,
        manual_snapshot=manual,
    )
    structure_issues = [issue for issue in issues if issue.code == "structure-changed"]
    blockers = blocking(issues, ignore_codes={"structure-changed"})

    if blockers:
        return UpdateRow(
            entry.package,
            "failed",
            "verification failed",
            code=EXIT_VERIFY,
            issues=tuple(f"{issue.code}: {issue.message}" for issue in blockers),
            staging=staging,
        )
    if structure_issues and not accept_structure_change:
        return UpdateRow(
            entry.package,
            "blocked",
            "unexpected structure change (re-run with --accept-structure-change)",
            code=EXIT_STRUCTURE,
            issues=tuple(f"{issue.code}: {issue.message}" for issue in structure_issues),
            staging=staging,
        )

    added, removed, changed = _diff_skills(previous, staging / "skills")

    if dry_run:
        return UpdateRow(
            entry.package,
            "dry-run",
            f"{before_version} -> {target_version}",
            added=added,
            removed=removed,
            changed=changed,
            staging=staging,
        )

    try:
        backup = apply_candidate(entry.plugin_dir, staging / "skills", target_version)
    except ApplyFailed as exc:
        return UpdateRow(
            entry.package,
            "failed",
            str(exc),
            code=EXIT_APPLY,
            added=added,
            removed=removed,
            changed=changed,
            staging=staging,
        )

    artifact = None
    if not no_pack:
        try:
            artifact = pack(entry.plugin_dir, dist, runner=pack_runner)
        except PackFailed as exc:
            _restore_backup(entry.plugin_dir, backup)
            return UpdateRow(
                entry.package,
                "failed",
                str(exc),
                code=EXIT_PACK,
                added=added,
                removed=removed,
                changed=changed,
                staging=staging,
            )

    state.write(
        entry.plugin_dir,
        {
            "package": entry.package,
            "upstream": entry.repo,
            "syncedTag": ref,
            "syncedCommit": info.commit,
            "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overlayVersion": 1,
            "skills": state.compute_tree_hashes(entry.plugin_dir / "skills"),
        },
    )

    if not keep_staging:
        shutil.rmtree(staging, ignore_errors=True)

    return UpdateRow(
        entry.package,
        "updated",
        f"{before_version} -> {target_version}",
        added=added,
        removed=removed,
        changed=changed,
        artifact=artifact,
    )


# ---- entry point --------------------------------------------------------


def _client(workspace: Path, args) -> GithubClient:
    return GithubClient(
        token=os.environ.get("GITHUB_TOKEN"),
        cache_dir=default_cache_dir(workspace),
        refresh=bool(getattr(args, "refresh", False)),
        offline=bool(getattr(args, "offline", False)),
    )


def _select(entries, packages, want_all: bool, parser):
    if packages:
        return registry.select(entries, list(packages))
    if want_all:
        return list(entries)
    parser.error("name at least one package or pass --all")
    return []


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
            selected = registry.select(entries, list(args.packages)) if args.packages else entries
        except RegistryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SELFCHECK
        return run_check(selected, _client(workspace, args), as_json=args.json)

    if args.command == "update":
        try:
            selected = _select(entries, args.packages, args.all, parser)
        except RegistryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SELFCHECK
        return run_update(
            selected,
            _client(workspace, args),
            workspace,
            accept_structure_change=args.accept_structure_change,
            dry_run=args.dry_run,
            rebuild=args.rebuild,
            keep_staging=args.keep_staging,
            no_pack=args.no_pack,
        )

    parser.error(f"command not implemented yet: {args.command}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

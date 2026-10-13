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
import json
import os
import shutil
import sys
import tarfile
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from core import registry, state
from core.apply import ApplyFailed, apply_candidate, restore_backup
from core.state import StateError
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
    STATUS_UNKNOWN,
    STATUS_UP_TO_DATE,
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
    update.add_argument(
        "--profile",
        default=None,
        help="DSH profile to scan for skill-name collisions (default: the desktop profile)",
    )
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


def default_profile() -> Path | None:
    """The desktop profile to scan for skill-name collisions, when it exists."""
    home = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if not home:
        return None
    candidate = Path(home) / ".dsh" / "profiles" / "desktop"
    return candidate if candidate.is_dir() else None


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
            # The ref decides which material is read: a rebuild passes the pinned
            # commit, an update passes the commit the new tag points at. Missing
            # material is an error, never a silent reuse of the bytes we have.
            root = sources / ref
            if not root.is_dir():
                raise SnapshotError(
                    f"{entry.package}: open-design material for {ref} is not cached; run "
                    f"python tools/fetch_open_design_sources.py --commit {ref}"
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
        try:
            info = client.latest(entry)
            row = CheckRow(
                package=entry.package,
                local=local_version(entry),
                remote=info.tag,
                status=info.status,
                reason=info.reason,
                manual_skills=entry.manual_skills,
            )
        except Exception as exc:  # noqa: BLE001 - one bad lookup must not lose the table
            row = CheckRow(
                package=entry.package,
                local="?",
                remote=None,
                status=STATUS_UNKNOWN,
                reason=f"{type(exc).__name__}: {exc}",
                manual_skills=entry.manual_skills,
            )
        rows.append(row)
    print(render_check(rows, as_json=as_json), file=stream)

    # A run that compared nothing at all is an upstream failure, not a pass;
    # otherwise the documented code 3 would be unreachable for `check`.
    if rows and all(row.status == STATUS_UNKNOWN for row in rows):
        return EXIT_UPSTREAM
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
    profile: Path | str | None = None,
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
    profile_skills = _profile_skills(Path(profile)) if profile is not None else {}

    rows: list[UpdateRow] = []
    exit_code = EXIT_OK
    for entry in entries:
        try:
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
                profile_skills=profile_skills,
            )
        except Exception as exc:  # noqa: BLE001 - one broken bundle must not lose the batch
            # `GithubClient.latest` already turns upstream failures into `unknown`
            # rows, so an exception escaping here means this bundle's own files could
            # not be read: a self-check failure (code 2), not an upstream one.
            row = UpdateRow(
                entry.package,
                "failed",
                f"cannot read this bundle: {type(exc).__name__}: {exc}",
                code=EXIT_SELFCHECK,
            )
            traceback.print_exc()
        rows.append(row)
        if row.code and exit_code == EXIT_OK:
            exit_code = row.code

    print(render_update(rows), file=stream)
    return exit_code


def _rollback_detail(plugin_dir: Path, backup: Path) -> str:
    """Roll back and describe any failure to do so, never hiding the original error."""
    try:
        restore_backup(plugin_dir, backup)
    except Exception as exc:  # noqa: BLE001 - the original failure is the one that matters
        return f" | rollback also failed: {type(exc).__name__}: {exc}"
    return ""


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
    profile_skills: dict | None = None,
) -> UpdateRow:
    before_version = local_version(entry)
    info = client.latest(entry)

    if info.status == STATUS_UPDATE_AVAILABLE and info.tag:
        ref = info.tag
        target_version = normalize_tag(info.tag, entry.tag_prefix) or before_version
    elif rebuild:
        ref = entry.baseline_commit or f"{entry.tag_prefix}{before_version}"
        target_version = before_version
    elif info.status == STATUS_UP_TO_DATE:
        return UpdateRow(entry.package, "skipped", f"already up to date ({before_version})")
    else:
        # "unknown" is not "current": a 404, a rate limit or --offline must never
        # be reported as a bundle that needs nothing.
        return UpdateRow(
            entry.package,
            "unknown",
            f"cannot compare with upstream: {info.reason or 'no answer from upstream'}",
            code=EXIT_UPSTREAM,
        )

    # The state file records both halves: the tag that version checks compare the
    # bundle against, and the exact revision its bytes came from (a commit, for a
    # bundle pinned to one). A commit must never be stored as the tag.
    if normalize_tag(ref, entry.tag_prefix) is not None:
        synced_tag = ref
        synced_commit = info.commit
    else:
        synced_tag = f"{entry.tag_prefix}{target_version}"
        synced_commit = ref

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
    # Names owned by *other* installed bundles; the bundle's own installed copy is
    # expected to share names with the candidate.
    foreign_skills = {
        name for name, owner in (profile_skills or {}).items() if owner != entry.package
    }
    issues = verify_candidate(
        staging,
        entry,
        previous=previous if previous.is_dir() else None,
        profile_skills=foreign_skills,
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
        except Exception as exc:  # noqa: BLE001 - any pack failure rolls the landing back
            detail = f"packing failed: {type(exc).__name__}: {exc}"
            detail += _rollback_detail(entry.plugin_dir, backup)
            return UpdateRow(
                entry.package,
                "failed",
                detail,
                code=EXIT_PACK,
                added=added,
                removed=removed,
                changed=changed,
                staging=staging,
            )
        except BaseException:
            # An interrupt during packing must roll the landing back before it propagates.
            try:
                restore_backup(entry.plugin_dir, backup)
            except Exception:  # noqa: BLE001 - the interrupt is what must propagate
                pass
            raise

    try:
        state.write(
            entry.plugin_dir,
            {
                "package": entry.package,
                "upstream": entry.repo,
                "syncedTag": synced_tag,
                "syncedCommit": synced_commit,
                "syncedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "overlayVersion": 1,
                "skills": state.compute_tree_hashes(entry.plugin_dir / "skills"),
            },
        )
    except Exception as exc:  # noqa: BLE001 - a landing without its record is not a landing
        detail = f"cannot write the state file: {type(exc).__name__}: {exc}"
        if artifact is not None:
            # The tarball belongs to a landing that is being rolled back, so it must
            # not stay in dist/ advertising a version this bundle does not contain.
            # If packing moved an earlier artifact aside, that one goes back.
            previous_artifact = artifact.parent / ".previous" / artifact.name
            try:
                artifact.unlink()
                if previous_artifact.is_file():
                    shutil.move(str(previous_artifact), str(artifact))
            except OSError as cleanup_exc:
                detail += f" | orphan tarball left behind: {cleanup_exc}"
        detail += _rollback_detail(entry.plugin_dir, backup)
        return UpdateRow(
            entry.package,
            "failed",
            detail,
            code=EXIT_APPLY,
            added=added,
            removed=removed,
            changed=changed,
            staging=staging,
        )
    except BaseException:
        # Same window as packing: an interrupt here must not leave an unrecorded landing.
        try:
            restore_backup(entry.plugin_dir, backup)
        except Exception:  # noqa: BLE001 - the interrupt is what must propagate
            pass
        raise

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


# ---- verify -------------------------------------------------------------


def _profile_skills(profile_dir: Path) -> dict[str, str]:
    """``{skill name: owning bundle}`` for every bundle installed in a profile."""
    found: dict[str, str] = {}
    node_modules = Path(profile_dir) / "node_modules"
    if not node_modules.is_dir():
        return found
    for bundle in sorted(node_modules.iterdir()):
        skills = bundle / "skills"
        try:
            if not bundle.is_dir() or not skills.is_dir():
                continue
            for skill in sorted(skills.iterdir()):
                if (skill / "SKILL.md").is_file():
                    found[skill.name] = bundle.name
        except OSError:
            # A lock, a broken junction or a half-removed package must not stop the
            # scan: the profile is not ours, and one unreadable bundle is not a reason
            # to fail a verify or an update.
            continue
    return found


def _disk_skill_hashes(skills_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(skills_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skills_dir.rglob("*")
        if path.is_file()
    }


def _tgz_skill_hashes(artifact: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    with tarfile.open(artifact) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            parts = member.name.split("/")
            if len(parts) < 3 or parts[0] != "package" or parts[1] != "skills":
                continue
            data = archive.extractfile(member).read()
            hashes["/".join(parts[2:])] = hashlib.sha256(data).hexdigest()
    return hashes


def _verify_entry(entry, dist: Path, profile_skills: dict | None) -> list[str]:
    """Everything that can be checked about one bundle without the network."""
    issues: list[str] = []
    version = ""

    manifest_path = entry.plugin_dir / "package.json"
    if not manifest_path.is_file():
        issues.append("package.json is missing")
    else:
        try:
            version = str(
                json.loads(manifest_path.read_text(encoding="utf-8")).get("version", "")
            )
        except json.JSONDecodeError:
            issues.append("package.json is not valid JSON")

    try:
        payload = state.read(entry.plugin_dir)
    except StateError as exc:
        payload = None
        issues.append(str(exc))

    if payload is None:
        issues.append("no state file: this bundle has never been synced by skill-sync")
    else:
        tag = str(payload.get("syncedTag") or "")
        normalized = normalize_tag(tag, entry.tag_prefix) or tag
        if version and normalized != version:
            issues.append(
                f"version/state mismatch: package.json says {version}, state says {tag}"
            )
        recorded = payload.get("skills") or {}
        skills_dir = entry.plugin_dir / "skills"
        actual = state.compute_tree_hashes(skills_dir) if skills_dir.is_dir() else {}
        for name in sorted(recorded):
            if name not in actual:
                issues.append(f"skill missing from disk: {name}")
            elif recorded[name].get("sha256") != actual[name]["sha256"]:
                issues.append(f"skill drift since the last sync: {name}")
        for name in sorted(set(actual) - set(recorded)):
            issues.append(f"skill present on disk but not in the state file: {name}")

    if profile_skills is not None:
        for name in entry.expected_skills:
            owner = profile_skills.get(name)
            if owner and owner != entry.package:
                issues.append(f"skill name {name} is also installed by {owner}")

    artifact = dist / f"{entry.package}-{version}.tgz" if version else None
    if artifact is None or not artifact.is_file():
        issues.append(f"tarball missing from {dist}: {entry.package}-{version}.tgz")
    else:
        skills_dir = entry.plugin_dir / "skills"
        shipped = _disk_skill_hashes(skills_dir) if skills_dir.is_dir() else {}
        packed = _tgz_skill_hashes(artifact)
        missing = sorted(set(shipped) - set(packed))
        extra = sorted(set(packed) - set(shipped))
        differing = sorted(
            relative
            for relative in set(shipped) & set(packed)
            if shipped[relative] != packed[relative]
        )
        if missing or extra or differing:
            issues.append(
                "tarball does not match the bundle on disk "
                f"(missing {len(missing)}, extra {len(extra)}, differing {len(differing)})"
            )
    return issues


def run_verify(
    entries: Sequence,
    workspace: Path | str,
    *,
    profile: Path | str | None = None,
    dist_dir: Path | str | None = None,
    stream=None,
) -> int:
    stream = stream if stream is not None else sys.stdout
    workspace = Path(workspace)
    dist = Path(dist_dir) if dist_dir is not None else default_dist_dir(workspace)
    profile_skills = _profile_skills(Path(profile)) if profile is not None else None

    lines = ["skill-sync verify", ""]
    exit_code = EXIT_OK

    for entry in entries:
        try:
            issues = _verify_entry(entry, dist, profile_skills)
        except Exception as exc:  # noqa: BLE001 - a broken artefact is a failed verification
            issues = [f"internal error while verifying: {type(exc).__name__}: {exc}"]

        if issues:
            if exit_code == EXIT_OK:
                exit_code = EXIT_VERIFY
            lines.append(f"  {entry.package}  FAILED")
            for issue in issues:
                lines.append(f"      ! {issue}")
        else:
            lines.append(f"  {entry.package}  ok")

    lines.append("")
    lines.append("summary: " + ("clean" if exit_code == EXIT_OK else "problems found"))
    print("\n".join(lines), file=stream)
    return exit_code


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
    """Entry point that maps failures onto the documented exit codes.

    At process level every path returns one of the seven codes (or the 2 argparse
    exits with), so an unmapped exception is reported as code 2 with its traceback
    rather than escaping as an interpreter failure. A programmatic caller must
    still handle the ``SystemExit`` that argument errors raise.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(parser, args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_SELFCHECK
    except RegistryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_SELFCHECK
    except Exception as exc:  # noqa: BLE001 - contract: never exit outside 0/2/3/4/5/6/7
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        print(
            "hint: reported as exit code 2 (self-check failed). If it looks like a workspace "
            "problem, note that --workspace is a global option and must come *before* the "
            "subcommand; otherwise report it with the traceback below.",
            file=sys.stderr,
        )
        traceback.print_exc()
        return EXIT_SELFCHECK


def _dispatch(parser, args) -> int:
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
            profile=args.profile or default_profile(),
            accept_structure_change=args.accept_structure_change,
            dry_run=args.dry_run,
            rebuild=args.rebuild,
            keep_staging=args.keep_staging,
            no_pack=args.no_pack,
        )

    if args.command == "verify":
        try:
            selected = _select(entries, args.packages, args.all, parser)
        except RegistryError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_SELFCHECK
        return run_verify(selected, workspace, profile=args.profile or default_profile())

    parser.error(f"command not implemented yet: {args.command}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())

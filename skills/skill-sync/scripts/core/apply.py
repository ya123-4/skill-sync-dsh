"""Landing a verified candidate: backup, atomic replace, rollback.

The staged tree is copied next to the bundle first (same volume), so the swap
itself is two directory moves rather than a long copy into a live path. The old
tree and manifest are moved to ``<plugin>/.skill-sync/backup/<UTC stamp>/`` before
anything changes; any failure -- including an interrupt, which is why the handler
catches ``BaseException`` -- restores them and removes the backup, leaving the
bundle byte-identical to how it was.

A successful landing also bumps the version in the manifest and in the bundle's
own documentation (`README.md`, `README.zh.md`, `THIRD_PARTY_NOTICES.md`), because
those files name the upstream version the bundle was built from and would
otherwise start lying on the first update.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

WORK_DIRNAME = ".skill-sync"
DOC_NAMES = ("README.md", "README.zh.md", "THIRD_PARTY_NOTICES.md")


class ApplyFailed(RuntimeError):
    """Landing failed; the bundle was rolled back (exit code 7)."""


def apply_candidate(
    plugin_dir: Path | str,
    staging_skills: Path | str,
    version: str,
    backup_root: Path | str | None = None,
    fail_after: int | None = None,
    fail_with: type[BaseException] = RuntimeError,
    dependency_bump: tuple[str, str] | None = None,
) -> Path:
    """Install ``staging_skills`` as ``<plugin>/skills`` and bump the version.

    ``dependency_bump`` is ``(name, version)`` for a bundle that vendors an npm
    package: the manifest's ``optionalDependencies[name]`` pin is what records
    which upstream release the shipped skills came from, so a landing that leaves
    it behind would advertise the old upstream forever.

    Returns the backup directory (kept on success, removed on failure).
    """
    plugin_dir = Path(plugin_dir)
    staging_skills = Path(staging_skills)
    manifest = plugin_dir / "package.json"

    if not manifest.is_file():
        raise ApplyFailed(f"{plugin_dir}: no package.json to update")
    if not staging_skills.is_dir():
        raise ApplyFailed(f"{staging_skills}: staged skills directory is missing")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work = plugin_dir / WORK_DIRNAME
    backup = (
        Path(backup_root) if backup_root is not None else _unique_dir(work / "backup", stamp)
    )
    incoming = work / f"incoming-{stamp}"
    skills_target = plugin_dir / "skills"

    backup.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    try:
        if skills_target.exists():
            shutil.move(str(skills_target), str(backup / "skills"))
        shutil.copy2(manifest, backup / "package.json")
        _backup_docs(plugin_dir, backup)
        _checkpoint(fail_after, 1, fail_with)

        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        shutil.copytree(staging_skills, incoming)
        shutil.move(str(incoming), str(skills_target))
        _checkpoint(fail_after, 2, fail_with)

        previous_version = _bump_manifest(manifest, version, dependency_bump)
        _bump_docs(plugin_dir, previous_version, version)
        _checkpoint(fail_after, 3, fail_with)
    except BaseException as exc:  # noqa: BLE001 - interrupts must roll back too
        _rollback(plugin_dir, backup, incoming)
        if isinstance(exc, Exception):
            raise ApplyFailed(f"{plugin_dir}: {exc}") from exc
        raise

    return backup


def _unique_dir(parent: Path, stem: str) -> Path:
    """A directory path that does not exist yet.

    The stamp has one-second resolution; a second landing within the same second
    used to move the old tree *into* the first backup (``backup/skills/skills``).
    """
    candidate = parent / stem
    index = 2
    while candidate.exists():
        candidate = parent / f"{stem}-{index}"
        index += 1
    return candidate


def _checkpoint(fail_after: int | None, step: int, fail_with: type[BaseException]) -> None:
    if fail_after is not None and step >= fail_after:
        raise fail_with(f"injected failure after step {step}")


def _bump_manifest(
    manifest: Path, version: str, dependency: tuple[str, str] | None = None
) -> str:
    """Rewrite ``version`` (and the description's version reference) in place.

    A textual replacement keeps the author's formatting; parsing and re-dumping
    the JSON would reflow the whole file. When ``dependency`` is given, the
    ``optionalDependencies`` pin for that package moves in the same pass. Returns
    the version that was replaced.
    """
    text = manifest.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApplyFailed(f"{manifest}: not valid JSON: {exc}") from exc

    previous = str(payload.get("version", ""))
    if not previous:
        raise ApplyFailed(f"{manifest}: no version field to replace")

    updated = text

    if dependency is not None:
        name, pin = dependency
        current = (payload.get("optionalDependencies") or {}).get(name)
        if not isinstance(current, str) or not current:
            raise ApplyFailed(f"{manifest}: no optionalDependencies[{name!r}] pin to update")
        if current != pin:
            marker = f'"{name}": "{current}"'
            if marker not in updated:
                raise ApplyFailed(
                    f"{manifest}: cannot rewrite optionalDependencies[{name!r}] {current} - the "
                    "manifest is not in the expected shape, and landing it would advertise a "
                    "stale upstream version"
                )
            updated = updated.replace(marker, f'"{name}": "{pin}"', 1)

    if previous != version:
        marker = f'"version": "{previous}"'
        if marker not in updated:
            raise ApplyFailed(
                f"{manifest}: cannot rewrite version {previous} - the manifest is not in the "
                "expected shape, and landing it would publish a stale version"
            )
        updated = updated.replace(marker, f'"version": "{version}"', 1)
        updated = re.sub(
            r'("description"\s*:\s*")([^"]*)"',
            lambda match: match.group(1) + match.group(2).replace(previous, version) + '"',
            updated,
            count=1,
        )

    if updated != text:
        manifest.write_bytes(updated.encode("utf-8"))
    return previous


def _bump_docs(plugin_dir: Path, previous: str, version: str) -> None:
    """Update the version references the bundle's own docs carry.

    Two phases on purpose: every doc is decoded and rewritten in memory first, so
    an undecodable or unreadable file aborts before any doc on disk changes.
    """
    if not previous or previous == version:
        return
    pending: list[tuple[Path, bytes]] = []
    for name in DOC_NAMES:
        path = plugin_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace(previous, version)
        if updated != text:
            pending.append((path, updated.encode("utf-8")))
    for path, payload in pending:
        path.write_bytes(payload)


def _backup_docs(plugin_dir: Path, backup: Path) -> None:
    """The docs name the version the bundle was built from, so they roll back too."""
    for name in DOC_NAMES:
        path = plugin_dir / name
        if path.is_file():
            shutil.copy2(path, backup / name)


def restore_backup(plugin_dir: Path | str, backup: Path | str, *, drop: bool = True) -> None:
    """Put a backed-up bundle back: skills, manifest and the docs that cite its version.

    Called both by the rollback here and by the caller when a *later* step (packing)
    fails, so a failed landing never leaves a doc advertising a version it rolled back.
    """
    plugin_dir = Path(plugin_dir)
    backup = Path(backup)
    skills_target = plugin_dir / "skills"
    if (backup / "skills").is_dir():
        if skills_target.exists():
            shutil.rmtree(skills_target, ignore_errors=True)
        shutil.move(str(backup / "skills"), str(skills_target))
    if (backup / "package.json").is_file():
        shutil.copy2(backup / "package.json", plugin_dir / "package.json")
    for name in DOC_NAMES:
        if (backup / name).is_file():
            shutil.copy2(backup / name, plugin_dir / name)
    if drop:
        shutil.rmtree(backup, ignore_errors=True)


def _rollback(plugin_dir: Path, backup: Path, incoming: Path) -> None:
    if incoming.exists():
        shutil.rmtree(incoming, ignore_errors=True)
    restore_backup(plugin_dir, backup, drop=True)

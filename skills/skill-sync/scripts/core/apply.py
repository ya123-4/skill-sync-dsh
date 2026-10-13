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
) -> Path:
    """Install ``staging_skills`` as ``<plugin>/skills`` and bump the version.

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
    backup = Path(backup_root) if backup_root is not None else work / "backup" / stamp
    incoming = work / f"incoming-{stamp}"
    skills_target = plugin_dir / "skills"

    backup.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    moved_old = False
    installed = False
    wrote_manifest = False
    try:
        if skills_target.exists():
            shutil.move(str(skills_target), str(backup / "skills"))
            moved_old = True
        shutil.copy2(manifest, backup / "package.json")
        _checkpoint(fail_after, 1, fail_with)

        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        shutil.copytree(staging_skills, incoming)
        shutil.move(str(incoming), str(skills_target))
        installed = True
        _checkpoint(fail_after, 2, fail_with)

        previous_version = _bump_manifest(manifest, version)
        _bump_docs(plugin_dir, previous_version, version)
        wrote_manifest = True
        _checkpoint(fail_after, 3, fail_with)
    except BaseException as exc:  # noqa: BLE001 - interrupts must roll back too
        _rollback(plugin_dir, backup, moved_old, wrote_manifest, installed, incoming)
        if isinstance(exc, Exception):
            raise ApplyFailed(f"{plugin_dir}: {exc}") from exc
        raise

    return backup


def _checkpoint(fail_after: int | None, step: int, fail_with: type[BaseException]) -> None:
    if fail_after is not None and step >= fail_after:
        raise fail_with(f"injected failure after step {step}")


def _bump_manifest(manifest: Path, version: str) -> str:
    """Rewrite ``version`` and the version reference inside ``description``.

    A textual replacement keeps the author's formatting; parsing and re-dumping
    the JSON would reflow the whole file. Returns the version that was replaced.
    """
    text = manifest.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ApplyFailed(f"{manifest}: not valid JSON: {exc}") from exc

    previous = str(payload.get("version", ""))
    if not previous:
        raise ApplyFailed(f"{manifest}: no version field to replace")

    updated = text.replace(f'"version": "{previous}"', f'"version": "{version}"', 1)
    if previous != version:
        updated = re.sub(
            r'("description"\s*:\s*")([^"]*)"',
            lambda match: match.group(1) + match.group(2).replace(previous, version) + '"',
            updated,
            count=1,
        )
    manifest.write_bytes(updated.encode("utf-8"))
    return previous


def _bump_docs(plugin_dir: Path, previous: str, version: str) -> None:
    """Update the version references the bundle's own docs carry."""
    if not previous or previous == version:
        return
    for name in DOC_NAMES:
        path = plugin_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        updated = text.replace(previous, version)
        if updated != text:
            path.write_bytes(updated.encode("utf-8"))


def _rollback(
    plugin_dir: Path,
    backup: Path,
    moved_old: bool,
    wrote_manifest: bool,
    installed: bool,
    incoming: Path,
) -> None:
    skills_target = plugin_dir / "skills"
    manifest = plugin_dir / "package.json"
    try:
        if incoming.exists():
            shutil.rmtree(incoming, ignore_errors=True)
        if installed and skills_target.exists():
            shutil.rmtree(skills_target, ignore_errors=True)
        if moved_old and (backup / "skills").is_dir() and not skills_target.exists():
            shutil.move(str(backup / "skills"), str(skills_target))
        if wrote_manifest and (backup / "package.json").is_file():
            shutil.copy2(backup / "package.json", manifest)
    finally:
        shutil.rmtree(backup, ignore_errors=True)

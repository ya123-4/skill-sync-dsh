"""The per-bundle sync record.

It lives inside the managed bundle (``<plugin>/.skill-sync-state.json``) and is
committed with it, so the audit trail and the rollback context travel with the
bundle instead of accumulating inside the manager.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

STATE_FILENAME = ".skill-sync-state.json"
STATE_SCHEMA = 1


class StateError(RuntimeError):
    """The state file is present but unusable (exit code 2)."""


def state_path(plugin_dir: Path | str) -> Path:
    return Path(plugin_dir) / STATE_FILENAME


def read(plugin_dir: Path | str) -> dict | None:
    path = state_path(plugin_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StateError(f"{path}: cannot read state: {exc}") from exc
    schema = payload.get("schema")
    if schema != STATE_SCHEMA:
        raise StateError(
            f"{path}: state schema {schema!r} is not supported (expected {STATE_SCHEMA})"
        )
    return payload


def write(plugin_dir: Path | str, payload: dict) -> Path:
    path = state_path(plugin_dir)
    document = {"schema": STATE_SCHEMA, **payload}
    path.write_bytes((json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return path


def compute_tree_hashes(skills_dir: Path | str) -> dict[str, dict]:
    """Per-skill digest of the produced tree: ``{skill: {files, sha256}}``."""
    skills_dir = Path(skills_dir)
    result: dict[str, dict] = {}
    for skill in sorted((path for path in skills_dir.iterdir() if path.is_dir()), key=lambda p: p.name):
        digest = hashlib.sha256()
        files = 0
        for path in sorted(skill.rglob("*")):
            if not path.is_file():
                continue
            digest.update(path.relative_to(skill).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            files += 1
        result[skill.name] = {"files": files, "sha256": digest.hexdigest()}
    return result


def manual_snapshot(plugin_dir: Path | str, manual_skills) -> dict[str, str]:
    """``{<skill>/<relative path>: sha256}`` for every file of a manual skill."""
    plugin_dir = Path(plugin_dir)
    snapshot: dict[str, str] = {}
    for name in manual_skills:
        skill = plugin_dir / "skills" / name
        if not skill.is_dir():
            raise StateError(f"{plugin_dir}: manual skill {name} is missing")
        for path in sorted(skill.rglob("*")):
            if not path.is_file():
                continue
            relative = f"{name}/{path.relative_to(skill).as_posix()}"
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot

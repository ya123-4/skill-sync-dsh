"""What a DSH profile has installed.

The five rebuilt bundles live in the workspace, so their version comes from their
own ``package.json``. Third-party plugins exist only in the profile, so their
version -- and whether they are installed at all -- has to be read from there.
"""
from __future__ import annotations

import json
from pathlib import Path


def _manifest(profile_dir: Path | str | None) -> dict:
    if profile_dir is None:
        return {}
    path = Path(profile_dir) / "package.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def installed_version(profile_dir: Path | str | None, package: str) -> str | None:
    """The installed version of ``package``, or None when it is absent or unreadable."""
    if profile_dir is None:
        return None
    path = Path(profile_dir) / "node_modules" / package / "package.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    version = payload.get("version") if isinstance(payload, dict) else None
    return str(version) if version else None


def is_installed(profile_dir: Path | str | None, package: str) -> bool:
    """Whether the profile's manifest lists ``package`` as a dependency.

    This is the signal for the install hint: a bundle that is already in the
    profile has to be replaced while DSH is not running, because the running host
    holds a handle on an installed bundle (pnpm then fails with ERR_PNPM_EPERM and
    empties the package directory first).
    """
    dependencies = _manifest(profile_dir).get("dependencies") or {}
    return package in dependencies

"""Packing a bundle with the system npm, and keeping the previous tarball.

The DSH runtime ships node without npm, so packing always goes through the
system npm and a cache directory inside the workspace (the sandbox blocks
``%LOCALAPPDATA%\\npm-cache``). An existing tarball for the same version is moved
aside into ``dist/.previous/`` before npm runs, so a rebuild never silently
destroys the artifact the profile currently points at.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

NPM_DEFAULT_PATH = r"C:\Program Files\nodejs\npm.cmd"

Runner = Callable[[list[str], dict], object]


class PackFailed(RuntimeError):
    """npm pack did not produce a usable tarball (exit code 6)."""


def pack(
    plugin_dir: Path | str,
    dist_dir: Path | str,
    npm_path: str = NPM_DEFAULT_PATH,
    runner: Runner | None = None,
    cache_dir: Path | str | None = None,
) -> Path:
    plugin_dir = Path(plugin_dir)
    dist_dir = Path(dist_dir)
    _strip_build_junk(plugin_dir)

    # A missing npm is deliberately *not* checked here: that check would also fire
    # for callers injecting their own runner, and `subprocess.run` already raises an
    # OSError that the handler below turns into PackFailed.
    try:
        return _pack(plugin_dir, dist_dir, str(npm_path), runner, cache_dir)
    except PackFailed:
        raise
    except OSError as exc:
        # A locked artifact, an unwritable dist directory or a missing npm must all
        # arrive as one failure type, so the caller can roll back and report code 6.
        raise PackFailed(f"packing {plugin_dir} failed: {type(exc).__name__}: {exc}") from exc


def _pack(
    plugin_dir: Path,
    dist_dir: Path,
    npm_path: str,
    runner: Runner | None,
    cache_dir: Path | str | None,
) -> Path:
    manifest_path = plugin_dir / "package.json"
    if not manifest_path.is_file():
        raise PackFailed(f"{plugin_dir}: no package.json to pack")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackFailed(f"{manifest_path}: not valid JSON: {exc}") from exc

    name = manifest.get("name")
    version = manifest.get("version")
    if not name or not version:
        raise PackFailed(f"{manifest_path}: needs both name and version")

    dist_dir.mkdir(parents=True, exist_ok=True)
    artifact = dist_dir / f"{name}-{version}.tgz"
    moved_previous: Path | None = None
    if artifact.is_file():
        previous = dist_dir / ".previous"
        previous.mkdir(parents=True, exist_ok=True)
        moved_previous = previous / artifact.name
        shutil.move(str(artifact), str(moved_previous))

    env = os.environ.copy()
    env["npm_config_cache"] = str(
        Path(cache_dir) if cache_dir is not None else dist_dir.parent / ".npm-cache"
    )
    argv = [str(npm_path), "pack", str(plugin_dir), "--pack-destination", str(dist_dir)]

    try:
        run = runner or _default_runner
        result = run(argv, env)
        returncode = getattr(result, "returncode", 1)
        if returncode != 0:
            detail = (getattr(result, "stderr", "") or "").strip()
            raise PackFailed(f"npm pack failed ({returncode}): {detail}")

        if not artifact.is_file() or artifact.stat().st_size == 0:
            raise PackFailed(f"npm pack produced no usable artifact: {artifact}")
    except BaseException:
        # The profile installs from this exact path, so a failed pack must leave a
        # *usable* artifact there: a partial or zero-byte file does not count.
        if moved_previous is not None:
            if artifact.exists():
                artifact.unlink()
            shutil.move(str(moved_previous), str(artifact))
        raise
    return artifact


def _default_runner(argv: list[str], env: dict):
    return subprocess.run(argv, env=env, capture_output=True, text=True, check=False)


def _strip_build_junk(plugin_dir: Path) -> None:
    """Bytecode compiled by a test run must never reach a release tarball.

    ``files: ["skills"]`` overrides ``.gitignore``, so anything a test run leaves
    in the tree would otherwise ship.
    """
    for cache in list(plugin_dir.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)

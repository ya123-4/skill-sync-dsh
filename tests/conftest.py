"""Shared test fixtures.

Two environment facts shape this file:

* The DSH sandbox denies writes outside the workspace, and pytest's own
  ``tmp_path`` is unusable here: its basetemp handling touches directories the
  sandbox refuses (``WinError 5``), even with ``--basetemp`` pointed inside the
  workspace. Tests therefore use the ``scratch`` fixture below, which creates and
  removes its own directory under ``tmpwork/skill-sync-scratch``.
* The product scripts are imported exactly as the CLI entry point imports them,
  so the script directory goes on ``sys.path``.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "skills" / "skill-sync" / "scripts"
WORKSPACE_ROOT = PKG_ROOT.parent.parent
SCRATCH_ROOT = WORKSPACE_ROOT / "tmpwork" / "skill-sync-scratch"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture()
def scratch() -> Path:
    """A writable, self-cleaning scratch directory inside the workspace."""
    from core.upstream import _remove_tree  # noqa: PLC0415 - needs sys.path above

    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    path = SCRATCH_ROOT / uuid.uuid4().hex[:12]
    path.mkdir()
    try:
        yield path
    finally:
        # A snapshot test can extract a tree whose descendants are past MAX_PATH (the
        # alibabacloud monorepo does, and this host has LongPathsEnabled=0). A plain
        # rmtree stops at the first such descendant and silently leaves the rest of
        # the tree behind, so the same extended-length path is used here.
        _remove_tree(path)

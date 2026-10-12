"""Shared test fixtures.

The DSH sandbox forbids writes outside the workspace, so tests that need scratch
space must run with ``--basetemp`` pointed inside the workspace (see
``tests/README.md``). ``tmp_path`` then resolves there safely.

This module also puts the product script directory on ``sys.path`` so tests
import the scripts exactly as the CLI entry point does.
"""
from __future__ import annotations

import sys
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PKG_ROOT / "skills" / "skill-sync" / "scripts"
WORKSPACE_ROOT = PKG_ROOT.parent.parent

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

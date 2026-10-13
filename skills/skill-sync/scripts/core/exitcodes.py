"""Process exit codes shared by every command.

The same seven codes are documented for users in ``skills/skill-sync/SKILL.md``
and asserted by ``tests/test_package_docs.py``; nothing outside this set may
reach the shell (see ``skill_sync.main``).
"""
from __future__ import annotations

EXIT_OK = 0
EXIT_SELFCHECK = 2
EXIT_UPSTREAM = 3
EXIT_STRUCTURE = 4
EXIT_VERIFY = 5
EXIT_PACK = 6
EXIT_APPLY = 7

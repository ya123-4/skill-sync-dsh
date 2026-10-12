"""Process exit codes shared by every command (design spec section 10)."""
from __future__ import annotations

EXIT_OK = 0
EXIT_SELFCHECK = 2
EXIT_UPSTREAM = 3
EXIT_STRUCTURE = 4
EXIT_VERIFY = 5
EXIT_PACK = 6
EXIT_APPLY = 7

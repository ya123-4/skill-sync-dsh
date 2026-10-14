"""Point one profile dependency at a different tarball -- and touch nothing else.

Why this exists: ``pnpm add`` re-resolves the whole ``^`` range, which can drag
unrelated dependencies to freshly published versions. DSH pins a
``minimumReleaseAge`` supply-chain policy, and pnpm writes before it verifies, so
that failure both leaves a policy-violating lockfile entry behind and makes every
later pnpm command fail the same way. ``pnpm install`` only resolves what changed,
so landing an update is a two-step: point the manifest here, then install.

This writes the profile's manifest and nothing else -- no install, no lockfile.

Usage: python point_profile.py <profile-dir> <package> <new-tarball-spec>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def point(profile: str | Path, package: str, new_spec: str) -> str:
    """Rewrite exactly one dependency specifier, preserving the file's formatting."""
    path = Path(profile) / "package.json"
    if not path.is_file():
        raise SystemExit(f"no package.json under {profile}")

    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} is not valid JSON: {exc}") from exc

    old_spec = (payload.get("dependencies") or {}).get(package)
    if old_spec is None:
        raise SystemExit(f"{package} is not a dependency of {path.parent}")
    if old_spec == new_spec:
        return f"{package} already points at {new_spec}"

    marker = f'"{package}": "{old_spec}"'
    if marker not in text:
        raise SystemExit(f"cannot find {marker!r} verbatim in {path}")

    # A backslash inside a JSON string is an escape sequence ("\U" is not even a
    # legal one), and every file: specifier in these manifests uses forward
    # slashes -- so normalise, then prove the result still parses before writing.
    new_spec = new_spec.replace("\\", "/")
    updated = text.replace(marker, f'"{package}": "{new_spec}"', 1)
    try:
        json.loads(updated)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"refusing to write {path}: the result would not be valid JSON: {exc}")
    path.write_text(updated, encoding="utf-8")
    json.loads(path.read_text(encoding="utf-8"))  # read back: never trust a silent write
    return f"{package}:\n  {old_spec}\n  -> {new_spec}"


def main(argv: list[str]) -> None:
    if len(argv) != 4:
        raise SystemExit("usage: point_profile.py <profile-dir> <package> <new-tarball-spec>")
    print(point(argv[1], argv[2], argv[3]))


if __name__ == "__main__":
    main(sys.argv)

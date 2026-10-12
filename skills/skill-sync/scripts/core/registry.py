"""Registry of the skill bundles this plugin manages.

The registry is data, not code: one entry per managed bundle, holding where its
upstream lives, how its tags are shaped, which recipe rebuilds it, and which
skills it is expected to ship. Every expectation here is checked against the
installed bundle by ``tests/test_registry.py``, so the file cannot silently
drift away from reality.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PLUGIN_BUILD_DIRNAME = "dsh-plugin-build"
REGISTRY_SCHEMA = 1
_REQUIRED_ENTRY_KEYS = ("package", "pluginDir", "upstream", "recipe", "expectedSkills")
_REQUIRED_UPSTREAM_KEYS = ("repo", "tagPrefix", "skillsRoot", "stripPrefix")


class RegistryError(RuntimeError):
    """The registry or the workspace root is unusable (exit code 2)."""


@dataclass(frozen=True)
class Entry:
    package: str
    plugin_dir: Path
    repo: str
    tag_prefix: str
    skills_root: str
    strip_prefix: str
    recipe: str
    expected_skills: tuple[str, ...]
    manual_skills: tuple[str, ...]
    commit_pinned: bool
    license: str
    notes: str

    def upstream_skill_name(self, local_name: str) -> str:
        """Map a produced skill directory name back to its upstream name."""
        if self.strip_prefix and local_name.startswith(self.strip_prefix):
            return local_name[len(self.strip_prefix):]
        return local_name


def registry_file() -> Path:
    """Path of the bundled registry (``skills/skill-sync/data/registry.json``)."""
    return Path(__file__).resolve().parents[2] / "data" / "registry.json"


def resolve_workspace(cwd: Path | str, override: str | None) -> Path:
    """Locate the workspace root: an explicit override, else the nearest ancestor
    containing ``dsh-plugin-build/``."""
    if override:
        candidate = Path(override).expanduser().resolve()
        if not (candidate / PLUGIN_BUILD_DIRNAME).is_dir():
            raise RegistryError(f"workspace override has no {PLUGIN_BUILD_DIRNAME}/: {candidate}")
        return candidate
    start = Path(cwd).expanduser().resolve()
    for candidate in (start, *start.parents):
        if (candidate / PLUGIN_BUILD_DIRNAME).is_dir():
            return candidate
    raise RegistryError(f"no {PLUGIN_BUILD_DIRNAME}/ found from {start}; pass --workspace DIR")


def load(workspace: Path | str) -> list[Entry]:
    """Load and validate every registry entry against ``workspace``."""
    workspace = Path(workspace)
    path = registry_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RegistryError(f"cannot read registry {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RegistryError(f"registry {path} is not valid JSON: {exc}") from exc

    if raw.get("schema") != REGISTRY_SCHEMA:
        raise RegistryError(
            f"registry schema {raw.get('schema')!r} is not supported (expected {REGISTRY_SCHEMA})"
        )

    entries: list[Entry] = []
    seen: set[str] = set()
    for item in raw.get("entries", []):
        entry = _build_entry(item, workspace)
        if entry.package in seen:
            raise RegistryError(f"duplicate package in registry: {entry.package}")
        seen.add(entry.package)
        entries.append(entry)
    if not entries:
        raise RegistryError("registry has no entries")
    return entries


def select(entries: list[Entry], names: list[str]) -> list[Entry]:
    """Pick entries by package name, preserving the requested order."""
    index = {entry.package: entry for entry in entries}
    missing = [name for name in names if name not in index]
    if missing:
        raise RegistryError("unknown package(s): " + ", ".join(missing))
    return [index[name] for name in names]


def _build_entry(item: dict, workspace: Path) -> Entry:
    label = item.get("package", "<unknown>")
    for key in _REQUIRED_ENTRY_KEYS:
        if key not in item:
            raise RegistryError(f"registry entry {label} is missing {key!r}")
    upstream = item["upstream"]
    for key in _REQUIRED_UPSTREAM_KEYS:
        if key not in upstream:
            raise RegistryError(f"{label}: upstream is missing {key!r}")

    plugin_dir = (workspace / item["pluginDir"]).resolve()
    if not (plugin_dir / "package.json").is_file():
        raise RegistryError(f"{label}: no package.json under {plugin_dir}")

    expected = tuple(item["expectedSkills"])
    if not expected:
        raise RegistryError(f"{label}: expectedSkills is empty")

    return Entry(
        package=item["package"],
        plugin_dir=plugin_dir,
        repo=upstream["repo"],
        tag_prefix=upstream["tagPrefix"],
        skills_root=upstream["skillsRoot"],
        strip_prefix=upstream["stripPrefix"],
        recipe=item["recipe"],
        expected_skills=expected,
        manual_skills=tuple(item.get("manualSkills", [])),
        commit_pinned=bool(item.get("commitPinned", False)),
        license=item.get("license", "unknown"),
        notes=item.get("notes", ""),
    )

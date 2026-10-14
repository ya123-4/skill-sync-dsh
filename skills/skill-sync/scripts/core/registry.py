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
KINDS = ("github-tags", "github-releases", "github-commits", "npm")
MODES = ("rebuild", "snapshot", "manual")
_REQUIRED_UPSTREAM_KEYS = ("repo", "tagPrefix", "skillsRoot", "stripPrefix")


class RegistryError(RuntimeError):
    """The registry or the workspace root is unusable (exit code 2)."""


@dataclass(frozen=True)
class Entry:
    package: str
    plugin_dir: Path | None
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
    baseline_commit: str | None = None
    # follow-型条目：上游在哪里查（kind）与怎么更新（mode）。
    # 省略即现有的 rebuild 型，行为完全不变。
    kind: str = "github-tags"
    mode: str = "rebuild"
    source: str = ""
    asset: str = ""

    @property
    def is_rebuild(self) -> bool:
        return self.mode == "rebuild"

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
    if "package" not in item:
        raise RegistryError("registry entry is missing 'package'")
    label = item["package"]
    kind = item.get("kind", "github-tags")
    mode = item.get("mode", "rebuild")
    if kind not in KINDS:
        raise RegistryError(f"{label}: unknown kind {kind!r} (expected one of {', '.join(KINDS)})")
    if mode not in MODES:
        raise RegistryError(f"{label}: unknown mode {mode!r} (expected one of {', '.join(MODES)})")

    upstream = item.get("upstream") or {}

    # rebuild 型：上游是 skill 仓库，需要完整的 upstream 描述与配方。
    if mode == "rebuild":
        for key in _REQUIRED_UPSTREAM_KEYS:
            if key not in upstream:
                raise RegistryError(f"{label}: upstream is missing {key!r}")
        expected = tuple(item.get("expectedSkills") or ())
        if not expected:
            raise RegistryError(f"{label}: expectedSkills is empty")

    # rebuild 与 snapshot 都要动本地目录；manual 型不碰文件系统。
    plugin_dir: Path | None = None
    if mode in ("rebuild", "snapshot"):
        if "pluginDir" not in item:
            raise RegistryError(f"{label}: mode {mode!r} needs pluginDir")
        plugin_dir = (workspace / item["pluginDir"]).resolve()
        if not (plugin_dir / "package.json").is_file():
            raise RegistryError(f"{label}: no package.json under {plugin_dir}")

    # kind 与 mode 必须互相兼容：registry 不接受"能加载、但一跑就崩"的组合。
    if mode == "rebuild" and kind != "github-tags":
        raise RegistryError(f"{label}: mode 'rebuild' needs kind 'github-tags', got {kind!r}")
    if mode == "snapshot":
        if kind != "github-releases":
            raise RegistryError(
                f"{label}: mode 'snapshot' needs kind 'github-releases', got {kind!r}"
            )
        if not item.get("asset"):
            raise RegistryError(f"{label}: mode 'snapshot' needs 'asset' (the release asset name)")
        snapshot_targets = tuple(item.get("expectedSkills") or ())
        if len(snapshot_targets) != 1:
            raise RegistryError(
                f"{label}: mode 'snapshot' needs exactly one expectedSkills entry -- the "
                f"directory the asset replaces -- got {len(snapshot_targets)}"
            )
    if kind == "github-commits":
        raise RegistryError(
            f"{label}: kind 'github-commits' is declared in the spec but not implemented; "
            "use 'github-tags' or 'github-releases'"
        )

    # follow 型需要一个可查询的上游标识：npm 包名，或 owner/repo（kind 决定怎么查）。
    source = item.get("source") or ""
    if kind == "npm" and not source:
        raise RegistryError(f"{label}: kind 'npm' needs 'source' (the package name)")
    if kind != "npm" and not (source or upstream.get("repo")):
        raise RegistryError(f"{label}: kind {kind!r} needs 'source' or upstream.repo")

    return Entry(
        package=item["package"],
        plugin_dir=plugin_dir,
        repo=upstream.get("repo", "") or (source if kind != "npm" else ""),
        tag_prefix=upstream.get("tagPrefix", ""),
        skills_root=upstream.get("skillsRoot", ""),
        strip_prefix=upstream.get("stripPrefix", ""),
        recipe=item.get("recipe", ""),
        expected_skills=tuple(item.get("expectedSkills") or ()),
        manual_skills=tuple(item.get("manualSkills", [])),
        commit_pinned=bool(item.get("commitPinned", False)),
        license=item.get("license", "unknown"),
        notes=item.get("notes", ""),
        baseline_commit=item.get("baselineCommit"),
        kind=kind,
        mode=mode,
        source=source,
        asset=item.get("asset", ""),
    )

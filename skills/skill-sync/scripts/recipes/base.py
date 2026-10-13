"""Recipe contract: an upstream checkout in, a staging ``skills/`` tree out.

A recipe reads only ``upstream_dir`` and writes only ``staging_dir``; installing
the result into the bundle is the core's job (backup, atomic replace, rollback).
Everything a recipe does that *deviates* from upstream is either a mechanical
rule named in the docs (a prefix, a flattening) or an overlay file applied
through the primitives below -- never free-form prose hidden in a recipe body.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from core.registry import Entry


class AnchorNotFound(ValueError):
    """An overlay anchor is missing, i.e. upstream text changed shape."""


class StructureChanged(RuntimeError):
    """Upstream no longer has the shape this recipe was written for (exit code 4)."""


@dataclass(frozen=True)
class RecipeContext:
    upstream_dir: Path
    upstream_ref: str
    plugin_dir: Path
    staging_dir: Path
    entry: Entry

    def __post_init__(self) -> None:
        if not Path(self.upstream_dir).is_dir():
            raise ValueError(f"upstream_dir does not exist: {self.upstream_dir}")
        staging = Path(self.staging_dir).resolve()
        plugin = Path(self.plugin_dir).resolve()
        if staging == plugin or plugin in staging.parents:
            raise ValueError(f"staging_dir must live outside plugin_dir: {staging}")


@dataclass
class RecipeResult:
    skills: list[str] = field(default_factory=list)
    manual_skills: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def insert_after(text: str, anchor: str, block: str) -> str:
    """Insert ``block`` directly after the first occurrence of ``anchor``."""
    index = text.find(anchor)
    if index < 0:
        raise AnchorNotFound(f"anchor not found: {anchor!r}")
    end = index + len(anchor)
    return text[:end] + block + text[end:]


def replace_between(text: str, start: str, end: str, block: str) -> str:
    """Replace the inclusive ``start`` .. ``end`` span with ``block``."""
    start_index = text.find(start)
    if start_index < 0:
        raise AnchorNotFound(f"start anchor not found: {start!r}")
    end_index = text.find(end, start_index + len(start))
    if end_index < 0:
        raise AnchorNotFound(f"end anchor not found after start: {end!r}")
    end_index += len(end)
    return text[:start_index] + block + text[end_index:]


def write_tree(source: Path, target: Path) -> list[str]:
    """Copy a directory tree byte-for-byte; return the relative paths written."""
    written: list[str] = []
    source = Path(source)
    target = Path(target)
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        destination = target / relative
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(path.read_bytes())
        written.append(relative.as_posix())
    return written

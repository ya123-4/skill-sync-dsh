"""mattpocock: the plugin.json skill set, flattened and re-worded for DSH.

Upstream ships skills in groups (``skills/engineering/<name>/``,
``skills/productivity/<name>/``) alongside ``in-progress/`` (documented upstream
as excluded from the plugin) and ``misc/`` (frozen). ``.claude-plugin/plugin.json``
is the canonical release set, so that file -- not a directory scan -- decides what
ships.

DSH discovers skills only one level deep (``<root>/<name>/SKILL.md``), so each
shipped skill is flattened to ``skills/<name>/`` while its sibling resources come
along. Two mechanical prose normalizations keep the upstream Claude Code wording
honest in this harness:

1. ``Skill tool`` -> `` `skill` tool ``   (DSH's skill-loading tool is named ``skill``)
2. `` `/name` `` -> `` `name` ``          (plugin slash commands are plain skills here)
"""
from __future__ import annotations

import json
import re

from recipes.base import RecipeContext, RecipeResult, StructureChanged, write_tree

PLUGIN_MANIFEST = ".claude-plugin/plugin.json"


def normalize_prose(text: str, names: set[str]) -> str:
    """Apply the two documented normalizations; no other prose is touched."""
    normalized = text.replace("Skill tool", "`skill` tool")
    if not names:
        return normalized
    slash_ref = re.compile(r"`/(" + "|".join(sorted(map(re.escape, names))) + r")`")
    return slash_ref.sub(lambda match: f"`{match.group(1)}`", normalized)


def assemble(context: RecipeContext) -> RecipeResult:
    entry = context.entry
    manifest_path = context.upstream_dir / PLUGIN_MANIFEST
    if not manifest_path.is_file():
        raise StructureChanged(f"{entry.package}: upstream has no {PLUGIN_MANIFEST}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StructureChanged(f"{entry.package}: {PLUGIN_MANIFEST} is not valid JSON: {exc}") from exc

    listed = [str(path).removeprefix("./") for path in manifest.get("skills", [])]
    if not listed:
        raise StructureChanged(f"{entry.package}: {PLUGIN_MANIFEST} lists no skills")

    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    for relative in listed:
        source = context.upstream_dir / relative
        if not (source / "SKILL.md").is_file():
            raise StructureChanged(f"{entry.package}: {relative} has no SKILL.md")
        name = source.name
        if name in skills:
            raise StructureChanged(f"{entry.package}: duplicate skill name {name}")
        write_tree(source, staging_skills / name)
        skills.append(name)

    for name in skills:
        declared = _declared_name((staging_skills / name / "SKILL.md").read_text(encoding="utf-8"))
        if declared != name:
            raise StructureChanged(
                f"{entry.package}: {name}/SKILL.md declares name: {declared!r}"
            )

    names = set(skills)
    for path in sorted(staging_skills.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        normalized = normalize_prose(text, names)
        if normalized != text:
            path.write_bytes(normalized.encode("utf-8"))

    return RecipeResult(skills=skills, manual_skills=[], notes=[])


def _declared_name(text: str) -> str | None:
    """The frontmatter ``name:`` of a SKILL.md, if it has one in the first lines."""
    for line in text.splitlines()[:12]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    return None

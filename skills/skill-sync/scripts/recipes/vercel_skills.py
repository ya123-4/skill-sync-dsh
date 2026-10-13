"""vercel-skills: two upstream copies plus one hand-distilled manual skill.

Upstream is a CLI repository, not a skill collection, so the bundle takes:

* ``skills/find-skills/`` -> ``skills/find-skills/`` (byte-identical upstream)
* ``.factory/workflows/pr-labeling/skills/pr-labeling/`` -> ``skills/pr-labeling/``
  (byte-identical upstream, relocated one level deep so DSH discovers it)

``skills-cli`` has no upstream counterpart: it was written for this bundle by
distilling the upstream README. It is copied from the installed bundle unchanged
and reported as ``manual`` -- an upstream change can therefore never be applied
to it automatically, only noticed and distilled by hand.
"""
from __future__ import annotations

from recipes.base import RecipeContext, RecipeResult, StructureChanged, write_tree

UPSTREAM_SKILLS = {
    "find-skills": "skills/find-skills",
    "pr-labeling": ".factory/workflows/pr-labeling/skills/pr-labeling",
}


def assemble(context: RecipeContext) -> RecipeResult:
    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []

    for name, relative in UPSTREAM_SKILLS.items():
        source = context.upstream_dir / relative
        if not (source / "SKILL.md").is_file():
            raise StructureChanged(
                f"{context.entry.package}: upstream {relative}/SKILL.md is missing"
            )
        write_tree(source, staging_skills / name)
        skills.append(name)

    manual = list(context.entry.manual_skills)
    for name in manual:
        source = context.plugin_dir / "skills" / name
        if not (source / "SKILL.md").is_file():
            raise StructureChanged(
                f"{context.entry.package}: installed manual skill {name} is missing"
            )
        write_tree(source, staging_skills / name)
        skills.append(name)

    return RecipeResult(skills=skills, manual_skills=manual, notes=[])

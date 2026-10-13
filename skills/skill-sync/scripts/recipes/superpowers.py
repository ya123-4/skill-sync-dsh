"""superpowers: upstream ``skills/`` ships byte-for-byte, nothing more.

obra/superpowers keeps each skill under ``skills/<name>/`` together with its
support files (scripts, references, prompts, templates). The bundle copies that
tree whole. Upstream's hooks and IDE plugin configuration live outside
``skills/`` and were never part of the bundle, so there is nothing to exclude.
"""
from __future__ import annotations

from recipes.base import RecipeContext, RecipeResult, StructureChanged, write_tree


def assemble(context: RecipeContext) -> RecipeResult:
    upstream_skills = context.upstream_dir / context.entry.skills_root
    if not upstream_skills.is_dir():
        raise StructureChanged(
            f"{context.entry.package}: upstream has no {context.entry.skills_root}/ "
            f"under {context.upstream_dir}"
        )

    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    for skill_dir in sorted(path for path in upstream_skills.iterdir() if path.is_dir()):
        write_tree(skill_dir, staging_skills / skill_dir.name)
        skills.append(skill_dir.name)

    if not skills:
        raise StructureChanged(f"{context.entry.package}: upstream {context.entry.skills_root}/ is empty")

    return RecipeResult(skills=skills, manual_skills=[], notes=[])

"""root_skill: one upstream ``SKILL.md`` at the repository root becomes one skill.

A skill repository whose prompt file *is* the repository root has this shape
(``blader/humanizer`` and ``op7418/Humanizer-zh`` both do). The recipe copies that
one file byte for byte into ``skills/<name>/SKILL.md`` -- no rewrite, no overlay --
so "update" can never mean anything but "adopt upstream's bytes".

It refuses to guess. An upstream whose frontmatter ``name`` no longer matches the
skill this bundle ships is a structure change (exit code 4), reported for a human,
rather than a bundle that only ``verify`` would catch. One recipe serves both
upstreams because the rule is identical for both; if they ever need different
treatment, that is the moment to split it in two.
"""
from __future__ import annotations

from core.verify import frontmatter_name
from recipes.base import RecipeContext, RecipeResult, StructureChanged


def assemble(context: RecipeContext) -> RecipeResult:
    if not context.entry.expected_skills:
        raise StructureChanged(
            f"{context.entry.package}: the registry declares no expected skill to build"
        )
    name = context.entry.expected_skills[0]

    source = context.upstream_dir / context.entry.skills_root / "SKILL.md"
    if not source.is_file():
        raise StructureChanged(
            f"{context.entry.package}: upstream {context.entry.skills_root}/SKILL.md is missing"
        )

    declared = frontmatter_name(source.read_text(encoding="utf-8"))
    if declared != name:
        raise StructureChanged(
            f"{context.entry.package}: upstream SKILL.md declares name {declared!r}, "
            f"expected {name!r}"
        )

    target = context.staging_dir / "skills" / name
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_bytes(source.read_bytes())

    return RecipeResult(skills=[name], manual_skills=[], notes=[])

"""rea-dsh-plugin: the REA agent skill rebuilt from the ``rea-agents`` npm package.

Upstream REA ships its agent skill twice: a source copy under the repository's
``.agents/skills/`` whose reference links point at repository-relative paths, and
the *published* copy inside the npm package under ``skills/`` whose links were
rewritten to absolute GitHub URLs. The bundle vendors the published copy, so the
npm registry tarball -- the version pinned in ``optionalDependencies`` -- is the
upstream here. Syncing from the repository tree would look plausible and would
silently break every reference in the shipped skill, which is why this entry is
``kind: npm`` and not ``github-tags``.

Two local adaptations turn that package into the shipped bundle:

1. ``reverse-engineer-anything`` is copied byte-for-byte, then its
   "Connect only when needed" section is replaced with the DSH wording stored
   under ``overlays/rea/connect-section.txt``: upstream's per-client setup steps
   do not apply once the plugin registers the MCP server and the skill itself;
2. ``rea-dsh`` is this plugin's own skill with no upstream counterpart. The
   landing replaces ``skills/`` wholesale, so the recipe carries the installed
   copy into the candidate unchanged and names it in ``manualSkills`` -- which
   makes ``core.verify`` compare it against the installed bytes.

A missing skill directory raises ``StructureChanged`` and a moved section raises
``AnchorNotFound``; neither is patched over, because both mean upstream moved.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from recipes.base import (
    AnchorNotFound,
    RecipeContext,
    RecipeResult,
    StructureChanged,
    replace_between,
    write_tree,
)

OVERLAY_DIR = Path(__file__).resolve().parent / "overlays" / "rea"

CONNECT_START = "## Connect only when needed\n"
ROUTE_START = "## Route the target first\n"


def overlay(name: str) -> str:
    return (OVERLAY_DIR / name).read_text(encoding="utf-8")


def assemble(context: RecipeContext) -> RecipeResult:
    entry = context.entry
    upstream_root = context.upstream_dir / entry.skills_root
    if not upstream_root.is_dir():
        raise StructureChanged(
            f"{entry.package}: upstream has no {entry.skills_root}/ under {context.upstream_dir}"
        )

    # Skills the plugin owns (registry manualSkills) have no upstream counterpart, so
    # they are carried over from the installed bundle; only the rest is looked for in
    # the package's skills/ tree.
    manual = tuple(entry.manual_skills)
    upstream_expected = {
        entry.upstream_skill_name(name) for name in entry.expected_skills if name not in manual
    }
    available = {path.name for path in upstream_root.iterdir() if path.is_dir()}
    missing = sorted(upstream_expected - available)
    if missing:
        raise StructureChanged(
            f"{entry.package}: upstream {entry.skills_root}/ is missing {missing}"
        )

    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    for bare in sorted(upstream_expected):
        local_name = f"{entry.strip_prefix}{bare}"
        target = staging_skills / local_name
        write_tree(upstream_root / bare, target)
        _apply_connect_overlay(target / "SKILL.md", context.upstream_ref)
        skills.append(local_name)

    carried = _carry_manual_skills(context, staging_skills)
    return RecipeResult(
        skills=sorted(skills + carried),
        manual_skills=carried,
        notes=[],
    )


def _apply_connect_overlay(skill_md: Path, upstream_ref: str) -> None:
    """Replace the client-setup section with the harness wording for this bundle.

    The overlay names the upstream release as ``{version}`` so the commands it
    quotes follow whatever release the rebuild pinned; a literal version there
    would advertise the release the overlay was written against, forever.
    """
    if not skill_md.is_file():
        raise StructureChanged(f"{skill_md.parent.name}: upstream SKILL.md is missing")
    text = skill_md.read_text(encoding="utf-8")
    if CONNECT_START not in text or ROUTE_START not in text:
        raise AnchorNotFound(
            f"{skill_md.parent.name}: expected the 'Connect only when needed' section "
            "ahead of 'Route the target first'"
        )
    section = overlay("connect-section.txt").replace("{version}", upstream_ref)
    updated = replace_between(text, CONNECT_START, ROUTE_START, section)
    skill_md.write_bytes(updated.encode("utf-8"))


def _carry_manual_skills(context: RecipeContext, staging_skills: Path) -> list[str]:
    """Copy the plugin's own skills into the candidate so a landing cannot drop them."""
    carried: list[str] = []
    for name in context.entry.manual_skills:
        source = context.plugin_dir / "skills" / name
        if not source.is_dir():
            raise StructureChanged(
                f"{context.entry.package}: manual skill {name} is missing from {context.plugin_dir}"
            )
        target = staging_skills / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        carried.append(name)
    return carried

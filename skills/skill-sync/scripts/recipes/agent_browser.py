"""agent-browser: nine prefixed skills rebuilt from upstream ``skill-data/``.

Upstream keeps skill bodies in ``skill-data/<bare-name>/``; ``skills/agent-browser/SKILL.md``
is an intentionally thin discovery stub for other harnesses, so it is never
bundled.

Three mechanical rules plus five versioned overlay blocks turn upstream into the
shipped bundle:

1. every upstream directory is renamed ``<bare>`` -> ``agent-browser-<bare>``,
   including the frontmatter ``name:``;
2. the ``skills/agent-browser/`` stub is not copied;
3. ``core/SKILL.md`` and ``core/references/proxy-support.md`` receive the local
   edits stored under ``overlays/agent-browser/``.

Anchors below are upstream text. A missing anchor raises ``AnchorNotFound`` and a
missing skill directory raises ``StructureChanged``; neither is ever patched over
silently, because both mean upstream moved.
"""
from __future__ import annotations

from pathlib import Path

from recipes.base import (
    AnchorNotFound,
    RecipeContext,
    RecipeResult,
    StructureChanged,
    insert_after,
    insert_after_once,
    replace_between,
    replace_once,
    replace_once_or_keep,
    write_tree,
)

OVERLAY_DIR = Path(__file__).resolve().parent / "overlays" / "agent-browser"

WINDOWS_NOTES_ANCHOR = "see [When to load another skill](#when-to-load-another-skill).\n\n"
ENGINE_FLAG_ANCHOR = "--headed                # show the window (default is headless)\n"
ENGINE_NOTE_ANCHOR = (
    "--namespace <name>      # isolate daemon sockets and restore-state directories\n```\n"
)
SIBLING_SECTION_START = "## When to load another skill\n"
SIBLING_SECTION_END = (
    "- **AWS Bedrock AgentCore cloud browser**: `agent-browser skills get agentcore`\n"
)
PROXY_LINE = "Skip proxy for specific domains using `--proxy-bypass` or `NO_PROXY`:"


def overlay(name: str) -> str:
    return (OVERLAY_DIR / name).read_text(encoding="utf-8")


def assemble(context: RecipeContext) -> RecipeResult:
    entry = context.entry
    upstream_root = context.upstream_dir / entry.skills_root
    if not upstream_root.is_dir():
        raise StructureChanged(
            f"{entry.package}: upstream has no {entry.skills_root}/ under {context.upstream_dir}"
        )

    expected = {entry.upstream_skill_name(name) for name in entry.expected_skills}
    available = {path.name for path in upstream_root.iterdir() if path.is_dir()}
    missing = sorted(expected - available)
    if missing:
        raise StructureChanged(
            f"{entry.package}: upstream {entry.skills_root}/ is missing {missing}"
        )

    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    for bare in sorted(expected):
        local_name = f"{entry.strip_prefix}{bare}"
        target = staging_skills / local_name
        write_tree(upstream_root / bare, target)
        _rewrite_frontmatter_name(target / "SKILL.md", bare, local_name)
        skills.append(local_name)

    _apply_core_overlays(staging_skills / f"{entry.strip_prefix}core")
    return RecipeResult(skills=skills, manual_skills=[], notes=[])


def _rewrite_frontmatter_name(skill_md: Path, bare: str, local_name: str) -> None:
    text = skill_md.read_text(encoding="utf-8")
    updated = replace_once(text, f"name: {bare}\n", f"name: {local_name}\n")
    skill_md.write_bytes(updated.encode("utf-8"))


def _apply_core_overlays(core_dir: Path) -> None:
    skill_md = core_dir / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    text = insert_after(text, WINDOWS_NOTES_ANCHOR, overlay("core-windows-notes.txt"))
    text = insert_after(text, ENGINE_FLAG_ANCHOR, overlay("core-engine-flag.txt"))
    text = insert_after(text, ENGINE_NOTE_ANCHOR, overlay("core-engine-note.txt"))
    text = replace_between(
        text,
        SIBLING_SECTION_START,
        SIBLING_SECTION_END,
        overlay("core-sibling-skills.txt"),
    )
    skill_md.write_bytes(text.encode("utf-8"))

    proxy = core_dir / "references" / "proxy-support.md"
    if not proxy.is_file():
        raise StructureChanged(f"{core_dir.name}: upstream reference proxy-support.md is missing")
    proxy_text = proxy.read_text(encoding="utf-8")
    try:
        replacement = overlay("proxy-obscura-line.txt")
        proxy_text = replace_once_or_keep(proxy_text, PROXY_LINE, replacement)
        proxy_text = insert_after_once(
            proxy_text, replacement + "\n\n", overlay("proxy-chrome-example.txt")
        )
    except AnchorNotFound as exc:
        raise StructureChanged(f"{core_dir.name}: proxy-support.md no longer matches: {exc}") from exc
    proxy.write_bytes(proxy_text.encode("utf-8"))

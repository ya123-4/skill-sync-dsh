"""open-design: 45 curated skills assembled from a pinned upstream snapshot.

Upstream is a 3.5 GB product repository, so only a curated slice is bundled and it
is fetched per file from jsDelivr at a pinned commit (see
``tools/fetch_open_design_sources.py``). ``ctx.upstream_dir`` is that material,
laid out with upstream's own paths:

    craft/*.md                              -> od-craft/craft/
    design-systems/<brand>/<kept files>     -> od-design-systems/systems/<brand>/
    design-templates/<name>/**              -> <name>/
    plugins/_official/scenarios/<name>/**   -> <name>/
    plugins/_official/atoms/<name>/**       -> od-<name>/

Four documented adaptations turn that material into the shipped bundle:

1. atoms are renamed ``od-<name>``, because upstream's bare ``handoff`` would
   collide with ``mattpocock-skills-dsh``'s ``handoff``;
2. every skill body is prefixed with a harness note: OpenDesign is an app that
   injects the active design system into the model's context, and DSH does not;
3. four scenario skills ship without frontmatter upstream, so ``name`` and
   ``description`` are synthesized from their sibling ``open-design.json``;
4. the two index skills (``od-craft``, ``od-design-systems``) are authored here --
   they are the entry points into the craft references and the design-system
   library.
"""
from __future__ import annotations

import json
from pathlib import Path

from recipes.base import RecipeContext, RecipeResult, StructureChanged, write_tree

TEMPLATES = [
    "web-prototype",
    "saas-landing",
    "pricing-page",
    "dashboard",
    "docs-page",
    "blog-post",
    "mobile-app",
    "mobile-onboarding",
    "social-carousel",
    "image-poster",
    "email-marketing",
    "weekly-update",
    "meeting-notes",
    "pm-spec",
    "simple-deck",
    "critique",
]

SCENARIOS = [
    "od-default",
    "od-design-refine",
    "od-new-generation",
    "od-code-migration",
    "od-figma-migration",
    "od-react-export",
    "od-nextjs-export",
    "od-vue-export",
    "od-media-generation",
    "od-tune-collab",
    "od-plugin-authoring",
    "od-share-to-community",
    "od-web-effect-extractor",
    "od-next-strategy",
]

ATOMS = [
    "build-test",
    "code-import",
    "critique-theater",
    "design-extract",
    "diff-review",
    "direction-picker",
    "discovery-question-form",
    "figma-extract",
    "handoff",
    "patch-edit",
    "rewrite-plan",
    "todo-write",
    "token-map",
]

DESIGN_SYSTEMS = [
    "linear-app",
    "minimal",
    "editorial",
    "claude",
    "openai",
    "github",
    "notion",
    "figma",
    "apple",
    "material",
    "ant",
    "ibm",
    "nvidia",
    "airbnb",
    "discord",
    "framer",
    "cursor",
    "vercel",
    "stripe",
    "spotify",
]

# Per-brand files kept from a 24-file upstream directory (the rest are OpenDesign
# app preview artifacts: components.html, system/**, preview/**).
SYSTEM_KEEP = (
    "DESIGN.md",
    "USAGE.md",
    "tokens.css",
    "tailwind-v4.css",
    "design-tokens.json",
    "manifest.json",
)

ADAPT_NOTE = (
    "> **DeepSeek Harness note.** This workflow was written for the OpenDesign app. A design system\n"
    "> is *not* injected into your context here: load `od-design-systems` to pick a brand\n"
    "> `DESIGN.md` (or skip it and state the tokens you assume). The `od:` / `triggers:`\n"
    "> frontmatter keys are OpenDesign metadata and can be ignored. Sibling files in this\n"
    "> directory are the workflow's assets and references.\n"
)


def assemble(context: RecipeContext) -> RecipeResult:
    entry = context.entry
    source = context.upstream_dir
    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    synthesized: list[str] = []

    for name in TEMPLATES:
        _copy_skill(source / "design-templates" / name, staging_skills / name, name, synthesized)
        skills.append(name)

    for name in SCENARIOS:
        _copy_skill(
            source / "plugins" / "_official" / "scenarios" / name,
            staging_skills / name,
            name,
            synthesized,
        )
        skills.append(name)

    for atom in ATOMS:
        local_name = f"od-{atom}"
        _copy_skill(
            source / "plugins" / "_official" / "atoms" / atom,
            staging_skills / local_name,
            local_name,
            synthesized,
        )
        skills.append(local_name)

    craft_source = source / "craft"
    if not craft_source.is_dir():
        raise StructureChanged(f"{entry.package}: upstream craft/ is missing")
    craft_files = sorted(
        path.relative_to(source).as_posix()
        for path in craft_source.glob("*.md")
        if path.name != "FUTURE_SECTIONS.md"
    )
    craft_dir = staging_skills / "od-craft"
    for relative in craft_files:
        target = craft_dir / "craft" / Path(relative).name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((source / relative).read_bytes())
    (craft_dir / "SKILL.md").write_bytes(_craft_index(craft_files).encode("utf-8"))
    skills.append("od-craft")

    brands = [
        brand for brand in DESIGN_SYSTEMS if (source / "design-systems" / brand).is_dir()
    ]
    if not brands:
        raise StructureChanged(f"{entry.package}: upstream design-systems/ is empty")
    systems_dir = staging_skills / "od-design-systems"
    for brand in brands:
        for kept in SYSTEM_KEEP:
            candidate = source / "design-systems" / brand / kept
            if not candidate.is_file():
                continue
            target = systems_dir / "systems" / brand / kept
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidate.read_bytes())
    (systems_dir / "SKILL.md").write_bytes(_design_systems_index(brands).encode("utf-8"))
    skills.append("od-design-systems")

    notes = []
    if synthesized:
        notes.append(
            "synthesized frontmatter from open-design.json for: " + ", ".join(sorted(synthesized))
        )
    return RecipeResult(skills=skills, manual_skills=[], notes=notes)


def _copy_skill(
    source_dir: Path,
    target_dir: Path,
    name: str,
    synthesized: list[str],
) -> None:
    if not (source_dir / "SKILL.md").is_file():
        raise StructureChanged(f"upstream skill directory is missing SKILL.md: {source_dir}")

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source_dir)
        target = target_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if relative.as_posix() == "SKILL.md":
            text = _skill_markdown(
                path.read_text(encoding="utf-8"),
                name,
                source_dir / "open-design.json",
                synthesized,
            )
            target.write_bytes(text.encode("utf-8"))
        else:
            target.write_bytes(path.read_bytes())


def _skill_markdown(markdown: str, name: str, manifest_path: Path, synthesized: list[str]) -> str:
    """Normalize an upstream SKILL.md: force ``name``, prepend the harness note.

    An upstream file without frontmatter returns a synthesized header built from
    the sibling ``open-design.json``.
    """
    markdown = markdown.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if markdown.startswith("---\n"):
        end = markdown.index("\n---", 4)
        head, body = markdown[: end + 4], markdown[end + 4 :]
        lines = head.splitlines()
        for index, line in enumerate(lines):
            if line.startswith("name:"):
                lines[index] = f"name: {name}"
                break
        else:
            raise StructureChanged(f"{name}: SKILL.md frontmatter has no name field")
        return "\n".join(lines) + "\n\n" + ADAPT_NOTE + body

    if not manifest_path.is_file():
        raise StructureChanged(f"{name}: no frontmatter and no {manifest_path.name} to build one from")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StructureChanged(f"{name}: {manifest_path.name} is not valid JSON: {exc}") from exc
    # A body-only upstream file keeps its prose verbatim; the header is added above it.
    body = markdown
    description = " ".join(
        str(manifest.get("description") or manifest.get("title") or "").split()
    )
    if not description:
        raise StructureChanged(f"{name}: {manifest_path.name} has no usable description")
    synthesized.append(name)
    escaped = description.replace('"', "'")
    return f'---\nname: {name}\ndescription: "{escaped}"\n---\n\n' + ADAPT_NOTE + body


def _craft_index(craft_files: list[str]) -> str:
    craft_rows = "\n".join(f"| `{Path(p).name}` | {p}" for p in craft_files)
    return f"""---
name: od-craft
description: OpenDesign's craft references for UI and design work - typography, colour, spacing/hierarchy, animation discipline, accessibility baseline, form validation, state coverage, RTL/bidi, laws of UX, and anti-AI-slop taste rules. Use when producing or critiquing any interface, landing page, dashboard, deck or poster, or when a design needs to not look machine-made.
---

# OpenDesign craft references

Curated from OpenDesign's `craft/` folder (Apache-2.0, nexu-io/open-design). These are the
taste and quality rules the OpenDesign design templates assume. Read the one or two files
that match the current job - they are short and concrete.

| File | Read it when |
|---|---|
{craft_rows}

Files live in `craft/` next to this SKILL.md. Read them with the normal file-read tool.

## How to use them

1. Pick the files that match the artefact: any UI work reads `anti-ai-slop.md` plus
   `typography-hierarchy.md`; motion work reads `animation-discipline.md`; forms read
   `form-validation.md`; anything user-facing reads `accessibility-baseline.md`.
2. Apply the rules while writing, not as a review pass afterwards.
3. When a rule conflicts with the user's explicit instruction, the user wins - say which
   rule you dropped and why.
"""


def _design_systems_index(brands: list[str]) -> str:
    brand_rows = "\n".join(f"| `{brand}` | `systems/{brand}/DESIGN.md` |" for brand in brands)
    return f"""---
name: od-design-systems
description: OpenDesign design-system library - {len(brands)} brand and style DESIGN.md specs (Linear, Claude, OpenAI, GitHub, Notion, Figma, Apple, Material, IBM, Vercel, Stripe, Airbnb, Discord and more) with token files. Use when a deliverable needs a coherent, named visual identity rather than ad-hoc styling, or when the user names a brand look.
---

# OpenDesign design systems

Curated from OpenDesign's `design-systems/` library (Apache-2.0, nexu-io/open-design). Each
entry is a complete design system: `DESIGN.md` (colour, typography, spacing, components,
motion, do/don't rules), plus `tokens.css`, `tailwind-v4.css`, `design-tokens.json` and
`USAGE.md` where upstream publishes them.

| Design system | Entry point |
|---|---|
{brand_rows}

Everything sits under `systems/<brand>/` next to this SKILL.md.

## Workflow

1. **Pick one.** If the user names a brand or vibe, use that entry. Otherwise choose the
   system that fits the audience and say which one you picked and why.
2. **Read `DESIGN.md` in full** before writing any markup. Then read `tokens.css` and use
   those custom properties instead of inventing values.
3. **Stay inside the system.** Do not mix two systems, and do not add tokens the system does
   not define - if something is genuinely missing, extend the token set explicitly and say so.
4. Pair with `od-craft` for the quality rules that apply across every system.

Upstream's own templates expect the active design system to be injected into the model
context by the OpenDesign app. In DeepSeek Harness nothing is injected: this skill is how you
get it into context.
"""

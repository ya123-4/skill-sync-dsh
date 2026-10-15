"""Golden regression: every recipe must reproduce the installed bundle exactly.

This is why the recipes are trustworthy. Each test replays a recipe against the
exact upstream tag the installed bundle claims to come from and compares the
result with what is on disk today, byte for byte. It answers the only question
that matters about a recipe: "is this the same transformation that produced the
bundle we already ship?"

The upstream bytes live under ``tmpwork/skill-sync-upstream/snapshots`` and are
never committed. A missing snapshot skips with the command that fetches it,
rather than passing quietly.
"""
import json
from pathlib import Path

import pytest

from core.registry import load
from core.upstream import (
    fetch_npm_snapshot,
    fetch_snapshot,
    local_revision,
    local_version,
    npm_snapshot_slug,
    snapshot_slug,
)
from recipes.base import RecipeContext

PKG_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE = PKG_ROOT.parent.parent
SNAPSHOT_CACHE = WORKSPACE / "tmpwork" / "skill-sync-upstream"


def registry_entry(package: str):
    entries = load(WORKSPACE)
    return next(entry for entry in entries if entry.package == package)


def installed_version(entry) -> str:
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    return manifest["version"]


def pinned_ref(entry) -> str:
    """The upstream revision the installed bytes came from.

    Normally the tag matching the bundle's own version; for a bundle built from a
    mid-branch snapshot (see KNOWN_DEVIATIONS.md) the recorded commit instead. A
    bundle vendoring an npm package is pinned by its ``optionalDependencies``
    entry, because its own version line is unrelated to the package's. A
    commit-tracked bundle has no tag, so it replays the revision in its state file
    (or the registry's baseline before the first sync).
    """
    if entry.kind == "npm":
        return local_version(entry)
    if entry.kind == "github-commits":
        return entry.baseline_commit or local_revision(entry) or ""
    return entry.baseline_commit or f"{entry.tag_prefix}{installed_version(entry)}"


def snapshot_for(entry) -> Path:
    ref = pinned_ref(entry)
    if not ref:
        pytest.skip(
            f"no recorded revision for {entry.package}; run: "
            f"python tools/fetch_snapshots.py --only {entry.package}"
        )
    if entry.kind == "npm":
        slug = npm_snapshot_slug(entry.source, ref)
        root = SNAPSHOT_CACHE / "snapshots" / slug
        if not (root / ".complete").is_file():
            pytest.skip(
                f"missing npm snapshot for {entry.package}@{ref}; "
                f"run: python tools/fetch_snapshots.py --only {entry.package}"
            )
        return fetch_npm_snapshot(entry, ref, SNAPSHOT_CACHE)

    root = SNAPSHOT_CACHE / "snapshots" / snapshot_slug(entry.repo, ref)
    if not (root / ".complete").is_file():
        pytest.skip(
            f"missing upstream snapshot for {entry.package}@{ref}; "
            f"run: python tools/fetch_snapshots.py --only {entry.package}"
        )
    return fetch_snapshot(entry, ref, SNAPSHOT_CACHE)


def context_for(entry, staging: Path) -> RecipeContext:
    return RecipeContext(
        upstream_dir=snapshot_for(entry),
        upstream_ref=pinned_ref(entry),
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )


def assert_tree_identical(produced: Path, shipped: Path) -> None:
    """Same file set, same bytes — the recipe reproduces the shipped bundle."""
    produced_files = {
        path.relative_to(produced).as_posix(): path
        for path in produced.rglob("*")
        if path.is_file()
    }
    shipped_files = {
        path.relative_to(shipped).as_posix(): path
        for path in shipped.rglob("*")
        if path.is_file()
    }

    missing = sorted(set(shipped_files) - set(produced_files))
    extra = sorted(set(produced_files) - set(shipped_files))
    differing = sorted(
        relative
        for relative in set(produced_files) & set(shipped_files)
        if produced_files[relative].read_bytes() != shipped_files[relative].read_bytes()
    )

    assert not missing, f"recipe produced nothing for {len(missing)} file(s): {missing[:8]}"
    assert not extra, f"recipe produced {len(extra)} unexpected file(s): {extra[:8]}"
    assert not differing, f"{len(differing)} file(s) differ: {differing[:8]}"


def test_superpowers_golden(scratch):
    from recipes.superpowers import assemble

    entry = registry_entry("superpowers-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_vercel_skills_golden(scratch):
    from recipes.vercel_skills import assemble

    entry = registry_entry("vercel-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == ["skills-cli"]
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_agent_browser_golden(scratch):
    from recipes.agent_browser import assemble

    entry = registry_entry("agent-browser-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_agent_browser_omits_the_discovery_stub(scratch):
    from recipes.agent_browser import assemble

    entry = registry_entry("agent-browser-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()
    assemble(context_for(entry, staging))

    # upstream skills/agent-browser/SKILL.md is a thin redirect stub, not a skill
    assert not (staging / "skills" / "agent-browser").exists()


def test_agent_browser_missing_skill_data_reports_structure_change(scratch):
    from recipes.agent_browser import assemble
    from recipes.base import StructureChanged

    entry = registry_entry("agent-browser-skill-dsh")
    upstream = scratch / "upstream"
    upstream.mkdir()
    staging = scratch / "staging"
    staging.mkdir()
    context = RecipeContext(
        upstream_dir=upstream,
        upstream_ref="v0.38.2",
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )

    with pytest.raises(StructureChanged):
        assemble(context)


def test_mattpocock_golden(scratch):
    from recipes.mattpocock import assemble

    entry = registry_entry("mattpocock-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_mattpocock_whitelist_and_flattening(scratch):
    from recipes.mattpocock import assemble

    entry = registry_entry("mattpocock-skills-dsh")
    upstream = scratch / "upstream"
    staging = scratch / "staging"
    staging.mkdir()
    _write_fake_mattpocock_upstream(upstream)

    result = assemble(
        RecipeContext(
            upstream_dir=upstream,
            upstream_ref="v9.9.9",
            plugin_dir=entry.plugin_dir,
            staging_dir=staging,
            entry=entry,
        )
    )

    # only the skills listed in .claude-plugin/plugin.json ship, flattened one level
    assert result.skills == ["alpha"]
    assert (staging / "skills" / "alpha" / "SKILL.md").is_file()
    assert not (staging / "skills" / "engineering").exists()
    assert not (staging / "skills" / "beta").exists()


def test_mattpocock_prose_normalization():
    from recipes.mattpocock import normalize_prose

    assert normalize_prose("Use the Skill tool first.", {"tdd"}) == "Use the `skill` tool first."
    assert normalize_prose("Run `/tdd` now.", {"tdd"}) == "Run `tdd` now."
    assert normalize_prose("a `/not-a-skill` ref", {"tdd"}) == "a `/not-a-skill` ref"


def _write_fake_mattpocock_upstream(upstream: Path) -> None:
    (upstream / ".claude-plugin").mkdir(parents=True)
    (upstream / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"version": "9.9.9", "skills": ["./skills/engineering/alpha"]}),
        encoding="utf-8",
    )
    for group, name in (("engineering", "alpha"), ("engineering", "beta")):
        skill = upstream / "skills" / group / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: x\n---\n\nUse the Skill tool.\n",
            encoding="utf-8",
        )


OPEN_DESIGN_SOURCES = WORKSPACE / "tmpwork" / "od-source"


def open_design_sources(entry) -> Path:
    """The cached jsDelivr material for the pinned open-design commit.

    open-design is a 3.5 GB repository, so the source material is fetched per file
    rather than as a tarball; the cache is populated by
    ``tools/fetch_open_design_sources.py``.
    """
    root = OPEN_DESIGN_SOURCES / str(entry.baseline_commit)
    if not root.is_dir():
        pytest.skip(
            f"missing open-design source cache for {entry.baseline_commit}; "
            f"run: python tools/fetch_open_design_sources.py --commit {entry.baseline_commit}"
        )
    return root


def open_design_context(entry, staging: Path) -> RecipeContext:
    return RecipeContext(
        upstream_dir=open_design_sources(entry),
        upstream_ref=str(entry.baseline_commit),
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )


def test_open_design_golden(scratch):
    from recipes.open_design import assemble

    entry = registry_entry("open-design-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(open_design_context(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_open_design_atoms_are_renamed(scratch):
    from recipes.open_design import assemble

    entry = registry_entry("open-design-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()
    assemble(open_design_context(entry, staging))

    assert (staging / "skills" / "od-handoff" / "SKILL.md").is_file()
    assert (staging / "skills" / "od-token-map" / "SKILL.md").is_file()
    assert not (staging / "skills" / "handoff").exists()


def test_open_design_injects_the_harness_note(scratch):
    from recipes.open_design import assemble

    entry = registry_entry("open-design-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()
    assemble(open_design_context(entry, staging))

    # The two index skills are authored here, not derived from an upstream body.
    index_skills = {"od-craft", "od-design-systems"}
    missing = [
        path.parent.name
        for path in sorted((staging / "skills").glob("*/SKILL.md"))
        if path.parent.name not in index_skills
        and "DeepSeek Harness note" not in path.read_text(encoding="utf-8")
    ]
    assert missing == []


def test_open_design_synthesizes_frontmatter_for_body_only_scenarios(scratch):
    from recipes.open_design import assemble

    entry = registry_entry("open-design-skills-dsh")
    staging = scratch / "staging"
    staging.mkdir()
    assemble(open_design_context(entry, staging))

    for name in ("od-design-refine", "od-react-export", "od-nextjs-export", "od-vue-export"):
        text = (staging / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), name
        assert f"name: {name}" in text, name
        assert "description:" in text, name


def test_humanizer_zh_golden(scratch):
    """The Chinese edition's bytes come from a commit, not a tag."""
    from recipes.root_skill import assemble

    entry = registry_entry("humanizer-zh-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_humanizer_golden(scratch):
    """The English original's bytes come from its release tag."""
    from recipes.root_skill import assemble

    entry = registry_entry("humanizer-skill-dsh")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == []
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_rea_golden(scratch):
    from recipes.rea import assemble

    entry = registry_entry("rea-dsh-plugin")
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(entry, staging))

    assert sorted(result.skills) == sorted(entry.expected_skills)
    assert result.manual_skills == ["rea-dsh"]
    assert_tree_identical(staging / "skills", entry.plugin_dir / "skills")


def test_rea_reports_a_moved_connect_section(scratch):
    """A section that no longer sits where the overlay expects is an error, not a patch."""
    from recipes.base import AnchorNotFound
    from recipes.rea import assemble

    entry = registry_entry("rea-dsh-plugin")
    upstream = scratch / "upstream" / entry.skills_root / "reverse-engineer-anything"
    upstream.mkdir(parents=True)
    (upstream / "SKILL.md").write_text(
        "---\nname: reverse-engineer-anything\ndescription: fixture\n---\n\n"
        "## Connect only when needed\n\nupstream text\n",
        encoding="utf-8",
    )
    staging = scratch / "staging"
    staging.mkdir()

    context = RecipeContext(
        upstream_dir=scratch / "upstream",
        upstream_ref="fixture",
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )
    with pytest.raises(AnchorNotFound):
        assemble(context)

"""aiops_monorepo: take exactly the declared skills out of a monorepo, then adapt their text.

``aliyun/alibabacloud-aiops-skills`` is a monorepo: ``skills/developertools/solutions/``
holds one directory per skill (about two hundred of them, 7280 files in the tree).
A recipe that copied ``skills_root`` wholesale -- the superpowers rule -- would pull
every unrelated skill into this bundle, so this recipe copies only the directories the
registry declares in ``expectedSkills`` and treats a missing one as a structure change.

The upstream ``alibabacloud-workbench-cli`` text is written for a generic Linux shell.
The one named local deviation is the overlay under ``recipes/overlays/``; it is applied
after the upstream Troubleshooting table's last row and nowhere else, so a moved anchor
is an ``AnchorNotFound`` rather than a note appended in the wrong place.
"""
from pathlib import Path

import pytest

from core.registry import Entry
from recipes.aiops_monorepo import LAST_TROUBLESHOOTING_ROW_START
from recipes.base import AnchorNotFound, RecipeContext, StructureChanged

UPSTREAM_SKILL = "alibabacloud-workbench-cli"
# Upstream's last Troubleshooting row exactly as upstream writes it: its error name is the
# one row of that table *not* wrapped in backticks, and its fix text sits on the same line
# as the exit-code cell. The whole row, including its newline, is what the note follows --
# inserting after the cell prefix alone would split the row and strand its fix text at EOF.
ANCHOR = LAST_TROUBLESHOOTING_ROW_START
ANCHOR_ROW = ANCHOR + " If using `RamRoleArn` mode, the CLI auto-refreshes.\n"


@pytest.fixture()
def entry(scratch) -> Entry:
    """The workbench entry, built directly so this test does not need the registry.

    Task 3 adds the registry row; constructing the ``Entry`` here keeps this recipe
    testable on its own, and pins the values the registry row will have to match.
    """
    return Entry(
        package="alibabacloud-workbench-cli-skill-dsh",
        plugin_dir=scratch / "bundle",
        repo="aliyun/alibabacloud-aiops-skills",
        tag_prefix="alibabacloud-workbench-cli-",
        skills_root="skills/developertools/solutions",
        strip_prefix="",
        recipe="aiops_monorepo",
        expected_skills=("alibabacloud-workbench-cli",),
        manual_skills=(),
        commit_pinned=True,
        license="Apache-2.0",
        notes="",
    )


def upstream_root(scratch, names) -> Path:
    """An upstream checkout carrying a troubleshooting table and the named skills."""
    root = scratch / "upstream"
    base = root / "skills" / "developertools" / "solutions"
    for name in names:
        directory = base / name
        directory.mkdir(parents=True, exist_ok=True)
        body = (
            "---\n"
            f"name: {name}\n"
            "description: fixture\n"
            "---\n"
            "\n"
            "## Troubleshooting\n"
            "\n"
            "| Error | Exit code | Fix |\n"
            "|---|---|---|\n"
            "| Permission denied (RAM) | 4 | Attach the policy. |\n"
            + ANCHOR_ROW
        )
        # write_bytes, not write_text: Windows newline translation would turn this
        # fixture into a CRLF file and make byte-level assertions a platform question.
        (directory / "SKILL.md").write_bytes(body.encode("utf-8"))
    return root


def context_for(upstream: Path, staging: Path, entry: Entry) -> RecipeContext:
    return RecipeContext(
        upstream_dir=upstream,
        upstream_ref="alibabacloud-workbench-cli-0.0.1",
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )


def test_only_declared_skills_are_copied(scratch, entry):
    """A monorepo holds hundreds of skills; only the declared one may land."""
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL, "some-other-skill"])
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(context_for(upstream, staging, entry))

    assert result.skills == [UPSTREAM_SKILL]
    assert result.manual_skills == []
    assert (staging / "skills" / UPSTREAM_SKILL / "SKILL.md").is_file()
    assert not (staging / "skills" / "some-other-skill").exists()


def test_missing_declared_skill_is_a_structure_change(scratch, entry):
    """Upstream dropping the skill must be reported, not silently produce an empty bundle."""
    from recipes.aiops_monorepo import assemble

    upstream = scratch / "upstream"
    upstream.mkdir()
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged):
        assemble(context_for(upstream, staging, entry))


def test_a_declared_skill_without_a_skill_md_is_a_structure_change(scratch, entry):
    """The directory exists but carries no prompt: still a structure change, not a bundle."""
    from recipes.aiops_monorepo import assemble

    upstream = scratch / "upstream"
    base = upstream / "skills" / "developertools" / "solutions"
    (base / UPSTREAM_SKILL).mkdir(parents=True)
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged):
        assemble(context_for(upstream, staging, entry))


def test_overlay_is_appended_after_the_troubleshooting_table(scratch, entry):
    """The DSH note lands directly below the last table row, and the file ends in one newline."""
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    staging = scratch / "staging"
    staging.mkdir()

    assemble(context_for(upstream, staging, entry))

    produced = (staging / "skills" / UPSTREAM_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert "## DeepSeek Harness notes" in produced
    assert "self-dial check failed" in produced
    assert produced.endswith("State the target instance and the impact first.\n")
    assert not produced.endswith("\n\n")
    # The whole row survives, exactly once, and the note follows the row's newline: this is
    # what fails if the note is inserted after a cell prefix instead of after the row, in
    # which case the row is split and its fix text ends up after the note.
    assert produced.count(ANCHOR_ROW) == 1
    # The note starts on the line after the row: the row, then the overlay's own leading
    # blank line, then the heading.
    assert produced.index(ANCHOR_ROW) + len(ANCHOR_ROW) == produced.index(
        "\n## DeepSeek Harness notes"
    )
    # Everything before the insertion point is upstream's text, unchanged.
    upstream_text = (upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert produced.startswith(upstream_text[: upstream_text.index(ANCHOR)])


def test_moved_anchor_raises_instead_of_relocating(scratch, entry):
    """A rewritten table row must fail loudly, and must not leave a half-written skill behind."""
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    skill_md.write_bytes(
        skill_md.read_bytes().replace(ANCHOR_ROW.encode("utf-8"), b"| not the anchor |\n")
    )
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(AnchorNotFound):
        assemble(context_for(upstream, staging, entry))

    assert not (staging / "skills" / UPSTREAM_SKILL / "SKILL.md").exists()


def test_a_backticked_anchor_is_not_upstreams_row(scratch, entry):
    """Pin the upstream shape this anchor depends on: that row's name is *not* backticked.

    Every other row of upstream's Troubleshooting table wraps its error in backticks, which
    makes the backticked spelling of this one look correct in prose. Upstream's actual row is
    ``| STS token expired | 4 |`` (no backticks), so a recipe using the backticked spelling
    would raise ``AnchorNotFound`` on every real rebuild. This test fails if anyone "fixes"
    the anchor to match the rest of the table.
    """
    from recipes.aiops_monorepo import assemble

    assert LAST_TROUBLESHOOTING_ROW_START == "| STS token expired | 4 |"
    assert "`" not in LAST_TROUBLESHOOTING_ROW_START
    assert LAST_TROUBLESHOOTING_ROW_START in ANCHOR_ROW

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    # Rewrite the row the way the rest of the table looks: error name in backticks.
    skill_md.write_bytes(
        skill_md.read_bytes().replace(
            ANCHOR_ROW.encode("utf-8"),
            "| `STS token expired` | 4 | If using `RamRoleArn` mode, the CLI auto-refreshes.\n".encode(
                "utf-8"
            ),
        )
    )
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(AnchorNotFound):
        assemble(context_for(upstream, staging, entry))


def test_an_ambiguous_anchor_raises_instead_of_picking_one(scratch, entry):
    """Two identical rows do not identify one insertion point, so the recipe must refuse."""
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    # Duplicate the row: the anchor is now ambiguous.
    skill_md.write_bytes(skill_md.read_bytes() + ANCHOR_ROW.encode("utf-8"))
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(AnchorNotFound):
        assemble(context_for(upstream, staging, entry))

    assert not (staging / "skills" / UPSTREAM_SKILL / "SKILL.md").exists()

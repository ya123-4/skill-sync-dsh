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
from dataclasses import replace
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
    """An upstream with no skills tree at all must be reported, not vendored as an empty bundle."""
    from recipes.aiops_monorepo import assemble

    upstream = scratch / "upstream"
    upstream.mkdir()
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert not (staging / "skills").exists()
    assert entry.skills_root in str(excinfo.value)


def test_a_declared_skill_missing_from_a_present_skills_root_is_a_structure_change(scratch, entry):
    """``skills_root`` exists and holds other skills: the *declared* directory is what is gone.

    This is the branch the empty-upstream test above cannot reach -- upstream still has
    ``skills/developertools/solutions/``, so the monorepo shape looks fine until the one
    declared directory turns out to be missing.
    """
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, ["some-other-skill"])
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert UPSTREAM_SKILL in str(excinfo.value)
    assert not (staging / "skills").exists()


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


def test_a_second_declared_skill_outside_the_overlay_targets_is_copied_verbatim(scratch, entry):
    """The overlay is for the skill that needs it; another declared skill is vendored untouched.

    The fixture gives every skill the same Troubleshooting table, so the second skill carries
    the anchor too. An "apply the overlay to every declared skill" regression therefore
    changes its bytes (and adds a second note) instead of passing unnoticed.
    """
    from recipes.aiops_monorepo import assemble

    other = "some-other-skill"
    upstream = upstream_root(scratch, [UPSTREAM_SKILL, other])
    staging = scratch / "staging"
    staging.mkdir()

    result = assemble(
        context_for(upstream, staging, replace(entry, expected_skills=(UPSTREAM_SKILL, other)))
    )

    assert result.skills == [UPSTREAM_SKILL, other]
    staged = (staging / "skills" / other / "SKILL.md").read_bytes()
    assert staged == (upstream / entry.skills_root / other / "SKILL.md").read_bytes()
    assert b"DeepSeek Harness notes" not in staged
    assert len(result.notes) == 1
    assert UPSTREAM_SKILL in result.notes[0]
    assert "## DeepSeek Harness notes" in (
        staging / "skills" / UPSTREAM_SKILL / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_a_crlf_skill_md_is_copied_byte_for_byte(scratch, entry):
    """Upstream's bytes are copied verbatim: text mode would silently rewrite CRLF to LF.

    The only bytes this recipe may add are the overlay block, so the staged file is exactly
    upstream's bytes followed by the overlay file's bytes -- CRs included.
    """
    from recipes import aiops_monorepo
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    crlf = skill_md.read_bytes().replace(b"\n", b"\r\n")
    assert b"\r\n" in crlf
    skill_md.write_bytes(crlf)
    overlay = (aiops_monorepo.OVERLAY_DIR / UPSTREAM_SKILL / "dsh-notes.txt").read_bytes()
    staging = scratch / "staging"
    staging.mkdir()

    assemble(context_for(upstream, staging, entry))

    staged = (staging / "skills" / UPSTREAM_SKILL / "SKILL.md").read_bytes()
    assert staged == crlf + overlay


def test_a_non_utf8_skill_md_is_a_structure_change(scratch, entry):
    """Bytes this recipe cannot read are an upstream shape change: exit 4, not a traceback."""
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    (upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md").write_bytes(
        b"---\nname: x\n---\n\n\xff\xfe not UTF-8\n"
    )
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert UPSTREAM_SKILL in str(excinfo.value)
    assert not (staging / "skills").exists()


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
    # Not just that one file: a moved anchor must not leave a candidate tree behind at all.
    assert not (staging / "skills").exists()


def test_prose_after_the_table_raises_instead_of_relocating(scratch, entry):
    """Upstream appending prose after the table moves the anchor off the document tail.

    The note may only be appended at the end of the file. This input evades the
    "ends in exactly one newline" guard -- the note lands mid-document and the result still
    ends in a single ``\\n`` -- so without the tail check a misplaced note would ship
    silently, which is the failure mode the anchor contract exists to prevent.
    """
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + b"\n## Appendix\n\nmore.\n")
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(AnchorNotFound) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert "is not the file's last content" in str(excinfo.value)
    assert not (staging / "skills").exists()


@pytest.mark.parametrize("tail", [b"\n", b"   \n", b"\n \n"])
def test_whitespace_after_the_table_is_refused_as_an_upstream_shape(scratch, entry, tail):
    """Whitespace after the row is an upstream shape question, so the refusal must say so.

    The overlay file is not at fault here: upstream no longer ends at its table. The message
    must name what actually went wrong instead of blaming the note the recipe was applying,
    and the note must never be left stranded above a whitespace-only line -- including tails
    that end in a single newline, which a plain "ends in one newline" check would accept.
    """
    from recipes.aiops_monorepo import assemble

    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    skill_md = upstream / entry.skills_root / UPSTREAM_SKILL / "SKILL.md"
    skill_md.write_bytes(skill_md.read_bytes() + tail)
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(AnchorNotFound) as excinfo:
        assemble(context_for(upstream, staging, entry))

    message = str(excinfo.value)
    assert "whitespace after the anchor row" in message
    assert "overlay" not in message.lower()
    assert not (staging / "skills").exists()


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


def test_a_missing_overlay_is_a_packaging_fault(scratch, entry, monkeypatch):
    """An absent overlay file is a broken plugin, not upstream moving its table."""
    from recipes import aiops_monorepo
    from recipes.aiops_monorepo import assemble

    monkeypatch.setattr(aiops_monorepo, "OVERLAY_DIR", scratch / "no-overlays-here")
    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert "overlay text is missing" in str(excinfo.value)
    assert not (staging / "skills").exists()


def test_a_malformed_overlay_is_a_packaging_fault(scratch, entry, monkeypatch):
    """An overlay that does not end in exactly one newline must not be blamed on the anchor."""
    from recipes import aiops_monorepo
    from recipes.aiops_monorepo import assemble

    overlays = scratch / "overlays" / UPSTREAM_SKILL
    overlays.mkdir(parents=True)
    (overlays / "dsh-notes.txt").write_bytes(b"\n## DeepSeek Harness notes\n\nno final newline")
    monkeypatch.setattr(aiops_monorepo, "OVERLAY_DIR", scratch / "overlays")
    upstream = upstream_root(scratch, [UPSTREAM_SKILL])
    staging = scratch / "staging"
    staging.mkdir()

    with pytest.raises(StructureChanged) as excinfo:
        assemble(context_for(upstream, staging, entry))

    assert "exactly one newline" in str(excinfo.value)
    assert not (staging / "skills").exists()

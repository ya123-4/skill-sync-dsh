"""root_skill: one upstream SKILL.md becomes one skill, byte for byte."""
from dataclasses import replace

import pytest

from helpers import make_entry
from recipes.base import RecipeContext, StructureChanged


def context(scratch, upstream, entry, name="staging"):
    staging = scratch / name
    staging.mkdir()
    return RecipeContext(
        upstream_dir=upstream,
        upstream_ref="deadbeef",
        plugin_dir=entry.plugin_dir,
        staging_dir=staging,
        entry=entry,
    )


def upstream_skill(scratch, text, name="upstream"):
    """Write the fixture with explicit bytes.

    ``write_text`` translates newlines on Windows, which would make this fixture a
    CRLF file and turn a byte-for-byte copy assertion into a platform question.
    """
    root = scratch / name
    root.mkdir()
    (root / "SKILL.md").write_bytes(text.encode("utf-8"))
    return root


def test_root_skill_copies_the_bytes(scratch):
    from recipes.root_skill import assemble

    entry = replace(
        make_entry(scratch, package="humanizer-zh-skill-dsh", expected=("humanizer-zh",)),
        skills_root=".",
    )
    body = "---\nname: humanizer-zh\ndescription: 中文\n---\n\n正文\n"

    result = assemble(context(scratch, upstream_skill(scratch, body), entry))

    assert result.skills == ["humanizer-zh"]
    assert result.manual_skills == []
    produced = scratch / "staging" / "skills" / "humanizer-zh" / "SKILL.md"
    assert produced.read_bytes() == body.encode("utf-8")


def test_root_skill_rejects_a_renamed_skill(scratch):
    """Upstream renaming its skill must surface as a structure change, not a broken bundle."""
    from recipes.root_skill import assemble

    entry = replace(make_entry(scratch, expected=("humanizer-zh",)), skills_root=".")
    body = "---\nname: humanizer-english\ndescription: x\n---\n\nbody\n"

    with pytest.raises(StructureChanged):
        assemble(context(scratch, upstream_skill(scratch, body), entry))


def test_root_skill_reports_a_missing_upstream_skill_md(scratch):
    from recipes.root_skill import assemble

    entry = replace(make_entry(scratch, expected=("humanizer",)), skills_root=".")
    empty = scratch / "upstream-empty"
    empty.mkdir()

    with pytest.raises(StructureChanged):
        assemble(context(scratch, empty, entry))

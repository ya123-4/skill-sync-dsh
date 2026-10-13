"""Recipe contract and the overlay primitives recipes use.

A recipe turns an unpacked upstream checkout into a ``skills/`` tree. It reads
only ``upstream_dir`` and writes only ``staging_dir``; installing the result is
the core's job. Local deviations from upstream are expressed as overlay text
applied through the two primitives below, so they stay visible, reviewable and
diffable instead of hiding inside a recipe body.
"""
import json

import pytest

from core.registry import Entry
from recipes.base import (
    AnchorNotFound,
    RecipeContext,
    RecipeResult,
    insert_after,
    replace_between,
    replace_once,
    write_tree,
)


def make_entry(scratch):
    plugin = scratch / "demo-skill-dsh"
    (plugin / "skills").mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": "demo-skill-dsh", "version": "1.0.0"}), encoding="utf-8"
    )
    return Entry(
        package="demo-skill-dsh",
        plugin_dir=plugin,
        repo="demo/skills",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe="demo",
        expected_skills=("demo",),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


def make_context(scratch):
    entry = make_entry(scratch)
    upstream = scratch / "upstream"
    upstream.mkdir()
    return RecipeContext(
        upstream_dir=upstream,
        upstream_ref="v1.0.0",
        plugin_dir=entry.plugin_dir,
        staging_dir=scratch / "staging",
        entry=entry,
    )


def test_insert_after_inserts_once():
    assert insert_after("a\nb\n", "a\n", "X\n") == "a\nX\nb\n"


def test_insert_after_missing_anchor_raises():
    with pytest.raises(AnchorNotFound):
        insert_after("a\n", "zzz", "X\n")


def test_replace_between_replaces_the_inclusive_range():
    assert replace_between("s\nmid\ne\n", "s\n", "e\n", "N\n") == "N\n"


def test_replace_between_missing_end_raises():
    with pytest.raises(AnchorNotFound):
        replace_between("s\nmid\n", "s\n", "e\n", "N\n")


def test_write_tree_preserves_bytes_and_structure(scratch):
    source = scratch / "source"
    (source / "nested").mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: foo\n---\n✨ 中文\n", encoding="utf-8")
    (source / "nested" / "blob.bin").write_bytes(bytes(range(256)))

    written = write_tree(source, scratch / "target")

    assert (scratch / "target" / "SKILL.md").read_bytes() == (source / "SKILL.md").read_bytes()
    assert (scratch / "target" / "nested" / "blob.bin").read_bytes() == bytes(range(256))
    assert "SKILL.md" in written
    assert "nested/blob.bin" in written


def test_result_collects_skills_manual_and_notes():
    result = RecipeResult(skills=["a", "b"], manual_skills=["m"], notes=["n"])
    assert result.skills == ["a", "b"]
    assert result.manual_skills == ["m"]
    assert result.notes == ["n"]


def test_context_requires_an_existing_upstream_dir(scratch):
    entry = make_entry(scratch)
    with pytest.raises(ValueError):
        RecipeContext(
            upstream_dir=scratch / "missing",
            upstream_ref="v1.0.0",
            plugin_dir=entry.plugin_dir,
            staging_dir=scratch / "staging",
            entry=entry,
        )


def test_context_rejects_staging_inside_the_plugin_dir(scratch):
    entry = make_entry(scratch)
    upstream = scratch / "upstream"
    upstream.mkdir()
    with pytest.raises(ValueError):
        RecipeContext(
            upstream_dir=upstream,
            upstream_ref="v1.0.0",
            plugin_dir=entry.plugin_dir,
            staging_dir=entry.plugin_dir / "staging",
            entry=entry,
        )


def test_context_accepts_a_staging_dir_outside_the_plugin_dir(scratch):
    context = make_context(scratch)
    assert context.upstream_ref == "v1.0.0"
    assert context.entry.package == "demo-skill-dsh"


def test_replace_once_replaces_the_first_occurrence_only():
    assert replace_once("a\nb\na\n", "a\n", "X\n") == "X\nb\na\n"


def test_replace_once_missing_text_raises():
    with pytest.raises(AnchorNotFound):
        replace_once("a\n", "zzz", "X\n")

"""Candidate verification: the six rules that decide whether a rebuild may land."""
import hashlib
from pathlib import Path

import pytest

from core.registry import Entry
from core.verify import VerifyFailed, blocking, verify_candidate


def make_entry(scratch, expected=("alpha",), manual=()) -> Entry:
    plugin = scratch / "demo-skill-dsh"
    (plugin / "skills").mkdir(parents=True, exist_ok=True)
    (plugin / "package.json").write_text('{"name": "demo-skill-dsh", "version": "1.0.0"}', encoding="utf-8")
    return Entry(
        package="demo-skill-dsh",
        plugin_dir=plugin,
        repo="demo/skills",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe="demo",
        expected_skills=tuple(expected),
        manual_skills=tuple(manual),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


def make_candidate(scratch, name="staging", skills=None, filename="staging") -> Path:
    staging = scratch / filename
    skills = skills if skills is not None else {"alpha": "alpha", "beta": "beta"}
    for skill_name, declared in skills.items():
        directory = staging / "skills" / skill_name
        directory.mkdir(parents=True, exist_ok=True)
        frontmatter = "" if declared is None else f"---\nname: {declared}\ndescription: d\n---\n\nbody\n"
        (directory / "SKILL.md").write_text(frontmatter, encoding="utf-8")
    return staging


def codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_clean_candidate_has_no_issues(scratch):
    entry = make_entry(scratch, expected=("alpha", "beta"))
    assert verify_candidate(make_candidate(scratch), entry) == []


def test_frontmatter_name_mismatch_blocks(scratch):
    entry = make_entry(scratch, expected=("alpha", "beta"))
    staging = make_candidate(scratch, skills={"alpha": "gamma", "beta": "beta"})
    issues = verify_candidate(staging, entry)
    assert "name-mismatch" in codes(issues)
    assert "name-mismatch" in {issue.code for issue in blocking(issues)}


def test_legacy_camelcase_frontmatter_blocks(scratch):
    entry = make_entry(scratch)
    staging = make_candidate(scratch, skills={"alpha": "alpha"})
    (staging / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\ndisableModelInvocation: true\n---\n\nbody\n",
        encoding="utf-8",
    )
    assert "legacy-frontmatter" in codes(verify_candidate(staging, entry))


def test_unexpected_structure_change_blocks(scratch):
    entry = make_entry(scratch, expected=("alpha", "beta"))
    staging = make_candidate(scratch, skills={"alpha": "alpha", "gamma": "gamma"})
    issues = verify_candidate(staging, entry)
    assert "structure-changed" in codes(issues)


def test_structure_change_is_ignorable_by_code(scratch):
    entry = make_entry(scratch, expected=("alpha", "beta"))
    staging = make_candidate(scratch, skills={"alpha": "alpha", "gamma": "gamma"})
    issues = verify_candidate(staging, entry)
    assert blocking(issues, ignore_codes={"structure-changed"}) == []
    with pytest.raises(VerifyFailed):
        raise VerifyFailed(blocking(issues))


def test_name_collision_with_another_bundle_blocks(scratch):
    entry = make_entry(scratch, expected=("alpha",))
    staging = make_candidate(scratch, skills={"alpha": "alpha"})
    issues = verify_candidate(staging, entry, profile_skills={"alpha", "handoff"})
    assert "duplicate-skill-name" in codes(issues)


def test_manual_skill_change_blocks(scratch):
    entry = make_entry(scratch, expected=("alpha",), manual=("manual-skill",))
    staging = make_candidate(scratch, skills={"alpha": "alpha", "manual-skill": "manual-skill"})
    digest = hashlib.sha256(b"something else entirely").hexdigest()
    issues = verify_candidate(
        staging, entry, manual_snapshot={"manual-skill/SKILL.md": digest}
    )
    assert "manual-changed" in codes(issues)


def test_manual_skill_unchanged_passes(scratch):
    entry = make_entry(scratch, expected=("alpha",), manual=("manual-skill",))
    staging = make_candidate(scratch, skills={"alpha": "alpha", "manual-skill": "manual-skill"})
    digest = hashlib.sha256(
        (staging / "skills" / "manual-skill" / "SKILL.md").read_bytes()
    ).hexdigest()
    issues = verify_candidate(
        staging, entry, manual_snapshot={"manual-skill/SKILL.md": digest}
    )
    assert "manual-changed" not in codes(issues)


def test_payload_shrink_blocks_and_growth_warns(scratch):
    entry = make_entry(scratch, expected=("alpha",))
    previous = scratch / "previous"
    (previous / "alpha").mkdir(parents=True)
    (previous / "alpha" / "SKILL.md").write_text("x" * 10_000, encoding="utf-8")

    tiny = scratch / "tiny"
    (tiny / "skills" / "alpha").mkdir(parents=True)
    (tiny / "skills" / "alpha" / "SKILL.md").write_text("x" * 100, encoding="utf-8")
    assert "payload-shrunk" in codes(verify_candidate(tiny, entry, previous=previous))

    huge = scratch / "huge"
    (huge / "skills" / "alpha").mkdir(parents=True)
    (huge / "skills" / "alpha" / "SKILL.md").write_text("x" * 40_000, encoding="utf-8")
    issues = verify_candidate(huge, entry, previous=previous)
    assert "payload-changed" in codes(issues)
    assert "payload-changed" not in {issue.code for issue in blocking(issues)}


def test_missing_skills_directory_blocks(scratch):
    entry = make_entry(scratch)
    empty = scratch / "empty"
    empty.mkdir()
    assert "no-skills" in codes(verify_candidate(empty, entry))

"""registry 只接受它真能跑的 kind × mode 组合。

加载得进来、一跑就崩的条目，比直接拒绝更糟：check 会给一个假的 unknown，
update 会抛 TypeError。这里把那些组合一个一个钉死。
"""
from pathlib import Path

import pytest

from core.registry import RegistryError, _build_entry

WORKSPACE = Path(__file__).resolve().parents[3]

UPSTREAM = {"repo": "o/r", "tagPrefix": "v", "skillsRoot": "skills", "stripPrefix": ""}


@pytest.mark.parametrize(
    "item",
    [
        pytest.param(
            {"package": "x", "kind": "nope", "mode": "manual", "source": "s"}, id="unknown-kind"
        ),
        pytest.param(
            {"package": "x", "kind": "github-commits", "mode": "manual", "source": "o/r"},
            id="commits-manual-not-supported",
        ),
        pytest.param(
            {"package": "x", "kind": "github-tags", "mode": "manual", "source": "o/r"},
            id="manual-needs-a-version-mapping",
        ),
        pytest.param(
            {
                "package": "x",
                "kind": "npm",
                "mode": "manual",
                "source": "pkg",
                "pluginDir": "archify-plugin-build",
            },
            id="npm-never-owns-a-directory",
        ),
    ],
)
def test_registry_refuses_combinations_the_code_cannot_run(item):
    with pytest.raises(RegistryError):
        _build_entry(item, WORKSPACE)


@pytest.mark.parametrize(
    "item",
    [
        pytest.param({"package": "x", "kind": "npm", "mode": "manual"}, id="npm-without-source"),
        pytest.param(
            {
                "package": "x",
                "kind": "github-releases",
                "mode": "snapshot",
                "pluginDir": "archify-plugin-build",
                "source": "o/r",
            },
            id="snapshot-without-asset",
        ),
        pytest.param(
            {
                "package": "x",
                "kind": "github-releases",
                "mode": "snapshot",
                "pluginDir": "archify-plugin-build",
                "source": "o/r",
                "asset": "a.zip",
            },
            id="snapshot-without-a-target-directory",
        ),
        pytest.param(
            {
                "package": "x",
                "kind": "github-releases",
                "mode": "snapshot",
                "pluginDir": "archify-plugin-build",
                "source": "o/r",
                "asset": "a.zip",
                "expectedSkills": ["a", "b"],
            },
            id="snapshot-with-two-targets",
        ),
        pytest.param(
            {
                "package": "x",
                "kind": "github-tags",
                "mode": "rebuild",
                "pluginDir": "archify-plugin-build",
                "upstream": UPSTREAM,
                "recipe": "superpowers",
            },
            id="rebuild-without-expected-skills",
        ),
        pytest.param(
            {
                "package": "x",
                "kind": "github-tags",
                "mode": "rebuild",
                "pluginDir": "archify-plugin-build",
                "recipe": "superpowers",
                "expectedSkills": ["a"],
            },
            id="rebuild-without-upstream",
        ),
    ],
)
def test_registry_refuses_incomplete_entries(item):
    with pytest.raises(RegistryError):
        _build_entry(item, WORKSPACE)


COMMIT_ENTRY = {
    "package": "x",
    "kind": "github-commits",
    "mode": "rebuild",
    "pluginDir": "archify-plugin-build",
    "recipe": "root_skill",
    "expectedSkills": ["a"],
    "source": "o/r",
}


def test_commit_kind_rebuild_is_accepted_without_a_tag_prefix():
    """A commit-tracked upstream has no tags, so tagPrefix/stripPrefix are optional."""
    entry = _build_entry({**COMMIT_ENTRY, "upstream": {"repo": "o/r", "skillsRoot": "."}}, WORKSPACE)

    assert (entry.kind, entry.mode, entry.branch) == ("github-commits", "rebuild", "main")
    assert (entry.tag_prefix, entry.strip_prefix, entry.repo) == ("", "", "o/r")


def test_commit_kind_rebuild_honours_an_explicit_branch():
    entry = _build_entry(
        {**COMMIT_ENTRY, "upstream": {"repo": "o/r", "skillsRoot": ".", "branch": "trunk"}},
        WORKSPACE,
    )

    assert entry.branch == "trunk"


def test_commit_kind_rebuild_needs_skills_root():
    """The refusal must name skillsRoot, not 'kind not implemented'."""
    with pytest.raises(RegistryError, match="skillsRoot"):
        _build_entry({**COMMIT_ENTRY, "upstream": {"repo": "o/r"}}, WORKSPACE)

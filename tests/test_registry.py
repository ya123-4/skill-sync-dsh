"""Registry loading, validation and workspace resolution."""
from pathlib import Path

import pytest

from core.registry import RegistryError, load, resolve_workspace, select

WORKSPACE = Path(__file__).resolve().parents[3]
NOWHERE = Path("C:/definitely-not-a-workspace-xyz")

# 六个 rebuild：前五个的上游是 GitHub 仓库的 tag，rea-dsh-plugin 的上游是 npm 包
# rea-agents（版本线由 optionalDependencies 钉住）。其余跟随上游发布：archify 与
# ppt-master 是 snapshot（整体替换 release 资产），剩下的是 npm manual（只报告，不落盘）。
EXPECTED_PACKAGES = [
    "agent-browser-skill-dsh",
    "vercel-skills-dsh",
    "superpowers-skill-dsh",
    "mattpocock-skills-dsh",
    "open-design-skills-dsh",
    "rea-dsh-plugin",
    "archify-skill-dsh",
    "ppt-master-skill-dsh",
    "dsh-better-sidebar",
    "dsh-mnemon",
    "dsh-whale-widget",
    "dshmarket",
]


def by_package(entries, package):
    return next(entry for entry in entries if entry.package == package)


@pytest.fixture()
def registry():
    return load(WORKSPACE)


def test_registry_lists_every_managed_package_in_order(registry):
    assert [entry.package for entry in registry] == EXPECTED_PACKAGES


def test_follow_entries_declare_a_queryable_upstream(registry):
    for entry in registry:
        if entry.is_rebuild:
            continue
        assert not entry.recipe, entry.package
        if entry.kind != "github-tags":
            assert entry.source, entry.package


def test_agent_browser_entry_fields(registry):
    entry = by_package(registry, "agent-browser-skill-dsh")
    assert (entry.repo, entry.skills_root, entry.strip_prefix, entry.tag_prefix) == (
        "vercel-labs/agent-browser",
        "skill-data",
        "agent-browser-",
        "v",
    )
    assert entry.recipe == "agent_browser"
    assert entry.commit_pinned is False


def test_open_design_entry_is_commit_pinned(registry):
    entry = by_package(registry, "open-design-skills-dsh")
    assert entry.commit_pinned is True
    assert entry.tag_prefix == "open-design-v"


def test_mattpocock_is_pinned_to_a_content_commit(registry):
    """The 1.3.1 bundle came from main, not from tag v1.3.1 (see KNOWN_DEVIATIONS.md)."""
    entry = by_package(registry, "mattpocock-skills-dsh")
    assert entry.baseline_commit == "49dd158d1076134a641b33efb035946536778336"
    assert entry.commit_pinned is True


def test_content_commits_are_recorded_where_the_bytes_need_one(registry):
    expected = {
        "mattpocock-skills-dsh": "49dd158d1076134a641b33efb035946536778336",
        "open-design-skills-dsh": "17e255959703a3fad7c8eb7f7b70dcafccc5629a",
    }
    for entry in registry:
        assert entry.baseline_commit == expected.get(entry.package), entry.package


def test_expected_skills_match_what_is_installed(registry):
    for entry in registry:
        if entry.plugin_dir is None:
            continue  # follow 型没有本地受管目录
        on_disk = {path.name for path in (entry.plugin_dir / "skills").iterdir() if path.is_dir()}
        assert set(entry.expected_skills) == on_disk, entry.package


def test_manual_skills_are_declared(registry):
    assert by_package(registry, "vercel-skills-dsh").manual_skills == ("skills-cli",)


def test_plugin_dir_resolves_under_workspace(registry):
    for entry in registry:
        if entry.plugin_dir is None:
            assert entry.mode == "manual", entry.package
            continue
        assert (entry.plugin_dir / "package.json").is_file()


def test_upstream_skill_name_strips_the_bundle_prefix(registry):
    entry = by_package(registry, "agent-browser-skill-dsh")
    assert entry.upstream_skill_name("agent-browser-core") == "core"
    assert entry.upstream_skill_name("agent-browser-protected-vercel-deployments") == (
        "protected-vercel-deployments"
    )


def test_select_filters_by_package(registry):
    chosen = select(registry, ["superpowers-skill-dsh", "vercel-skills-dsh"])
    assert [entry.package for entry in chosen] == ["superpowers-skill-dsh", "vercel-skills-dsh"]


def test_select_unknown_package_raises(registry):
    with pytest.raises(RegistryError):
        select(registry, ["no-such-package"])


def test_workspace_not_found_raises():
    with pytest.raises(RegistryError):
        resolve_workspace(NOWHERE, None)


def test_workspace_found_from_subdirectory(scratch):
    nested = scratch / "a" / "b"
    nested.mkdir(parents=True)
    assert resolve_workspace(nested, None) == WORKSPACE


def test_workspace_override_is_used(scratch):
    fake = scratch / "fake-ws"
    (fake / "dsh-plugin-build").mkdir(parents=True)
    assert resolve_workspace(NOWHERE, str(fake)) == fake.resolve()


def test_workspace_override_without_build_dir_raises(scratch):
    empty = scratch / "empty"
    empty.mkdir()
    with pytest.raises(RegistryError):
        resolve_workspace(WORKSPACE, str(empty))

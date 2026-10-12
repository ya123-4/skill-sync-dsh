"""Registry loading, validation and workspace resolution."""
from pathlib import Path

import pytest

from core.registry import RegistryError, load, resolve_workspace, select

WORKSPACE = Path(__file__).resolve().parents[3]
NOWHERE = Path("C:/definitely-not-a-workspace-xyz")

EXPECTED_PACKAGES = [
    "agent-browser-skill-dsh",
    "vercel-skills-dsh",
    "superpowers-skill-dsh",
    "mattpocock-skills-dsh",
    "open-design-skills-dsh",
]


def by_package(entries, package):
    return next(entry for entry in entries if entry.package == package)


@pytest.fixture()
def registry():
    return load(WORKSPACE)


def test_registry_has_five_entries_in_order(registry):
    assert [entry.package for entry in registry] == EXPECTED_PACKAGES


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


def test_expected_skills_match_what_is_installed(registry):
    for entry in registry:
        on_disk = {path.name for path in (entry.plugin_dir / "skills").iterdir() if path.is_dir()}
        assert set(entry.expected_skills) == on_disk, entry.package


def test_manual_skills_are_declared(registry):
    assert by_package(registry, "vercel-skills-dsh").manual_skills == ("skills-cli",)


def test_plugin_dir_resolves_under_workspace(registry):
    for entry in registry:
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

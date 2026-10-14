"""follow 型条目：npm 上游、manual 更新、以及安装提示的前置条件。

Increment spec: docs/superpowers/specs/2026-10-14-skill-sync-follow-entries.md
"""
import io
import json
from types import SimpleNamespace

import pytest

from core.registry import Entry
from core.upstream import LatestInfo, STATUS_UNKNOWN, STATUS_UPDATE_AVAILABLE
from helpers import FakeClient, fake_pack_runner, make_entry, make_upstream, provider
from skill_sync import run_check, run_update


@pytest.fixture()
def dist(scratch):
    return scratch / "dist"


def npm_entry(scratch, package="dsh-better-sidebar", version="0.25.0", source=None) -> Entry:
    """A third-party plugin installed from npm: no pluginDir, no recipe."""
    return Entry(
        package=package,
        plugin_dir=None,
        repo="",
        tag_prefix="",
        skills_root="",
        strip_prefix="",
        recipe="",
        expected_skills=(),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="third-party",
        kind="npm",
        mode="manual",
        source=source or package,
    )


def make_profile(scratch, dependencies: dict) -> "object":
    profile = scratch / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    for name, spec in dependencies.items():
        package = profile / "node_modules" / name
        package.mkdir(parents=True, exist_ok=True)
        version = spec if str(spec)[:1].isdigit() else "0.0.0"
        (package / "package.json").write_text(
            json.dumps({"name": name, "version": version}), encoding="utf-8"
        )
    (profile / "package.json").write_text(
        json.dumps({"name": "desktop", "dependencies": dependencies}), encoding="utf-8"
    )
    return profile


# ---- registry defaults ---------------------------------------------------


def test_existing_entries_stay_rebuild_shaped():
    from core import registry

    entry = make_entry_local()
    assert entry.kind == "github-tags"
    assert entry.mode == "rebuild"
    assert entry.is_rebuild
    assert entry.repo == "demo/skills"


def make_entry_local() -> Entry:
    return Entry(
        package="demo",
        plugin_dir=None,
        repo="demo/skills",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe="superpowers",
        expected_skills=(),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


# ---- npm upstream --------------------------------------------------------


def test_npm_client_reads_dist_tags_latest():
    from core.npm import NpmClient

    def http(url, headers):
        return 200, json.dumps({"dist-tags": {"latest": "0.26.0"}}).encode("utf-8")

    info = NpmClient(http=http).latest("dsh-better-sidebar")
    assert info.status == STATUS_UPDATE_AVAILABLE or info.tag == "0.26.0"
    assert info.tag == "0.26.0"


def test_npm_client_reports_a_missing_package_as_unknown():
    from core.npm import NpmClient

    def http(url, headers):
        return 404, b"{}"

    info = NpmClient(http=http).latest("nope")
    assert info.status == STATUS_UNKNOWN
    assert info.tag is None


def test_npm_client_reports_malformed_json_as_unknown():
    from core.npm import NpmClient

    def http(url, headers):
        return 200, b"not json"

    info = NpmClient(http=http).latest("broken")
    assert info.status == STATUS_UNKNOWN


def test_npm_client_reports_a_transport_failure_as_unknown():
    from core.npm import NpmClient

    def http(url, headers):
        raise OSError("network down")

    info = NpmClient(http=http).latest("flaky")
    assert info.status == STATUS_UNKNOWN
    assert info.reason


# ---- check ---------------------------------------------------------------


def test_check_reports_an_npm_entry_against_the_profile(scratch):
    profile = make_profile(scratch, {"dsh-better-sidebar": "0.25.0"})
    entry = npm_entry(scratch)

    class NpmClient:
        def latest(self, name, local=None):
            return LatestInfo(STATUS_UPDATE_AVAILABLE, "0.26.0", None, None, "npm")

    stream = io.StringIO()
    code = run_check(
        [entry], FakeClient(), profile=profile, npm_client=NpmClient(), stream=stream
    )
    text = stream.getvalue()

    assert code == 0
    assert "dsh-better-sidebar" in text and "0.26.0" in text
    assert "update available" in text


def test_check_flags_a_manual_entry_with_the_command_to_run(scratch):
    profile = make_profile(scratch, {"dsh-better-sidebar": "0.25.0"})
    entry = npm_entry(scratch)

    class NpmClient:
        def latest(self, name, local=None):
            return LatestInfo(STATUS_UPDATE_AVAILABLE, "0.26.0", None, None, "npm")

    stream = io.StringIO()
    run_check([entry], FakeClient(), profile=profile, npm_client=NpmClient(), stream=stream)

    assert "pnpm add dsh-better-sidebar@0.26.0" in stream.getvalue()


# ---- update --------------------------------------------------------------


def test_update_reports_a_manual_entry_without_touching_anything(scratch, dist):
    profile = make_profile(scratch, {"dsh-better-sidebar": "0.25.0"})
    entry = npm_entry(scratch)
    before = (profile / "package.json").read_bytes()

    class NpmClient:
        def latest(self, name, local=None):
            return LatestInfo(STATUS_UPDATE_AVAILABLE, "0.26.0", None, None, "npm")

    stream = io.StringIO()
    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        dist_dir=dist,
        profile=profile,
        npm_client=NpmClient(),
        stream=stream,
    )
    text = stream.getvalue()

    assert code == 0
    assert "manual" in text
    assert "pnpm add dsh-better-sidebar@0.26.0" in text
    assert (profile / "package.json").read_bytes() == before
    assert not dist.exists() or not list(dist.glob("*.tgz"))


# ---- install hint --------------------------------------------------------


def test_next_step_tells_you_to_quit_dsh_for_an_already_installed_bundle(scratch, dist):
    profile = make_profile(scratch, {"demo-skill-dsh": "file:old.tgz"})
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    stream = io.StringIO()
    run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        profile=profile,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )
    text = stream.getvalue()

    assert "quit DSH" in text
    assert "pnpm.cjs" in text
    assert "install_bundle" not in text
    assert "demo-skill-dsh-2.0.0.tgz" in text


def test_next_step_uses_install_bundle_for_a_bundle_not_in_the_profile(scratch, dist):
    profile = make_profile(scratch, {"something-else": "1.0.0"})
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    stream = io.StringIO()
    run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        profile=profile,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )
    text = stream.getvalue()

    assert "install_bundle" in text
    assert "quit DSH" not in text


def test_install_hint_points_the_manifest_at_the_new_tarball(scratch, dist):
    """`pnpm install` would faithfully rebuild the version being replaced: the
    profile still pins the old tarball, so the hint has to be `pnpm add file:`."""
    profile = make_profile(scratch, {"demo-skill-dsh": "file:old.tgz"})
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    stream = io.StringIO()
    run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        profile=profile,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )
    text = stream.getvalue()

    assert "point_profile.py" in text
    assert "demo-skill-dsh-2.0.0.tgz" in text
    assert " install --store-dir" in text
    assert "pnpm add" not in text


def test_point_profile_only_rewrites_that_dependency(scratch):
    """`pnpm add` re-resolves every `^` range and can trip DSH's supply-chain
    policy; pointing the manifest first keeps the install to one resolution."""
    from point_profile import point

    profile = make_profile(scratch, {"demo-skill-dsh": "file:old.tgz", "other": "^1.2.3"})
    before = json.loads((profile / "package.json").read_text(encoding="utf-8"))

    message = point(profile, "demo-skill-dsh", "file:new.tgz")

    after = json.loads((profile / "package.json").read_text(encoding="utf-8"))
    assert after["dependencies"]["demo-skill-dsh"] == "file:new.tgz"
    assert after["dependencies"]["other"] == before["dependencies"]["other"]
    assert "old.tgz" in message and "new.tgz" in message


def test_point_profile_refuses_an_unknown_package(scratch):
    from point_profile import point

    profile = make_profile(scratch, {"demo-skill-dsh": "file:old.tgz"})
    with pytest.raises(SystemExit):
        point(profile, "not-a-dependency", "file:new.tgz")


def test_check_does_not_fail_when_a_follow_entry_is_simply_not_installed(scratch):
    """An empty profile is a fact about the profile, not an upstream failure."""
    profile = make_profile(scratch, {})
    entry = npm_entry(scratch)

    class NpmClient:
        def latest(self, name, local=None):
            return LatestInfo(STATUS_UPDATE_AVAILABLE, "0.26.0", None, None, "npm")

    stream = io.StringIO()
    code = run_check(
        [entry], FakeClient(), profile=profile, npm_client=NpmClient(), stream=stream
    )

    assert code == 0
    assert "not installed" in stream.getvalue()


def test_update_reports_an_unreachable_npm_registry_as_an_upstream_failure(scratch):
    profile = make_profile(scratch, {"dsh-better-sidebar": "0.25.0"})
    entry = npm_entry(scratch)

    class NpmClient:
        def latest(self, name, local=None):
            return LatestInfo(STATUS_UNKNOWN, None, None, "network down", "npm")

    stream = io.StringIO()
    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        dist_dir=scratch / "dist",
        profile=profile,
        npm_client=NpmClient(),
        stream=stream,
    )

    assert code == 3, stream.getvalue()
    assert "network down" in stream.getvalue()


def test_verify_has_nothing_to_check_for_a_follow_entry(scratch):
    from skill_sync import _verify_entry

    entry = npm_entry(scratch)
    assert entry.plugin_dir is None
    assert _verify_entry(entry, scratch / "dist", None) == []

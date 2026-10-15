"""Upstream API queries: newest matching tag or head commit, caching, failure classification."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from core.registry import Entry
from core.upstream import GithubClient, local_version


def make_entry(
    scratch: Path,
    version: str = "0.38.2",
    tag_prefix: str = "v",
    commit_pinned: bool = False,
    package: str = "demo-skill-dsh",
    repo: str = "demo/skills",
) -> Entry:
    plugin = scratch / package
    (plugin / "skills").mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": package, "version": version}), encoding="utf-8"
    )
    return Entry(
        package=package,
        plugin_dir=plugin,
        repo=repo,
        tag_prefix=tag_prefix,
        skills_root="skills",
        strip_prefix="",
        recipe="demo",
        expected_skills=("demo",),
        manual_skills=(),
        commit_pinned=commit_pinned,
        license="MIT",
        notes="",
    )


def github_http(release=None, tags=None, release_status: int = 200, tags_status: int = 200):
    """Fake GitHub API that routes by URL and records nothing."""

    def _http(url: str, headers: dict):
        if url.endswith("/releases/latest"):
            body = json.dumps(release if release is not None else {})
            return release_status, body.encode("utf-8")
        if "/tags" in url:
            body = json.dumps(tags if tags is not None else [])
            return tags_status, body.encode("utf-8")
        if "/commits/" in url:
            return 200, json.dumps({"sha": "deadbeef"}).encode("utf-8")
        raise AssertionError(f"unexpected url: {url}")

    return _http


@pytest.fixture()
def cache_dir(scratch: Path) -> Path:
    return scratch / "cache"


def test_local_version_is_read_from_the_plugin_manifest(scratch):
    assert local_version(make_entry(scratch, version="6.4.2")) == "6.4.2"


def test_newer_release_reports_update_available(scratch, cache_dir):
    entry = make_entry(scratch, version="0.38.2")
    client = GithubClient(http=github_http(release={"tag_name": "v0.39.0"}), cache_dir=cache_dir)
    info = client.latest(entry)
    assert (info.status, info.tag) == ("update_available", "v0.39.0")


def test_equal_release_reports_up_to_date(scratch, cache_dir):
    entry = make_entry(scratch, version="0.38.2")
    client = GithubClient(http=github_http(release={"tag_name": "v0.38.2"}), cache_dir=cache_dir)
    assert client.latest(entry).status == "up_to_date"


def test_rate_limit_is_unknown(scratch, cache_dir):
    entry = make_entry(scratch)
    client = GithubClient(
        http=github_http(release_status=403, tags_status=403), cache_dir=cache_dir
    )
    info = client.latest(entry)
    assert info.status == "unknown"
    assert "rate limit" in info.reason.lower()


def test_missing_repository_is_unknown_and_says_renamed(scratch, cache_dir):
    entry = make_entry(scratch)
    client = GithubClient(
        http=github_http(release_status=404, tags_status=404), cache_dir=cache_dir
    )
    info = client.latest(entry)
    assert info.status == "unknown"
    assert "renamed" in info.reason.lower() or "not found" in info.reason.lower()


def test_timeout_is_unknown(scratch, cache_dir):
    def _http(url, headers):
        raise TimeoutError("timed out")

    info = GithubClient(http=_http, cache_dir=cache_dir).latest(make_entry(scratch))
    assert info.status == "unknown"
    assert "TimeoutError" in info.reason


def test_foreign_tags_are_filtered_out(scratch, cache_dir):
    entry = make_entry(
        scratch, version="1.3.1", package="mattpocock-skills-dsh", repo="mattpocock/skills"
    )
    http = github_http(
        release_status=404,
        tags=[{"name": "mattpocock-skills@1.0.0"}, {"name": "v1.3.1"}],
    )
    info = GithubClient(http=http, cache_dir=cache_dir).latest(entry)
    assert (info.status, info.tag) == ("up_to_date", "v1.3.1")


def test_only_non_release_tags_is_unknown(scratch, cache_dir):
    entry = make_entry(scratch, tag_prefix="open-design-v")
    http = github_http(release_status=404, tags=[{"name": "pr-3706-verification-assets"}])
    info = GithubClient(http=http, cache_dir=cache_dir).latest(entry)
    assert info.status == "unknown"
    assert info.tag is None


def test_cache_hit_avoids_a_second_request(scratch, cache_dir):
    entry = make_entry(scratch, version="0.38.2")
    calls = []

    def _http(url, headers):
        calls.append(url)
        return 200, json.dumps({"tag_name": "v0.39.0"}).encode("utf-8")

    first = GithubClient(http=_http, cache_dir=cache_dir)
    assert first.latest(entry).status == "update_available"
    second = GithubClient(http=_http, cache_dir=cache_dir)
    assert second.latest(entry).status == "update_available"
    assert len(calls) == 1


def test_refresh_bypasses_the_cache(scratch, cache_dir):
    entry = make_entry(scratch, version="0.38.2")
    calls = []

    def _http(url, headers):
        calls.append(url)
        return 200, json.dumps({"tag_name": "v0.39.0"}).encode("utf-8")

    GithubClient(http=_http, cache_dir=cache_dir).latest(entry)
    refreshed = GithubClient(http=_http, cache_dir=cache_dir, refresh=True)
    refreshed.latest(entry)
    assert len(calls) == 2


def test_offline_without_cache_is_unknown(scratch, cache_dir):
    def _http(url, headers):
        raise AssertionError("offline mode must not touch the network")

    info = GithubClient(http=_http, cache_dir=cache_dir, offline=True).latest(make_entry(scratch))
    assert info.status == "unknown"
    assert info.source == "cache"


def test_token_is_sent_but_never_leaked(scratch, cache_dir):
    seen = {}

    def _http(url, headers):
        seen.update(headers)
        return 200, json.dumps({"tag_name": "v0.39.0"}).encode("utf-8")

    info = GithubClient(http=_http, cache_dir=cache_dir, token="ghp_secret_value").latest(
        make_entry(scratch)
    )
    assert seen.get("Authorization") == "Bearer ghp_secret_value"
    assert "ghp_secret_value" not in json.dumps(info.__dict__)


def test_commit_pinned_entry_reports_head_commit(scratch, cache_dir):
    entry = make_entry(
        scratch,
        version="0.24.1",
        tag_prefix="open-design-v",
        commit_pinned=True,
        package="open-design-skills-dsh",
        repo="nexu-io/open-design",
    )
    http = github_http(release={"tag_name": "open-design-v0.24.1"})
    info = GithubClient(http=http, cache_dir=cache_dir).latest(entry)
    assert info.tag == "open-design-v0.24.1"
    assert info.commit == "deadbeef"


# ---- commit-tracked upstreams (kind: github-commits) --------------------
#
# An upstream with no tags has no version to compare: the newest commit on its
# branch is the release, and the baseline is the revision recorded in the
# bundle's own state file. A missing baseline is unknown, never "current".


def commit_entry(scratch, synced_commit=None, package="humanizer-zh-skill-dsh"):
    entry = replace(
        make_entry(
            scratch,
            version="0.1.0",
            tag_prefix="",
            package=package,
            repo="op7418/Humanizer-zh",
        ),
        kind="github-commits",
        branch="main",
    )
    if synced_commit is not None:
        (entry.plugin_dir / ".skill-sync-state.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "package": package,
                    "upstream": entry.repo,
                    "syncedTag": "0.1.0",
                    "syncedCommit": synced_commit,
                    "syncedAt": "2026-10-15T00:00:00+00:00",
                    "overlayVersion": 1,
                    "skills": {},
                }
            ),
            encoding="utf-8",
        )
    return entry


def commit_http(sha="b" * 40, status=200):
    def _http(url, headers):
        return status, json.dumps({"sha": sha}).encode("utf-8")

    return _http


def test_commit_kind_same_revision_is_up_to_date(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)
    info = GithubClient(http=commit_http(sha="a" * 40), cache_dir=cache_dir).latest(entry)

    assert (info.status, info.tag, info.commit) == ("up_to_date", None, "a" * 40)


def test_commit_kind_new_revision_is_update_available(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)
    info = GithubClient(http=commit_http(sha="b" * 40), cache_dir=cache_dir).latest(entry)

    assert (info.status, info.commit) == ("update_available", "b" * 40)


def test_commit_kind_without_a_recorded_revision_is_unknown(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit=None)
    info = GithubClient(http=commit_http(), cache_dir=cache_dir).latest(entry)

    assert info.status == "unknown"
    assert "no local revision" in (info.reason or "")


def test_commit_kind_with_a_corrupt_record_is_unknown(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)
    (entry.plugin_dir / ".skill-sync-state.json").write_text("{not json", encoding="utf-8")

    info = GithubClient(http=commit_http(), cache_dir=cache_dir).latest(entry)

    assert info.status == "unknown"
    assert "no local revision" in (info.reason or "")


def test_commit_kind_rate_limit_is_unknown(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)
    info = GithubClient(http=commit_http(status=403), cache_dir=cache_dir).latest(entry)

    assert info.status == "unknown"
    assert "rate limit" in (info.reason or "").lower()


def test_commit_kind_missing_repository_is_unknown(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)
    info = GithubClient(http=commit_http(status=404), cache_dir=cache_dir).latest(entry)

    assert info.status == "unknown"
    reason = (info.reason or "").lower()
    assert "not found" in reason or "renamed" in reason


def test_commit_kind_uses_the_branch_in_the_entry(scratch, cache_dir):
    entry = replace(commit_entry(scratch, synced_commit="a" * 40), branch="trunk")
    seen = []

    def _http(url, headers):
        seen.append(url)
        return 200, json.dumps({"sha": "a" * 40}).encode("utf-8")

    GithubClient(http=_http, cache_dir=cache_dir).latest(entry)

    assert seen and seen[0].endswith("/commits/trunk")


def test_commit_kind_offline_without_cache_is_unknown(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)

    def _http(url, headers):
        raise AssertionError("offline mode must not touch the network")

    info = GithubClient(http=_http, cache_dir=cache_dir, offline=True).latest(entry)

    assert (info.status, info.source) == ("unknown", "cache")


def test_commit_kind_never_serves_a_cached_revision(scratch, cache_dir):
    """A commit moves, unlike a tag: a fresh cache must not hide a moved head.

    The tag path may answer from its 10-minute cache because a tag cannot move.
    A branch head can, and the value would be landed by ``update`` -- so a
    commit-tracked bundle asks upstream every time it is online.
    """
    entry = commit_entry(scratch, synced_commit="a" * 40)
    responses = iter(["b" * 40, "c" * 40])
    calls = []

    def _http(url, headers):
        calls.append(url)
        return 200, json.dumps({"sha": next(responses)}).encode("utf-8")

    first = GithubClient(http=_http, cache_dir=cache_dir)
    assert first.latest(entry).commit == "b" * 40
    second = GithubClient(http=_http, cache_dir=cache_dir)
    assert second.latest(entry).commit == "c" * 40
    assert len(calls) == 2


def test_commit_kind_offline_uses_the_cached_revision(scratch, cache_dir):
    entry = commit_entry(scratch, synced_commit="a" * 40)

    def _http(url, headers):
        return 200, json.dumps({"sha": "b" * 40}).encode("utf-8")

    GithubClient(http=_http, cache_dir=cache_dir).latest(entry)

    def _offline(url, headers):
        raise AssertionError("offline mode must not touch the network")

    info = GithubClient(http=_offline, cache_dir=cache_dir, offline=True).latest(entry)
    assert (info.status, info.commit) == ("update_available", "b" * 40)

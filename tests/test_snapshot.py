"""Pinned upstream snapshot download and extraction.

Tarballs are built in-memory by the tests, so no binary fixtures are committed
and every failure mode (corrupt gzip, no wrapper directory, HTTP error) is
reproducible offline.
"""
import io
import json
import tarfile

import pytest

from core.registry import Entry
from core.upstream import SnapshotError, fetch_snapshot, snapshot_url


def make_entry(scratch, package="demo-skill-dsh", repo="demo/skills") -> Entry:
    plugin = scratch / package
    (plugin / "skills").mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": package, "version": "1.0.0"}), encoding="utf-8"
    )
    return Entry(
        package=package,
        plugin_dir=plugin,
        repo=repo,
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


def build_tarball(top="mini-1.0.0", entries=None) -> bytes:
    """A gzip tarball shaped like codeload's output."""
    payload = entries if entries is not None else {"skills/foo/SKILL.md": "---\nname: foo\n---\n"}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for relative, text in payload.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(f"{top}/{relative}" if top else relative)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def serving(payload: bytes, status: int = 200, calls: list | None = None):
    def _http(url, headers):
        if calls is not None:
            calls.append(url)
        return status, payload

    return _http


def test_snapshot_url_uses_tags_for_versions_and_bare_ref_for_commits():
    assert snapshot_url("a/b", "v1.0.0") == "https://codeload.github.com/a/b/tar.gz/refs/tags/v1.0.0"
    sha = "17e255959703a3fad7c8eb7f7b70dcafccc5629a"
    assert snapshot_url("a/b", sha) == f"https://codeload.github.com/a/b/tar.gz/{sha}"


def test_fetch_downloads_and_extracts(scratch):
    root = fetch_snapshot(make_entry(scratch), "v1.0.0", scratch / "cache", serving(build_tarball()))
    assert root.is_dir()
    assert root.name == "mini-1.0.0"
    assert (root / "skills/foo/SKILL.md").is_file()


def test_fetch_reuses_cache_without_downloading_again(scratch):
    entry = make_entry(scratch)
    cache = scratch / "cache"
    calls: list = []
    fetch_snapshot(entry, "v1.0.0", cache, serving(build_tarball(), calls=calls))
    fetch_snapshot(entry, "v1.0.0", cache, serving(build_tarball(), calls=calls))
    assert len(calls) == 1


def test_cached_snapshot_needs_no_http(scratch):
    entry = make_entry(scratch)
    cache = scratch / "cache"
    fetch_snapshot(entry, "v1.0.0", cache, serving(build_tarball()))

    def exploding(url, headers):
        raise AssertionError("a cache hit must not download")

    assert fetch_snapshot(entry, "v1.0.0", cache, exploding).is_dir()


def test_corrupt_tarball_raises(scratch):
    with pytest.raises(SnapshotError):
        fetch_snapshot(make_entry(scratch), "v1.0.0", scratch / "cache", serving(b"not a tarball"))


def test_tarball_without_wrapper_directory_raises(scratch):
    payload = build_tarball(top="", entries={"SKILL.md": "no wrapper directory here"})
    with pytest.raises(SnapshotError):
        fetch_snapshot(make_entry(scratch), "v1.0.0", scratch / "cache", serving(payload))


def test_http_error_raises_snapshot_error(scratch):
    with pytest.raises(SnapshotError):
        fetch_snapshot(make_entry(scratch), "v1.0.0", scratch / "cache", serving(b"", status=404))


def test_failed_download_leaves_no_partial_cache(scratch):
    entry = make_entry(scratch)
    cache = scratch / "cache"
    with pytest.raises(SnapshotError):
        fetch_snapshot(entry, "v1.0.0", cache, serving(b"not a tarball"))

    # A later good download must succeed rather than trip over the corpse.
    root = fetch_snapshot(entry, "v1.0.0", cache, serving(build_tarball()))
    assert (root / "skills/foo/SKILL.md").is_file()

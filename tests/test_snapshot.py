"""Pinned upstream snapshot download and extraction.

Tarballs are built in-memory by the tests, so no binary fixtures are committed
and every failure mode (corrupt gzip, no wrapper directory, HTTP error) is
reproducible offline.
"""
import dataclasses
import importlib.util
import io
import json
import os
import shutil
import tarfile
from pathlib import Path

import pytest

from core.registry import Entry
from core.upstream import SnapshotError, fetch_snapshot, snapshot_url

PKG_ROOT = Path(__file__).resolve().parent.parent


def longest_path_exists(path: Path) -> bool:
    """Whether ``path`` exists -- asked in a way that can answer past MAX_PATH.

    The trees under test are deliberately deeper than Windows can address without
    an extended-length prefix, so a probe written with the plain path would report
    "absent" for a directory that is merely unreachable, and every assertion below
    would pass vacuously. On POSIX there is no such limit and the plain probe is the
    answer.
    """
    if os.name != "nt":  # pragma: no cover - POSIX has no such limit
        return path.exists()

    from core.upstream import _long_path  # noqa: PLC0415 - needs conftest's sys.path

    try:
        return Path(_long_path(path)).exists()
    except OSError:
        # The path is past MAX_PATH and not prefixed: it exists but cannot be
        # addressed, which is exactly the state this probe reports as present.
        return True


def load_fetch_snapshots_module():
    """The shipped script as a module, exactly as callers import it."""
    spec = importlib.util.spec_from_file_location(
        "fetch_snapshots", PKG_ROOT / "tools" / "fetch_snapshots.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_transient_transport_failure_is_retried(scratch):
    import http.client

    calls: list = []

    def flaky(url, headers):
        calls.append(url)
        if len(calls) < 3:
            raise http.client.IncompleteRead(b"partial")  # seen on this host's TLS middlebox
        return 200, build_tarball()

    root = fetch_snapshot(
        make_entry(scratch), "v1.0.0", scratch / "cache", flaky, sleep=lambda _: None
    )
    assert len(calls) == 3
    assert (root / "skills/foo/SKILL.md").is_file()


def test_missing_release_is_not_retried(scratch):
    calls: list = []
    with pytest.raises(SnapshotError):
        fetch_snapshot(
            make_entry(scratch),
            "v1.0.0",
            scratch / "cache",
            serving(b"", status=404, calls=calls),
            sleep=lambda _: None,
        )
    assert len(calls) == 1


def test_deeply_nested_members_extract_on_a_long_path_host(scratch):
    """本机 LongPathsEnabled=0；成员名长到普通路径必炸时仍要能取出文件。

    The member below lands at ~332 characters under the real ``scratch`` root
    (~106 characters) -- past MAX_PATH, and past the 292-character ``SKILL.md``
    the alibabacloud bundle needs -- so a plain path cannot even be stat'd. The
    returned checkout must still be usable: ``checkout / <member>`` has to
    resolve, which is what the alibabacloud recipe does with its ``SKILL.md``.
    """
    deep = "/".join(["d" * 20] * 8) + "/SKILL.md"        # 176-character member path
    payload = build_tarball(entries={deep: "deep body"})
    root = fetch_snapshot(make_entry(scratch), "v1.0.0", scratch / "cache", serving(payload))
    extracted = root / deep.replace("/", os.sep)
    assert root.is_dir()
    assert extracted.is_file()
    assert extracted.read_bytes() == b"deep body"


def test_removing_a_cache_root_takes_its_long_descendants_with_it(scratch):
    """`_remove_tree` must reach past MAX_PATH, or a retry keeps the old corpse.

    The root stays short (~144 characters) while its descendants do not, which is
    exactly the shape of a real cache entry: a plain ``rmtree`` removes what it can
    reach, gives up at the first long directory and leaves the rest behind, so the
    next attempt would extract over a half-deleted tree.
    """
    from core.upstream import _long_path, _remove_tree

    root = scratch / "cache" / "snapshots" / "demo__skills__v1.0.0"
    deep = root / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20)
    Path(_long_path(deep)).mkdir(parents=True)

    _remove_tree(root)

    assert not root.exists()
    assert not Path(_long_path(deep)).exists()


def test_refresh_discards_a_deep_cached_snapshot(scratch):
    """``--refresh`` over a deep cache entry must land a fresh checkout.

    The entry is deep *because* extraction now works: the alibabacloud monorepo
    reaches 292 characters under the real cache root, while ``LongPathsEnabled`` is
    0 here. ``tools/fetch_snapshots.py --refresh`` deletes the cached entry before
    re-fetching, and a plain ``rmtree`` aborts at the first descendant past
    MAX_PATH: the ``.complete`` marker survives, the cache-hit branch of
    ``_fetch_tarball`` sees it, and ``--refresh`` reports success while serving the
    old bytes. The deep file below is the part only an extended-length walk can
    remove, so this test fails against the plain-``rmtree`` refresh and passes
    against the shipped one.
    """
    from core.upstream import _long_path  # noqa: PLC0415 - needs conftest's sys.path

    module = load_fetch_snapshots_module()
    entry = make_entry(scratch)
    cache = scratch / "cache"
    slug = "demo__skills__v1.0.0"

    root = cache / "snapshots" / slug
    (root / "mini-1.0.0" / "skills" / "foo").mkdir(parents=True)
    (root / "mini-1.0.0" / "skills" / "foo" / "SKILL.md").write_text("stale", encoding="utf-8")
    (root / ".complete").write_text("demo/skills@v1.0.0\n", encoding="utf-8")

    deep = root / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20)
    Path(_long_path(deep)).mkdir(parents=True)
    (Path(_long_path(deep)) / "deep.md").write_text("stale", encoding="utf-8")

    # The entry under test is short while its descendants are not, and the deep path
    # only proves anything while it is genuinely past MAX_PATH.
    assert len(str(root)) < 260, len(str(root))
    assert len(str(deep)) > 260, len(str(deep))

    removed = module.refresh_snapshot_cache(cache, slug)
    assert Path(removed) == root, removed

    fetched: list = []
    root = fetch_snapshot(entry, "v1.0.0", cache, serving(build_tarball(), calls=fetched))

    assert fetched, "a discarded cache entry must be re-downloaded, not served stale"
    assert not longest_path_exists(deep), "a deep descendant survived the refresh"
    assert (root / "skills/foo/SKILL.md").read_text(encoding="utf-8").startswith("---")


def test_refresh_flag_drops_the_deep_cache_before_it_re_fetches(scratch, monkeypatch):
    """The CLI's own ``--refresh`` path, end to end over a stubbed transport.

    The unit test above proves ``refresh_snapshot_cache`` removes deep trees; this
    one proves ``main()`` actually goes through it. The registry entry is real, so
    the slug ``main()`` builds is the slug this test caches; the fetch is stubbed
    because the real one would need the network. Under the old plain ``rmtree`` the
    deletion aborts below, the ``.complete`` marker survives, and the recorded
    search path still holds the stale checkout when the fetch is called.
    """
    from core.upstream import _long_path  # noqa: PLC0415 - needs conftest's sys.path

    module = load_fetch_snapshots_module()

    workspace = scratch / "fake-workspace"
    (workspace / "dsh-plugin-build").mkdir(parents=True)
    plugin = workspace / "dsh-plugin-build" / "demo-skill-dsh"
    (plugin / "skills").mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": "demo-skill-dsh", "version": "1.0.0"}), encoding="utf-8"
    )

    monkeypatch.setattr(
        module,
        "load",
        lambda _workspace: [
            dataclasses.replace(make_entry(scratch), plugin_dir=plugin, kind="github-tags")
        ],
    )

    slug = "demo__skills__v1.0.0"
    root = workspace / "tmpwork" / "skill-sync-upstream" / "snapshots" / slug
    (root / "mini-1.0.0").mkdir(parents=True)
    (root / "mini-1.0.0" / "SKILL.md").write_text("stale", encoding="utf-8")
    (root / ".complete").write_text("demo/skills@v1.0.0\n", encoding="utf-8")
    deep = root / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20) / ("d" * 20)
    Path(_long_path(deep)).mkdir(parents=True)
    assert len(str(deep)) > 260, len(str(deep))

    seen: list = []
    monkeypatch.setattr(module, "fetch_snapshot", lambda entry, ref, cache: seen.append(ref))

    exit_code = module.main(["--workspace", str(workspace), "--refresh"])

    assert exit_code == 0, exit_code
    assert seen == ["v1.0.0"], "the cache entry was not discarded before re-fetching"
    assert not longest_path_exists(deep), "a deep descendant survived --refresh"
    assert not (root / ".complete").is_file()


def test_persistent_transport_failure_gives_up_after_three_attempts(scratch):
    calls: list = []

    def always_fails(url, headers):
        calls.append(url)
        raise TimeoutError("no answer")

    with pytest.raises(SnapshotError):
        fetch_snapshot(
            make_entry(scratch), "v1.0.0", scratch / "cache", always_fails, sleep=lambda _: None
        )
    assert len(calls) == 3

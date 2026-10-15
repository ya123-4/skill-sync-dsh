"""update orchestration: check -> fetch -> recipe -> verify -> apply -> pack."""
import dataclasses
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import state
from core.registry import Entry
from core.upstream import (
    STATUS_UNKNOWN,
    STATUS_UP_TO_DATE,
    STATUS_UPDATE_AVAILABLE,
    GithubClient,
    LatestInfo,
)
from skill_sync import run_update


def make_entry(
    scratch,
    version="1.0.0",
    package="demo-skill-dsh",
    recipe="superpowers",
    expected=("alpha",),
) -> Entry:
    plugin = scratch / package
    (plugin / "skills" / "alpha").mkdir(parents=True, exist_ok=True)
    (plugin / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: old\n---\n\nold body\n", encoding="utf-8"
    )
    (plugin / "package.json").write_text(
        json.dumps(
            {
                "name": package,
                "version": version,
                "description": f"DeepSeek Harness bundle for demo {version}",
            }
        ),
        encoding="utf-8",
    )
    return Entry(
        package=package,
        plugin_dir=plugin,
        repo="demo/skills",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe=recipe,
        expected_skills=tuple(expected),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


def make_upstream(scratch, body="new body", skills=("alpha",), name_for=None) -> Path:
    root = scratch / "upstream"
    names = name_for or {name: name for name in skills}
    for name in skills:
        directory = root / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {names[name]}\ndescription: new\n---\n\n{body}\n", encoding="utf-8"
        )
    return root


def snapshot_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class FakeClient:
    def __init__(self, status=STATUS_UPDATE_AVAILABLE, tag="v2.0.0"):
        self.info = LatestInfo(status=status, tag=tag, commit=None, reason=None, source="test")

    def latest(self, entry):
        return self.info


def provider(root: Path):
    return lambda entry, ref: root


def fake_pack_runner(create=True, returncode=0):
    def _runner(argv, env):
        if create:
            destination = Path(argv[argv.index("--pack-destination") + 1])
            manifest = json.loads((Path(argv[2]) / "package.json").read_text(encoding="utf-8"))
            (destination / f"{manifest['name']}-{manifest['version']}.tgz").write_bytes(b"tgz")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

    return _runner


@pytest.fixture()
def dist(scratch):
    return scratch / "dist"


def test_update_dry_run_writes_nothing(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch)
    before = snapshot_tree(entry.plugin_dir)

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        dry_run=True,
        stream=io.StringIO(),
    )

    assert code == 0
    assert snapshot_tree(entry.plugin_dir) == before


def test_update_structure_change_exits_4(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, skills=("alpha", "beta"))

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        stream=io.StringIO(),
    )

    assert code == 4


def test_update_accept_structure_change_proceeds(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, skills=("alpha", "beta"))

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        accept_structure_change=True,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    assert (entry.plugin_dir / "skills" / "beta" / "SKILL.md").is_file()


def test_update_skips_an_up_to_date_bundle(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch)
    before = snapshot_tree(entry.plugin_dir)

    code = run_update(
        [entry],
        FakeClient(status=STATUS_UP_TO_DATE, tag="v1.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        stream=io.StringIO(),
    )

    assert code == 0
    assert snapshot_tree(entry.plugin_dir) == before


def test_update_rebuild_forces_a_same_version_rebuild(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="rebuilt body")

    code = run_update(
        [entry],
        FakeClient(status=STATUS_UP_TO_DATE, tag="v1.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        rebuild=True,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    text = (entry.plugin_dir / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "rebuilt body" in text


def test_update_success_bumps_version_state_and_tarball(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.0.0"
    assert "2.0.0" in manifest["description"]

    payload = state.read(entry.plugin_dir)
    assert payload is not None
    assert payload["syncedTag"] == "v2.0.0"
    assert "alpha" in payload["skills"]

    assert (dist / "demo-skill-dsh-2.0.0.tgz").is_file()
    assert "new body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")


def test_update_verify_failure_leaves_the_bundle_untouched(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, name_for={"alpha": "not-alpha"})
    before = snapshot_tree(entry.plugin_dir)

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 5
    assert snapshot_tree(entry.plugin_dir) == before


def test_update_pack_failure_rolls_the_bundle_back(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")
    before = snapshot_tree(entry.plugin_dir)

    code = run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(returncode=1),
        stream=io.StringIO(),
    )

    assert code == 6
    assert snapshot_tree(entry.plugin_dir) == before


def test_update_reports_the_install_command(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch)
    stream = io.StringIO()

    run_update(
        [entry],
        FakeClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )

    output = stream.getvalue()
    assert "demo-skill-dsh-2.0.0.tgz" in output
    assert "install" in output.lower()


def test_update_rebuild_pinned_to_a_commit_keeps_state_comparable(scratch, dist):
    """A commit ref must not be recorded as the tag; verify compares tags to versions."""
    import dataclasses

    entry = dataclasses.replace(make_entry(scratch, version="1.0.0"), baseline_commit="a" * 40)
    upstream = make_upstream(scratch)

    code = run_update(
        [entry],
        FakeClient(status=STATUS_UP_TO_DATE, tag="v1.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        rebuild=True,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    payload = state.read(entry.plugin_dir)
    assert payload["syncedTag"] == "v1.0.0"
    assert payload["syncedCommit"] == "a" * 40


# ---- commit-tracked bundles (kind: github-commits) ----------------------


class CommitClient:
    """A commit-driven entry has no tag: the revision is the whole signal."""

    def __init__(self, status=STATUS_UPDATE_AVAILABLE, commit="b" * 40):
        self.info = LatestInfo(status=status, tag=None, commit=commit, reason=None, source="test")

    def latest(self, entry):
        return self.info


def commit_entry(scratch, version="0.1.0", name="humanizer-zh"):
    """A managed bundle whose *installed* skill already carries the target name.

    Two things have to line up before the commit path is even reached:
    ``verify_candidate`` compares the staged tree against the installed directory
    names (or it reports a structure change, exit 4) and each ``SKILL.md`` must
    declare the name its directory has (or it reports name-mismatch, exit 5).
    Fixtures are written as explicit bytes: ``write_text`` would emit CRLF on
    Windows and the recipe's frontmatter check reads for a bare ``---\\n``.
    """
    entry = make_entry(
        scratch,
        version=version,
        package="humanizer-zh-skill-dsh",
        recipe="root_skill",
        expected=(name,),
    )
    skill = entry.plugin_dir / "skills" / "alpha"
    skill.rename(entry.plugin_dir / "skills" / name)
    skill = entry.plugin_dir / "skills" / name
    (skill / "SKILL.md").write_bytes(
        f"---\nname: {name}\ndescription: old\n---\n\nold body\n".encode("utf-8")
    )
    return dataclasses.replace(entry, kind="github-commits", tag_prefix="", skills_root=".")


def root_upstream(scratch, name="humanizer-zh", body="new body"):
    root = scratch / "upstream"
    root.mkdir(exist_ok=True)
    (root / "SKILL.md").write_bytes(
        f"---\nname: {name}\ndescription: new\n---\n\n{body}\n".encode("utf-8")
    )
    return root


def test_short_revision_truncates_a_sha():
    from core.versions import short_revision

    assert short_revision("f4518a8eab97b8bfebc66a89d34320a89bef6930") == "f4518a8"
    assert short_revision("no state") == "no state"


def test_update_commit_kind_lands_the_sha_it_queried(scratch, dist):
    entry = commit_entry(scratch)
    upstream = root_upstream(scratch)

    code = run_update(
        [entry],
        CommitClient(commit="b" * 40),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.1.1"
    payload = state.read(entry.plugin_dir)
    assert payload["syncedTag"] == "0.1.1"
    assert payload["syncedCommit"] == "b" * 40
    assert (dist / "humanizer-zh-skill-dsh-0.1.1.tgz").is_file()
    text = (entry.plugin_dir / "skills" / "humanizer-zh" / "SKILL.md").read_text(encoding="utf-8")
    assert "new body" in text


def test_update_commit_kind_without_a_baseline_is_unknown(scratch, dist):
    entry = commit_entry(scratch)
    before = snapshot_tree(entry.plugin_dir)

    code = run_update(
        [entry],
        CommitClient(status=STATUS_UNKNOWN),
        scratch,
        upstream_provider=provider(root_upstream(scratch)),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 3  # EXIT_UPSTREAM: unknown is never "current"
    assert snapshot_tree(entry.plugin_dir) == before


def test_update_commit_kind_rebuild_replays_the_installed_revision(scratch, dist):
    """``--rebuild`` replays what is installed, not the bootstrap baseline.

    When the two differ, preferring ``baselineCommit`` quietly rewinds the bundle
    to the bytes of its very first build -- and every check stays green, because
    the state is rewritten to match the rewind.
    """
    entry = dataclasses.replace(commit_entry(scratch), baseline_commit="c" * 40)
    state.write(
        entry.plugin_dir,
        {
            "package": entry.package,
            "upstream": entry.repo,
            "syncedTag": "0.1.1",
            "syncedCommit": "a" * 40,
            "syncedAt": "2026-10-15T00:00:00+00:00",
            "overlayVersion": 1,
            "skills": {},
        },
    )

    code = run_update(
        [entry],
        CommitClient(status=STATUS_UP_TO_DATE),
        scratch,
        upstream_provider=provider(root_upstream(scratch, body="rebuilt body")),
        dist_dir=dist,
        rebuild=True,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 0
    assert state.read(entry.plugin_dir)["syncedCommit"] == "a" * 40
    text = (entry.plugin_dir / "skills" / "humanizer-zh" / "SKILL.md").read_text(encoding="utf-8")
    assert "rebuilt body" in text


def test_update_commit_kind_queries_upstream_instead_of_a_stale_cache(scratch, dist):
    """The revision update lands must be one it asked for itself.

    A *fresh* cache is the trap: `check` may have filled it minutes ago, and the
    branch head can move in between. Landing that cached revision means landing
    bytes update never looked at -- and the cached sha may already be unreachable
    server-side. Zero HTTP calls is the failure signature.
    """
    entry = commit_entry(scratch)
    state.write(
        entry.plugin_dir,
        {
            "package": entry.package,
            "upstream": entry.repo,
            "syncedTag": "0.1.0",
            "syncedCommit": "a" * 40,
            "syncedAt": "2026-10-15T00:00:00+00:00",
            "overlayVersion": 1,
            "skills": {},
        },
    )
    cache = scratch / "cache"
    (cache / "api").mkdir(parents=True)
    (cache / "api" / f"{entry.repo.replace('/', '__')}.json").write_text(
        json.dumps(
            {
                "repo": entry.repo,
                "tag": "",
                "commit": "9" * 40,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def _http(url, headers):
        calls.append(url)
        return 200, json.dumps({"sha": "b" * 40}).encode("utf-8")

    code = run_update(
        [entry],
        GithubClient(http=_http, cache_dir=cache),
        scratch,
        upstream_provider=provider(root_upstream(scratch)),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert calls, "update must ask upstream rather than trust a cached revision"
    assert code == 0
    assert state.read(entry.plugin_dir)["syncedCommit"] == "b" * 40

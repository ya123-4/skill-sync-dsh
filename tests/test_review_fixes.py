"""Fixes required by the whole-branch review of 2026-10-13.

Each test here pins one finding: a false "up to date" line, a release tag that
skips the prefix filter, a stranded tarball, an interrupt that skips rollback, a
name collision that never gates a landing, and version references left behind in
the bundle's own docs.
"""
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import state
from core.apply import ApplyFailed, apply_candidate
from core.pack import PackFailed, pack
from core.registry import Entry
from core.upstream import STATUS_UNKNOWN, STATUS_UP_TO_DATE, GithubClient, LatestInfo
from skill_sync import default_upstream_provider, run_update, run_verify


# ---- helpers (deliberately local; the review tests must stand alone) ----


def make_entry(
    scratch,
    version="1.0.0",
    package="demo-skill-dsh",
    recipe="superpowers",
    expected=("alpha",),
    baseline_commit=None,
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
        baseline_commit=baseline_commit,
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


class FakeClient:
    def __init__(self, status=STATUS_UP_TO_DATE, tag="v1.0.0", reason=None, commit=None):
        self.info = LatestInfo(status=status, tag=tag, commit=commit, reason=reason, source="test")

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


# ---- C1: unknown must never be reported as "already up to date" ----


def test_update_reports_unknown_instead_of_claiming_up_to_date(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch)
    stream = io.StringIO()

    code = run_update(
        [entry],
        FakeClient(status=STATUS_UNKNOWN, tag=None, reason="GitHub API rate limit reached"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        stream=stream,
    )

    output = stream.getvalue()
    assert "already up to date" not in output.lower()
    assert "rate limit" in output
    assert code == 3


# ---- I1: the release path must honour the tag prefix and fall back to tags ----


def test_release_tag_without_the_prefix_falls_back_to_tags(scratch):
    entry = make_entry(scratch, package="mattpocock-skills-dsh", version="1.3.1")

    def _http(url, headers):
        if url.endswith("/releases/latest"):
            return 200, json.dumps({"tag_name": "mattpocock-skills@1.0.0"}).encode("utf-8")
        if "/tags" in url:
            return 200, json.dumps([{"name": "v1.4.0"}, {"name": "v1.3.1"}]).encode("utf-8")
        raise AssertionError(url)

    info = GithubClient(http=_http, cache_dir=None).latest(entry)

    assert info.tag == "v1.4.0"
    assert info.status == "update_available"


def test_tag_fallback_picks_the_numerically_greatest(scratch):
    entry = make_entry(scratch, version="1.3.1")

    def _http(url, headers):
        if url.endswith("/releases/latest"):
            return 404, b"{}"
        if "/tags" in url:
            return 200, json.dumps(
                [{"name": "v1.9.0"}, {"name": "v1.10.0"}, {"name": "v1.3.1"}]
            ).encode("utf-8")
        raise AssertionError(url)

    info = GithubClient(http=_http, cache_dir=None).latest(entry)

    assert info.tag == "v1.10.0"


# ---- I2: a pinned recipe must not silently rebuild old material for a new ref ----


def test_open_design_provider_refuses_an_uncached_ref(scratch):
    from core.upstream import SnapshotError

    entry = make_entry(
        scratch, package="open-design-skills-dsh", recipe="open_design", baseline_commit="a" * 40
    )
    (scratch / "tmpwork" / "od-source" / ("a" * 40)).mkdir(parents=True)

    resolved = default_upstream_provider(scratch)

    assert resolved(entry, "a" * 40) == scratch / "tmpwork" / "od-source" / ("a" * 40)
    with pytest.raises(SnapshotError):
        resolved(entry, "b" * 40)


# ---- I3: a failed pack must put the previous tarball back ----


def test_pack_failure_restores_the_previous_tarball(scratch):
    entry = make_entry(scratch)
    dist = scratch / "dist"
    dist.mkdir()
    stale = dist / "demo-skill-dsh-1.0.0.tgz"
    stale.write_bytes(b"old tarball")

    with pytest.raises(PackFailed):
        pack(entry.plugin_dir, dist, runner=fake_pack_runner(create=False, returncode=1))

    assert stale.read_bytes() == b"old tarball"
    assert not (dist / ".previous" / "demo-skill-dsh-1.0.0.tgz").exists()


# ---- I4: an interrupt during landing must roll back, not leave a hole ----


def test_apply_rolls_back_on_interrupt(scratch):
    entry = make_entry(scratch)
    staging = scratch / "staging" / "skills" / "alpha"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nnew body\n", encoding="utf-8"
    )
    before = {
        path.relative_to(entry.plugin_dir).as_posix(): path.read_bytes()
        for path in entry.plugin_dir.rglob("*")
        if path.is_file()
    }

    with pytest.raises(KeyboardInterrupt):
        apply_candidate(
            entry.plugin_dir,
            scratch / "staging" / "skills",
            "2.0.0",
            fail_after=1,
            fail_with=KeyboardInterrupt,
        )

    after = {
        path.relative_to(entry.plugin_dir).as_posix(): path.read_bytes()
        for path in entry.plugin_dir.rglob("*")
        if path.is_file()
    }
    assert after == before


# ---- I5: a name collision with another installed bundle must gate a landing ----


def test_update_blocks_on_a_profile_name_collision(scratch, dist):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch)
    profile = scratch / "profile"
    other = profile / "node_modules" / "other-skill-dsh" / "skills" / "alpha"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n\nx\n", encoding="utf-8")

    code = run_update(
        [entry],
        FakeClient(tag="v2.0.0", status="update_available"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        profile=profile,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 5
    assert not (dist / "demo-skill-dsh-2.0.0.tgz").exists()


# ---- I6: the bundle's own docs must not keep claiming the old version ----


def test_apply_updates_version_references_in_docs(scratch):
    entry = make_entry(scratch, version="1.0.0")
    (entry.plugin_dir / "README.md").write_text(
        "Bundle for demo v1.0.0.\n", encoding="utf-8"
    )
    (entry.plugin_dir / "THIRD_PARTY_NOTICES.md").write_text(
        "Upstream version: 1.0.0\n", encoding="utf-8"
    )
    staging = scratch / "staging" / "skills" / "alpha"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nnew body\n", encoding="utf-8"
    )

    apply_candidate(entry.plugin_dir, scratch / "staging" / "skills", "2.0.0")

    assert "v2.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")
    assert "1.0.0" not in (entry.plugin_dir / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )


# ---- M3: a manifest whose version cannot be rewritten must not land at all ----


def test_apply_refuses_when_the_version_cannot_be_rewritten(scratch):
    """Before this fix a reformatted manifest landed silently with the old version."""
    entry = make_entry(scratch, version="1.0.0")
    manifest = entry.plugin_dir / "package.json"
    manifest.write_text(
        '{"name": "demo-skill-dsh","version":"1.0.0","description":"demo 1.0.0"}',
        encoding="utf-8",
    )
    staging = scratch / "staging" / "skills" / "alpha"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nnew body\n", encoding="utf-8"
    )

    with pytest.raises(ApplyFailed):
        apply_candidate(entry.plugin_dir, scratch / "staging" / "skills", "2.0.0")

    text = manifest.read_text(encoding="utf-8")
    assert "1.0.0" in text
    assert "2.0.0" not in text


# ---- M4: an unmapped exception must not escape as exit code 1 ----


def test_cli_maps_unexpected_errors_to_exit_2(monkeypatch, capsys):
    import skill_sync

    def boom(workspace):
        raise ValueError("registry exploded")

    monkeypatch.setattr(skill_sync.registry, "load", boom)

    code = skill_sync.main(["check", "--offline"])

    captured = capsys.readouterr()
    assert code == 2
    assert "ValueError" in captured.err
    assert "registry exploded" in captured.err


# ---- M2: verify must see a skill directory the state file never recorded ----


def test_verify_reports_a_skill_directory_added_by_hand(scratch):
    entry = make_entry(scratch)
    state.write(
        entry.plugin_dir,
        {
            "package": entry.package,
            "upstream": entry.repo,
            "syncedTag": "v1.0.0",
            "syncedCommit": None,
            "skills": state.compute_tree_hashes(entry.plugin_dir / "skills"),
        },
    )
    dist = scratch / "dist"
    dist.mkdir()
    version = json.loads(
        (entry.plugin_dir / "package.json").read_text(encoding="utf-8")
    )["version"]
    with tarfile.open(dist / f"{entry.package}-{version}.tgz", "w:gz") as archive:
        for skill in sorted((entry.plugin_dir / "skills").iterdir()):
            for path in sorted(skill.rglob("*")):
                if not path.is_file():
                    continue
                data = path.read_bytes()
                info = tarfile.TarInfo(
                    f"package/skills/{skill.name}/{path.relative_to(skill).as_posix()}"
                )
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))

    handmade = entry.plugin_dir / "skills" / "handmade"
    handmade.mkdir()
    (handmade / "SKILL.md").write_text(
        "---\nname: handmade\ndescription: d\n---\n\nx\n", encoding="utf-8"
    )

    stream = io.StringIO()
    code = run_verify([entry], scratch, dist_dir=dist, stream=stream)

    assert code == 5
    assert "handmade" in stream.getvalue()


# ---- round 2, finding 1: a failure after the version write must still roll back ----


def test_apply_rolls_back_when_the_docs_step_fails(scratch):
    """The manifest is written before `wrote_manifest` used to be set, and the doc
    pass may raise (an undecodable README). Both must leave version 1.0.0 behind."""
    entry = make_entry(scratch, version="1.0.0")
    (entry.plugin_dir / "README.md").write_text("Bundle for demo v1.0.0.\n", encoding="utf-8")
    (entry.plugin_dir / "README.zh.md").write_bytes(b"\xff\xfe not utf-8 at all")
    staging = scratch / "staging" / "skills" / "alpha"
    staging.mkdir(parents=True)
    (staging / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nnew body\n", encoding="utf-8"
    )

    with pytest.raises(ApplyFailed):
        apply_candidate(entry.plugin_dir, scratch / "staging" / "skills", "2.0.0")

    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    # the decodable doc must not have been half-updated either
    assert "v1.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")
    # and the tree itself is back
    assert "old body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")


# ---- round 2, I-1: the docs belong to the transaction too ----


def _staged(scratch) -> Path:
    staging = scratch / "staging" / "skills" / "alpha"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: new\n---\n\nnew body\n", encoding="utf-8"
    )
    return scratch / "staging" / "skills"


def test_apply_rolls_the_docs_back_when_a_later_step_fails(scratch):
    entry = make_entry(scratch, version="1.0.0")
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")

    with pytest.raises(ApplyFailed):
        apply_candidate(entry.plugin_dir, _staged(scratch), "2.0.0", fail_after=3)

    assert "v1.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")


def test_pack_failure_rolls_the_docs_back_too(scratch, dist):
    entry = make_entry(scratch)
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")
    (entry.plugin_dir / "THIRD_PARTY_NOTICES.md").write_text(
        "Upstream version: 1.0.0\n", encoding="utf-8"
    )
    upstream = make_upstream(scratch, body="new body")

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(returncode=1),
        stream=io.StringIO(),
    )

    assert code == 6
    assert "v1.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")
    assert "1.0.0" in (entry.plugin_dir / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )


# ---- round 2, I-2: a pack error that is not a PackFailed must still roll back ----


def test_pack_reports_a_missing_npm_as_a_pack_failure(scratch):
    entry = make_entry(scratch)
    with pytest.raises(PackFailed):
        pack(
            entry.plugin_dir,
            scratch / "dist",
            npm_path=str(scratch / "definitely-not-npm.cmd"),
            runner=fake_pack_runner(),
        )


def test_update_maps_an_unexpected_pack_error_to_code_6_and_rolls_back(scratch, dist):
    entry = make_entry(scratch)
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")
    upstream = make_upstream(scratch, body="new body")

    def exploding_runner(argv, env):
        raise FileNotFoundError("npm is not where pack.py thinks it is")

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=exploding_runner,
        stream=io.StringIO(),
    )

    assert code == 6
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    assert "v1.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")
    assert not (entry.plugin_dir / ".skill-sync-state.json").exists()


# ---- round 2, M-4: a rebuild that needs no version rewrite is a no-op, not a failure ----


def test_rebuild_with_a_reformatted_manifest_is_a_no_op(scratch, dist):
    entry = make_entry(scratch, version="1.0.0")
    (entry.plugin_dir / "package.json").write_text(
        '{"name": "demo-skill-dsh","version":"1.0.0","description":"demo 1.0.0"}',
        encoding="utf-8",
    )
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
    assert "rebuilt body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")


# ---- round 2, M-1: a corrupt tarball is a verification failure, with a report ----


def test_verify_reports_a_corrupt_tarball_as_a_verification_failure(scratch):
    entry = make_entry(scratch)
    state.write(
        entry.plugin_dir,
        {
            "package": entry.package,
            "upstream": entry.repo,
            "syncedTag": "v1.0.0",
            "syncedCommit": None,
            "skills": state.compute_tree_hashes(entry.plugin_dir / "skills"),
        },
    )
    dist = scratch / "dist"
    dist.mkdir()
    (dist / f"{entry.package}-1.0.0.tgz").write_bytes(b"this is not a tarball")

    stream = io.StringIO()
    code = run_verify([entry], scratch, dist_dir=dist, stream=stream)

    assert code == 5
    assert "demo-skill-dsh" in stream.getvalue()


# ---- round 3, I-1: the state write belongs to the transaction too ----


def test_update_rolls_back_when_the_state_write_fails(scratch, dist, monkeypatch):
    import skill_sync

    entry = make_entry(scratch)
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")
    upstream = make_upstream(scratch, body="new body")

    def locked(plugin_dir, payload):
        raise OSError("the state file is locked by another process")

    monkeypatch.setattr(skill_sync.state, "write", locked)

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=io.StringIO(),
    )

    assert code == 7
    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    assert "v1.0.0" in (entry.plugin_dir / "README.md").read_text(encoding="utf-8")
    assert "old body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")


# ---- round 3, I-2: an interrupt during packing must roll back before it propagates ----


def test_update_rolls_back_when_packing_is_interrupted(scratch, dist):
    entry = make_entry(scratch)
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")
    upstream = make_upstream(scratch, body="new body")

    def interrupted(argv, env):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        run_update(
            [entry],
            FakeClient(status="update_available", tag="v2.0.0"),
            scratch,
            upstream_provider=provider(upstream),
            dist_dir=dist,
            pack_runner=interrupted,
            stream=io.StringIO(),
        )

    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    assert "old body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")


# ---- round 3, I-3: one broken bundle must not discard the whole batch ----


def test_update_keeps_the_batch_when_one_bundle_explodes(scratch, dist):
    good = make_entry(scratch, package="good-skill-dsh")
    bad = make_entry(scratch, package="bad-skill-dsh")
    upstream = make_upstream(scratch, body="new body")

    class ExplodingClient:
        def latest(self, entry):
            if entry.package == "bad-skill-dsh":
                raise ValueError("malformed cached payload")
            return SimpleNamespace(
                status="update_available", tag="v2.0.0", commit=None, reason=None, source="test"
            )

    stream = io.StringIO()
    code = run_update(
        [bad, good],
        ExplodingClient(),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )

    output = stream.getvalue()
    assert code != 0
    assert "bad-skill-dsh" in output and "good-skill-dsh" in output
    assert "malformed cached payload" in output
    # the healthy bundle still landed and still advertises its install step
    assert json.loads((good.plugin_dir / "package.json").read_text(encoding="utf-8"))[
        "version"
    ] == "2.0.0"
    assert "install_bundle" in output


# ---- round 3, I-4: a zero-byte artifact must not strand the good tarball ----


def test_pack_restores_the_previous_tarball_over_a_zero_byte_artifact(scratch):
    entry = make_entry(scratch)
    dist = scratch / "dist"
    dist.mkdir()
    stale = dist / "demo-skill-dsh-1.0.0.tgz"
    stale.write_bytes(b"old tarball")

    def runner(argv, env):
        # a failed npm run that still touched the destination
        (dist / "demo-skill-dsh-1.0.0.tgz").write_bytes(b"")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    with pytest.raises(PackFailed):
        pack(entry.plugin_dir, dist, runner=runner)

    assert stale.read_bytes() == b"old tarball"
    assert not (dist / ".previous" / "demo-skill-dsh-1.0.0.tgz").exists()


# ---- round 4: the state write must roll back on an interrupt too ----


def test_update_rolls_back_when_the_state_write_is_interrupted(scratch, dist, monkeypatch):
    import skill_sync

    entry = make_entry(scratch)
    (entry.plugin_dir / "README.md").write_text("Bundle for v1.0.0.\n", encoding="utf-8")
    upstream = make_upstream(scratch, body="new body")

    def interrupted(plugin_dir, payload):
        raise KeyboardInterrupt()

    monkeypatch.setattr(skill_sync.state, "write", interrupted)

    with pytest.raises(KeyboardInterrupt):
        run_update(
            [entry],
            FakeClient(status="update_available", tag="v2.0.0"),
            scratch,
            upstream_provider=provider(upstream),
            dist_dir=dist,
            pack_runner=fake_pack_runner(),
            stream=io.StringIO(),
        )

    manifest = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "1.0.0"
    assert "old body" in (
        entry.plugin_dir / "skills" / "alpha" / "SKILL.md"
    ).read_text(encoding="utf-8")

"""update orchestration: check -> fetch -> recipe -> verify -> apply -> pack."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import state
from core.registry import Entry
from core.upstream import STATUS_UP_TO_DATE, STATUS_UPDATE_AVAILABLE, LatestInfo
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

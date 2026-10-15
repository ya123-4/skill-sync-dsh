"""snapshot 型更新：用上游发布的资产替换受管目录（archify 的形态）。

Increment spec: docs/superpowers/specs/2026-10-14-skill-sync-follow-entries.md
"""
import io
import json
import zipfile
from pathlib import Path

import pytest

import skill_sync
from core.registry import Entry
from core.release import ReleaseError, extract_subtree
from helpers import FakeClient, fake_pack_runner
from skill_sync import run_update


def make_zip(path: Path, members: dict) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return path


def test_extract_subtree_writes_only_that_subtree(scratch):
    archive = make_zip(
        scratch / "a.zip",
        {
            "archify/SKILL.md": "---\nname: archify\ndescription: d\n---\n\nbody\n",
            "archify/bin/tool.mjs": "console.log(1)\n",
            "other/notes.md": "ignore me\n",
        },
    )
    target = scratch / "out"

    assert extract_subtree(archive, "archify", target) == 2
    assert (target / "SKILL.md").is_file()
    assert (target / "bin" / "tool.mjs").is_file()
    assert not (target / "notes.md").exists()


def test_extract_subtree_rejects_a_missing_subtree(scratch):
    archive = make_zip(scratch / "b.zip", {"other/x.txt": "x"})
    with pytest.raises(ReleaseError):
        extract_subtree(archive, "archify", scratch / "out")


def test_extract_subtree_refuses_an_escaping_entry(scratch):
    archive = make_zip(scratch / "c.zip", {"archify/../../escape.txt": "boom"})
    with pytest.raises(ReleaseError):
        extract_subtree(archive, "archify", scratch / "out")


def snapshot_entry(scratch, version="3.0.1") -> Entry:
    plugin = scratch / "archify-plugin-build"
    skill = plugin / "skills" / "archify"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: archify\ndescription: old\n---\n\nold body\n", encoding="utf-8"
    )
    (plugin / "package.json").write_text(
        json.dumps({"name": "archify-skill-dsh", "version": version, "description": "demo"}),
        encoding="utf-8",
    )
    return Entry(
        package="archify-skill-dsh",
        plugin_dir=plugin,
        repo="tt-a1i/archify",
        tag_prefix="v",
        skills_root="",
        strip_prefix="",
        recipe="",
        expected_skills=("archify",),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
        kind="github-releases",
        mode="snapshot",
        source="tt-a1i/archify",
        asset="archify.zip",
    )


def test_update_snapshot_lands_the_release_asset(scratch, monkeypatch):
    entry = snapshot_entry(scratch)
    archive = make_zip(
        scratch / "archify.zip",
        {
            "archify/SKILL.md": "---\nname: archify\ndescription: new\n---\n\nnew body\n",
            "archify/bin/tool.mjs": "console.log(2)\n",
        },
    )
    monkeypatch.setattr(skill_sync, "fetch_release_asset", lambda *args, **kwargs: archive)
    dist = scratch / "dist"
    stream = io.StringIO()

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v3.1.0"),
        scratch,
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )
    text = stream.getvalue()

    assert code == 0, text
    assert "3.0.1 -> v3.1.0" in text
    landed = (entry.plugin_dir / "skills" / "archify" / "SKILL.md").read_text(encoding="utf-8")
    assert "new body" in landed
    assert (entry.plugin_dir / "skills" / "archify" / "bin" / "tool.mjs").is_file()
    assert (dist / "archify-skill-dsh-3.1.0.tgz").is_file()
    payload = json.loads(
        (entry.plugin_dir / ".skill-sync-state.json").read_text(encoding="utf-8")
    )
    assert payload["syncedTag"] == "v3.1.0"
    assert payload["snapshotAsset"] == "archify.zip"


def test_update_snapshot_dry_run_writes_nothing(scratch, monkeypatch):
    entry = snapshot_entry(scratch)
    archive = make_zip(
        scratch / "archify.zip",
        {"archify/SKILL.md": "---\nname: archify\ndescription: new\n---\n\nnew body\n"},
    )
    monkeypatch.setattr(skill_sync, "fetch_release_asset", lambda *args, **kwargs: archive)
    before = (entry.plugin_dir / "skills" / "archify" / "SKILL.md").read_bytes()

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v3.1.0"),
        scratch,
        dist_dir=scratch / "dist",
        dry_run=True,
        stream=io.StringIO(),
    )

    assert code == 0
    assert (entry.plugin_dir / "skills" / "archify" / "SKILL.md").read_bytes() == before


def test_extract_subtree_refuses_a_member_that_is_rooted_after_the_prefix(scratch):
    """`archify//evil.txt` strips to a rooted path, which Windows does not call
    absolute -- containment has to be checked, not assumed."""
    for name in ("archify//evil.txt", "archify/\\evil.txt"):
        archive = make_zip(scratch / "d.zip", {name: "boom"})
        with pytest.raises(ReleaseError):
            extract_subtree(archive, "archify", scratch / "out-rooted")
        assert not (scratch / "evil.txt").exists()


def test_extract_subtree_refuses_a_member_that_climbs_out(scratch):
    archive = make_zip(scratch / "e.zip", {"archify/sub/../../escape.txt": "boom"})
    with pytest.raises(ReleaseError):
        extract_subtree(archive, "archify", scratch / "out-climb")


# ---- ppt-master 的形态：资产名带版本号，载荷多包一层 ----------------------


def ppt_master_entry(scratch, version="6.7.0") -> Entry:
    plugin = scratch / "ppt-master-plugin-build"
    skill = plugin / "skills" / "ppt-master"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: ppt-master\ndescription: old\n---\n\nold body\n", encoding="utf-8"
    )
    (plugin / "package.json").write_text(
        json.dumps({"name": "ppt-master-skill-dsh", "version": version, "description": "demo"}),
        encoding="utf-8",
    )
    return Entry(
        package="ppt-master-skill-dsh",
        plugin_dir=plugin,
        repo="hugohe3/ppt-master",
        tag_prefix="v",
        skills_root="",
        strip_prefix="",
        recipe="",
        expected_skills=("ppt-master",),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
        kind="github-releases",
        mode="snapshot",
        source="hugohe3/ppt-master",
        asset="ppt-master-skill-{tag}.zip",
        asset_subtree="ppt-master/skills/ppt-master",
    )


def test_release_asset_name_expands_the_tag_placeholder(scratch):
    """`{tag}` 让"资产名内含版本"的上游也能登记成一条静态 registry 数据。"""
    from core.release import fetch_release_asset

    calls: list = []

    def fake_http(url):
        calls.append(url)
        return b"payload"

    path = fetch_release_asset(
        "hugohe3/ppt-master",
        "v6.7.0",
        "ppt-master-skill-{tag}.zip",
        scratch / "cache",
        http=fake_http,
    )

    assert calls == [
        "https://github.com/hugohe3/ppt-master/releases/download/v6.7.0/"
        "ppt-master-skill-v6.7.0.zip"
    ]
    assert path.name.endswith("ppt-master-skill-v6.7.0.zip")
    assert path.read_bytes() == b"payload"


def test_update_snapshot_unwraps_a_nested_asset(scratch, monkeypatch):
    """ppt-master 的资产把载荷放在 ppt-master/skills/ 下，assetSubtree 负责剥掉这层。"""
    entry = ppt_master_entry(scratch)
    archive = make_zip(
        scratch / "ppt.zip",
        {
            "ppt-master/skills/ppt-master/SKILL.md": (
                "---\nname: ppt-master\ndescription: new\n---\n\nnew body\n"
            ),
            "ppt-master/skills/ppt-master/references/x.md": "ref\n",
            "ppt-master/README.md": "not part of the skill\n",
        },
    )
    seen: dict = {}

    def fake_fetch(repo, tag, asset, cache_dir, http=None):
        seen["asset"] = asset
        return archive

    monkeypatch.setattr(skill_sync, "fetch_release_asset", fake_fetch)
    dist = scratch / "dist"
    stream = io.StringIO()

    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v6.7.1"),
        scratch,
        dist_dir=dist,
        pack_runner=fake_pack_runner(),
        stream=stream,
    )
    text = stream.getvalue()

    assert code == 0, text
    landed = entry.plugin_dir / "skills" / "ppt-master"
    assert "new body" in (landed / "SKILL.md").read_text(encoding="utf-8")
    assert (landed / "references" / "x.md").is_file()
    # 前缀之外的内容（资产里的 ppt-master/README.md）不得越界落进来
    assert not (landed / "README.md").exists()
    assert (dist / "ppt-master-skill-dsh-6.7.1.tgz").is_file()
    # registry 保留占位符原文；展开只发生在 core.release
    assert seen["asset"] == "ppt-master-skill-{tag}.zip"

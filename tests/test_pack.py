"""Packing a bundle: npm invocation, artifact checks, previous-tarball retention."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.pack import NPM_DEFAULT_PATH, PackFailed, pack


def make_plugin(scratch, name="demo-skill-dsh", version="1.0.0") -> Path:
    plugin = scratch / name
    (plugin / "skills" / "alpha").mkdir(parents=True)
    (plugin / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\n---\n\nbody\n", encoding="utf-8"
    )
    (plugin / "package.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return plugin


def recording_runner(plugin, calls, create=True, returncode=0):
    def _runner(argv, env):
        calls.append({"argv": list(argv), "env": dict(env)})
        if create:
            destination = Path(argv[argv.index("--pack-destination") + 1])
            manifest = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
            artifact = destination / f"{manifest['name']}-{manifest['version']}.tgz"
            artifact.write_bytes(b"fake tarball")
        return SimpleNamespace(returncode=returncode, stdout="", stderr="boom")

    return _runner


def test_pack_builds_the_expected_artifact_name(scratch):
    plugin = make_plugin(scratch)
    calls = []
    artifact = pack(plugin, scratch / "dist", runner=recording_runner(plugin, calls))
    assert artifact.name == "demo-skill-dsh-1.0.0.tgz"
    assert artifact.read_bytes() == b"fake tarball"


def test_pack_uses_the_configured_npm_and_cache(scratch):
    plugin = make_plugin(scratch)
    calls = []
    cache = scratch / "npm-cache"
    pack(plugin, scratch / "dist", runner=recording_runner(plugin, calls), cache_dir=cache)

    argv = calls[0]["argv"]
    env = calls[0]["env"]
    assert argv[0] == NPM_DEFAULT_PATH
    assert argv[1] == "pack"
    assert str(plugin) in argv
    assert env["npm_config_cache"] == str(cache)


def test_pack_moves_an_existing_tarball_to_previous(scratch):
    plugin = make_plugin(scratch)
    dist = scratch / "dist"
    dist.mkdir()
    stale = dist / "demo-skill-dsh-1.0.0.tgz"
    stale.write_bytes(b"old tarball")

    pack(plugin, dist, runner=recording_runner(plugin, []))
    assert (dist / ".previous" / "demo-skill-dsh-1.0.0.tgz").read_bytes() == b"old tarball"
    assert (dist / "demo-skill-dsh-1.0.0.tgz").read_bytes() == b"fake tarball"


def test_pack_fails_when_npm_reports_failure(scratch):
    plugin = make_plugin(scratch)
    with pytest.raises(PackFailed):
        pack(plugin, scratch / "dist", runner=recording_runner(plugin, [], returncode=1))


def test_pack_fails_when_no_artifact_appears(scratch):
    plugin = make_plugin(scratch)
    with pytest.raises(PackFailed):
        pack(plugin, scratch / "dist", runner=recording_runner(plugin, [], create=False))


def test_pack_fails_on_a_missing_manifest(scratch):
    plugin = make_plugin(scratch)
    shutil.rmtree(plugin / "skills")
    (plugin / "package.json").unlink()
    with pytest.raises(PackFailed):
        pack(plugin, scratch / "dist", runner=recording_runner(plugin, [], create=False))

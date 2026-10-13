"""Landing a candidate: backup, atomic replace, rollback, and the state file."""
import hashlib
import json
from pathlib import Path

import pytest

from core import state
from core.apply import ApplyFailed, apply_candidate
from core.state import StateError


def make_plugin(scratch, version="1.0.0", skills=("alpha",)) -> Path:
    plugin = scratch / "demo-skill-dsh"
    for name in skills:
        directory = plugin / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: old\n---\n\nold body\n", encoding="utf-8"
        )
    (plugin / "package.json").write_text(
        json.dumps(
            {
                "name": "demo-skill-dsh",
                "version": version,
                "description": f"DeepSeek Harness bundle for demo {version}",
            }
        ),
        encoding="utf-8",
    )
    return plugin


def make_staging(scratch, skills=("alpha",), body="new body") -> Path:
    staging = scratch / "staging"
    for name in skills:
        directory = staging / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: new\n---\n\n{body}\n", encoding="utf-8"
        )
    return staging / "skills"


def tree_hashes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_apply_replaces_skills_and_keeps_a_backup(scratch):
    plugin = make_plugin(scratch)
    backup = apply_candidate(plugin, make_staging(scratch, body="new body"), "2.0.0")

    assert (plugin / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8").endswith(
        "new body\n"
    )
    assert (backup / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8").endswith(
        "old body\n"
    )
    assert (backup / "package.json").is_file()


def test_apply_bumps_the_version_and_the_description_reference(scratch):
    plugin = make_plugin(scratch, version="1.0.0")
    apply_candidate(plugin, make_staging(scratch), "2.0.0")

    manifest = json.loads((plugin / "package.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "2.0.0"
    assert "2.0.0" in manifest["description"]
    assert "1.0.0" not in manifest["description"]


def test_apply_failure_rolls_back_exactly(scratch):
    plugin = make_plugin(scratch)
    before = tree_hashes(plugin)

    with pytest.raises(ApplyFailed):
        apply_candidate(plugin, make_staging(scratch, body="new body"), "2.0.0", fail_after=1)

    assert tree_hashes(plugin) == before


def test_apply_failure_after_the_swap_also_rolls_back(scratch):
    plugin = make_plugin(scratch)
    before = tree_hashes(plugin)

    with pytest.raises(ApplyFailed):
        apply_candidate(plugin, make_staging(scratch, body="new body"), "2.0.0", fail_after=2)

    assert tree_hashes(plugin) == before


def test_apply_leaves_no_incoming_directory_behind(scratch):
    plugin = make_plugin(scratch)
    apply_candidate(plugin, make_staging(scratch), "2.0.0")
    leftovers = [path.name for path in (plugin / ".skill-sync").glob("incoming*")]
    assert leftovers == []


def test_apply_does_not_touch_files_outside_the_plugin(scratch):
    plugin = make_plugin(scratch)
    sentinel = scratch / "outside.txt"
    sentinel.write_text("untouched", encoding="utf-8")

    apply_candidate(plugin, make_staging(scratch), "2.0.0")

    assert sentinel.read_text(encoding="utf-8") == "untouched"


def test_state_roundtrip(scratch):
    plugin = make_plugin(scratch)
    payload = {"schema": 1, "package": "demo-skill-dsh", "syncedTag": "v2.0.0"}
    state.write(plugin, payload)
    assert state.read(plugin) == payload


def test_state_read_returns_none_when_absent(scratch):
    assert state.read(make_plugin(scratch)) is None


def test_state_rejects_an_unknown_schema(scratch):
    plugin = make_plugin(scratch)
    (plugin / ".skill-sync-state.json").write_text('{"schema": 99}', encoding="utf-8")
    with pytest.raises(StateError):
        state.read(plugin)


def test_compute_tree_hashes_covers_every_skill(scratch):
    plugin = make_plugin(scratch, skills=("alpha", "beta"))
    hashes = state.compute_tree_hashes(plugin / "skills")
    assert sorted(hashes) == ["alpha", "beta"]
    assert hashes["alpha"]["files"] == 1
    assert len(hashes["alpha"]["sha256"]) == 64


def test_manual_snapshot_hashes_manual_skills_only(scratch):
    plugin = make_plugin(scratch, skills=("alpha", "manual-skill"))
    snapshot = state.manual_snapshot(plugin, ("manual-skill",))
    assert list(snapshot) == ["manual-skill/SKILL.md"]
    assert snapshot["manual-skill/SKILL.md"] == hashlib.sha256(
        (plugin / "skills" / "manual-skill" / "SKILL.md").read_bytes()
    ).hexdigest()

"""verify command: local consistency, no network."""
import io
import json
import tarfile
from pathlib import Path

import pytest

from core import state
from core.registry import Entry
from skill_sync import run_verify


def make_entry(
    scratch,
    version="1.0.0",
    package="demo-skill-dsh",
    skills=("alpha",),
    with_state=True,
) -> Entry:
    plugin = scratch / package
    for name in skills:
        directory = plugin / "skills" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: d\n---\n\nbody\n", encoding="utf-8"
        )
    (plugin / "package.json").write_text(
        json.dumps({"name": package, "version": version, "description": f"demo {version}"}),
        encoding="utf-8",
    )
    if with_state:
        state.write(
            plugin,
            {
                "package": package,
                "upstream": "demo/skills",
                "syncedTag": f"v{version}",
                "syncedCommit": None,
                "skills": state.compute_tree_hashes(plugin / "skills"),
            },
        )
    return Entry(
        package=package,
        plugin_dir=plugin,
        repo="demo/skills",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe="superpowers",
        expected_skills=tuple(skills),
        manual_skills=(),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


def make_dist(scratch, entry, mutate=None) -> Path:
    dist = scratch / "dist"
    dist.mkdir(exist_ok=True)
    version = json.loads((entry.plugin_dir / "package.json").read_text(encoding="utf-8"))["version"]
    artifact = dist / f"{entry.package}-{version}.tgz"
    with tarfile.open(artifact, "w:gz") as archive:
        for skill in sorted((entry.plugin_dir / "skills").iterdir()):
            for path in sorted(skill.rglob("*")):
                if not path.is_file():
                    continue
                data = mutate(path) if mutate else path.read_bytes()
                info = tarfile.TarInfo(
                    f"package/skills/{skill.name}/{path.relative_to(skill).as_posix()}"
                )
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return dist


def run(entries, scratch, **kwargs) -> tuple[int, str]:
    stream = io.StringIO()
    code = run_verify(entries, scratch, stream=stream, **kwargs)
    return code, stream.getvalue()


def test_clean_bundle_verifies(scratch):
    entry = make_entry(scratch)
    dist = make_dist(scratch, entry)

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 0
    assert "demo-skill-dsh" in output


def test_missing_state_blocks(scratch):
    entry = make_entry(scratch, with_state=False)
    dist = make_dist(scratch, entry)

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 5
    assert "state" in output.lower()


def test_version_state_mismatch_blocks(scratch):
    entry = make_entry(scratch, version="1.0.0")
    dist = make_dist(scratch, entry)
    manifest = entry.plugin_dir / "package.json"
    manifest.write_text(
        json.dumps({"name": entry.package, "version": "2.0.0", "description": "demo"}), encoding="utf-8"
    )

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 5
    assert "version" in output.lower()


def test_skill_drift_from_the_state_file_blocks(scratch):
    entry = make_entry(scratch)
    dist = make_dist(scratch, entry)
    (entry.plugin_dir / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\n---\n\nchanged after sync\n", encoding="utf-8"
    )

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 5
    assert "drift" in output.lower() or "changed" in output.lower()


def test_profile_name_collision_blocks(scratch):
    entry = make_entry(scratch)
    dist = make_dist(scratch, entry)
    profile = scratch / "profile"
    other = profile / "node_modules" / "other-skill-dsh" / "skills" / "alpha"
    other.mkdir(parents=True)
    (other / "SKILL.md").write_text("---\nname: alpha\ndescription: d\n---\n\nx\n", encoding="utf-8")

    code, output = run([entry], scratch, dist_dir=dist, profile=profile)

    assert code == 5
    assert "other-skill-dsh" in output


def test_tgz_tree_mismatch_blocks(scratch):
    entry = make_entry(scratch)
    dist = make_dist(scratch, entry, mutate=lambda path: b"different tarball content\n")

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 5
    assert "tarball" in output.lower()


def test_missing_tarball_blocks(scratch):
    entry = make_entry(scratch)
    dist = scratch / "dist"
    dist.mkdir()

    code, output = run([entry], scratch, dist_dir=dist)

    assert code == 5
    assert "tarball" in output.lower()

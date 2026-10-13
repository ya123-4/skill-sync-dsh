"""Shared factories for the skill-sync test suite."""
import json
from pathlib import Path
from types import SimpleNamespace

from core.registry import Entry


def make_entry(
    scratch,
    version="1.0.0",
    package="demo-skill-dsh",
    recipe="superpowers",
    expected=("alpha",),
    baseline_commit=None,
    manual_skills=(),
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
        manual_skills=tuple(manual_skills),
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


def stage_skills(scratch, body="new body", name="alpha", directory="staging") -> Path:
    staging = scratch / directory / "skills" / name
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: new\n---\n\n{body}\n", encoding="utf-8"
    )
    return scratch / directory / "skills"


class FakeClient:
    def __init__(self, status="up_to_date", tag="v1.0.0", reason=None, commit=None):
        self.info = SimpleNamespace(
            status=status, tag=tag, commit=commit, reason=reason, source="test"
        )

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

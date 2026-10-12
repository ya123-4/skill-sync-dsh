"""check command: three-state report, JSON shape, exit codes, non-ASCII safety."""
import importlib
import io
import json
import sys

import pytest

from core.registry import Entry
from core.report import CheckRow, render_check
from core.upstream import STATUS_UNKNOWN, STATUS_UPDATE_AVAILABLE, GithubClient
from skill_sync import run_check


def make_entry(scratch, package, version, manual_skills=()):
    plugin = scratch / package
    (plugin / "skills").mkdir(parents=True)
    (plugin / "package.json").write_text(
        json.dumps({"name": package, "version": version}), encoding="utf-8"
    )
    return Entry(
        package=package,
        plugin_dir=plugin,
        repo=f"demo/{package}",
        tag_prefix="v",
        skills_root="skills",
        strip_prefix="",
        recipe="demo",
        expected_skills=("demo",),
        manual_skills=tuple(manual_skills),
        commit_pinned=False,
        license="MIT",
        notes="",
    )


def routing_http(releases, failure_status=None):
    """Route release lookups by repository name; optional global failure."""

    def _http(url, headers):
        if failure_status is not None:
            return failure_status, b"{}"
        for repo, tag in releases.items():
            if f"/repos/{repo}/" in url:
                if tag is None:
                    return 404, b"{}"
                return 200, json.dumps({"tag_name": tag}).encode("utf-8")
        return 404, b"{}"

    return _http


@pytest.fixture()
def three_entries(scratch):
    return [
        make_entry(scratch, "alpha-skill-dsh", "1.0.0"),
        make_entry(scratch, "beta-skill-dsh", "1.0.0"),
        make_entry(scratch, "gamma-skill-dsh", "1.0.0"),
    ]


def test_check_renders_all_three_states(three_entries):
    client = GithubClient(
        http=routing_http({"demo/alpha-skill-dsh": "v2.0.0", "demo/beta-skill-dsh": "v1.0.0"}),
        cache_dir=None,
    )
    stream = io.StringIO()
    code = run_check(three_entries, client, stream=stream)
    text = stream.getvalue()

    assert code == 0
    assert "alpha-skill-dsh" in text and "update available" in text
    assert "beta-skill-dsh" in text and "up to date" in text
    assert "gamma-skill-dsh" in text and "unknown" in text


def test_check_json_shape(three_entries):
    client = GithubClient(http=routing_http({"demo/alpha-skill-dsh": "v2.0.0"}), cache_dir=None)
    stream = io.StringIO()
    run_check(three_entries, client, as_json=True, stream=stream)
    payload = json.loads(stream.getvalue())

    assert [row["package"] for row in payload] == [entry.package for entry in three_entries]
    assert set(payload[0]) == {"package", "local", "remote", "status", "reason"}
    assert payload[0]["status"] == STATUS_UPDATE_AVAILABLE
    assert payload[0]["remote"] == "v2.0.0"


def test_check_exit_code_is_zero_even_when_everything_is_unknown(three_entries):
    client = GithubClient(http=routing_http({}, failure_status=403), cache_dir=None)
    assert run_check(three_entries, client, stream=io.StringIO()) == 0


def test_check_offline_without_cache_marks_unknown(three_entries):
    client = GithubClient(http=routing_http({}), cache_dir=None, offline=True)
    stream = io.StringIO()
    run_check(three_entries, client, stream=stream)
    assert stream.getvalue().count("unknown") >= 3


def test_check_flags_manual_skills(scratch):
    entry = make_entry(scratch, "vercel-skills-dsh", "1.7.1", manual_skills=("skills-cli",))
    client = GithubClient(http=routing_http({"demo/vercel-skills-dsh": "v1.7.1"}), cache_dir=None)
    stream = io.StringIO()
    run_check([entry], client, stream=stream)
    text = stream.getvalue()
    assert "skills-cli" in text
    assert "manual" in text.lower()


def test_render_check_preserves_non_ascii():
    rows = [
        CheckRow(
            package="demo",
            local="1.0.0",
            remote=None,
            status=STATUS_UNKNOWN,
            reason="upstream ✨ 发布说明 unavailable",
        )
    ]
    text = render_check(rows, as_json=False)
    assert "✨" in text
    assert "发布说明" in text


def test_cli_reconfigures_stdout_for_non_ascii(monkeypatch):
    stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk")
    monkeypatch.setattr(sys, "stdout", stream)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="gbk"))

    module = importlib.import_module("skill_sync")
    importlib.reload(module)

    assert sys.stdout.encoding.lower().replace("-", "") == "utf8"
    assert sys.stdout.errors == "replace"
    assert sys.stderr.errors == "replace"

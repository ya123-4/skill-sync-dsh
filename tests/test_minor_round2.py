"""Fixes for the nine Minor findings left by reviews 3 and 4 (2026-10-13).

Each test pins one of: the check exit code, backup-directory reuse inside the same
second, the npm pre-check defeating runner injection, non-atomic state writes, a
contradictory hint, a rollback that hides the original error, an orphan tarball,
and the two halves of the per-bundle guard.
"""
import io
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import skill_sync
from core import apply as apply_module
from core import state
from core.apply import apply_candidate
from core.exitcodes import EXIT_UPSTREAM, EXIT_VERIFY
from core.pack import PackFailed, pack
from core.upstream import UpstreamError
from helpers import (
    FakeClient,
    fake_pack_runner,
    make_entry,
    make_upstream,
    provider,
    stage_skills,
)
from skill_sync import run_check, run_update, run_verify


@pytest.fixture()
def dist(scratch):
    return scratch / "dist"


# ---- R3-1: check must not claim success when nothing could be compared ----


def test_check_exits_three_when_no_bundle_could_be_compared(scratch):
    entries = [
        make_entry(scratch, package="alpha-skill-dsh"),
        make_entry(scratch, package="beta-skill-dsh"),
    ]

    class UnknownClient:
        def latest(self, entry):
            return SimpleNamespace(
                status="unknown", tag=None, commit=None, reason="rate limit", source="test"
            )

    assert run_check(entries, UnknownClient(), stream=io.StringIO()) == EXIT_UPSTREAM


def test_check_still_exits_zero_when_some_rows_were_compared(scratch):
    entries = [
        make_entry(scratch, package="alpha-skill-dsh"),
        make_entry(scratch, package="beta-skill-dsh"),
    ]

    class MixedClient:
        def latest(self, entry):
            if entry.package == "beta-skill-dsh":
                return SimpleNamespace(
                    status="unknown", tag=None, commit=None, reason="rate limit", source="test"
                )
            return SimpleNamespace(
                status="up_to_date", tag="v1.0.0", commit=None, reason=None, source="test"
            )

    assert run_check(entries, MixedClient(), stream=io.StringIO()) == 0


# ---- R3-2: two landings in the same second must not share a backup ----


class _FrozenDatetime:
    @staticmethod
    def now(tz=None):
        return datetime(2026, 10, 13, 12, 0, 0, tzinfo=timezone.utc)

    strftime = staticmethod(lambda fmt: datetime(2026, 10, 13, 12, 0, 0).strftime(fmt))


def test_two_landings_in_the_same_second_use_distinct_backups(scratch, monkeypatch):
    monkeypatch.setattr(apply_module, "datetime", _FrozenDatetime)
    entry = make_entry(scratch, version="1.0.0")

    first = apply_candidate(entry.plugin_dir, stage_skills(scratch, "one", directory="s1"), "1.1.0")
    second = apply_candidate(
        entry.plugin_dir, stage_skills(scratch, "two", directory="s2"), "1.2.0"
    )

    assert first != second
    assert (first / "skills" / "alpha" / "SKILL.md").is_file()
    assert (second / "skills" / "alpha" / "SKILL.md").is_file()
    # the classic symptom: the second move nested the tree inside the first backup
    assert not (second / "skills" / "skills").exists()
    # each backup holds what the tree looked like *before* that landing
    assert "old body" in (first / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "one" in (second / "skills" / "alpha" / "SKILL.md").read_text(encoding="utf-8")


# ---- R3-3: an injected runner must not require npm to exist ----


def test_pack_with_an_injected_runner_does_not_need_npm(scratch):
    entry = make_entry(scratch)
    artifact = pack(
        entry.plugin_dir,
        scratch / "dist",
        npm_path=str(scratch / "definitely-not-npm.cmd"),
        runner=fake_pack_runner(),
    )
    assert artifact.is_file()


# ---- R3-4: a failed state write must not damage the previous one ----


def test_state_write_is_atomic(scratch, monkeypatch):
    entry = make_entry(scratch)
    state.write(entry.plugin_dir, {"package": entry.package, "syncedTag": "v1.0.0"})
    original = state.state_path(entry.plugin_dir).read_bytes()

    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", explode)
    with pytest.raises(OSError):
        state.write(entry.plugin_dir, {"package": entry.package, "syncedTag": "v2.0.0"})

    assert state.state_path(entry.plugin_dir).read_bytes() == original


# ---- R3-5: the hint must not contradict the code it just returned ----


def test_unexpected_error_hint_matches_the_returned_code(monkeypatch, capsys):
    def boom(workspace):
        raise ValueError("registry exploded")

    monkeypatch.setattr(skill_sync.registry, "load", boom)
    code = skill_sync.main(["check", "--offline"])
    error = capsys.readouterr().err

    assert code == 2
    assert "outside the documented exit codes" not in error
    assert "registry exploded" in error


# ---- R3-6: a failing rollback must not hide the original failure ----


def test_a_failed_rollback_still_reports_the_original_error(scratch, dist, monkeypatch):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    def exploding_runner(argv, env):
        raise PackFailed("npm blew up")

    def broken_restore(plugin_dir, backup, drop=True):
        raise OSError("cannot restore the backup")

    monkeypatch.setattr(skill_sync, "restore_backup", broken_restore)

    stream = io.StringIO()
    code = run_update(
        [entry],
        FakeClient(status="update_available", tag="v2.0.0"),
        scratch,
        upstream_provider=provider(upstream),
        dist_dir=dist,
        pack_runner=exploding_runner,
        stream=stream,
    )

    text = stream.getvalue()
    assert code == 6
    assert "npm blew up" in text
    assert "cannot restore the backup" in text


# ---- R4-1: a landing that rolls back must not leave its tarball behind ----


def test_a_failed_state_write_removes_the_orphan_tarball(scratch, dist, monkeypatch):
    entry = make_entry(scratch)
    upstream = make_upstream(scratch, body="new body")

    def locked(plugin_dir, payload):
        raise OSError("the state file is locked")

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
    assert not (dist / "demo-skill-dsh-2.0.0.tgz").exists()


# ---- R4-2: one broken lookup must not cost the whole check table ----


def test_check_keeps_the_table_when_one_lookup_explodes(scratch):
    good = make_entry(scratch, package="good-skill-dsh")
    bad = make_entry(scratch, package="bad-skill-dsh")

    class ExplodingClient:
        def latest(self, entry):
            if entry.package == "bad-skill-dsh":
                raise ValueError("malformed cached payload")
            return SimpleNamespace(
                status="up_to_date", tag="v1.0.0", commit=None, reason=None, source="test"
            )

    stream = io.StringIO()
    run_check([bad, good], ExplodingClient(), stream=stream)
    text = stream.getvalue()

    assert "good-skill-dsh" in text and "bad-skill-dsh" in text
    assert "malformed cached payload" in text


# ---- R4-3: the batch guard must classify what it catches ----


def test_the_batch_guard_classifies_an_upstream_error_as_code_3(scratch, dist):
    good = make_entry(scratch, package="good-skill-dsh")
    bad = make_entry(scratch, package="bad-skill-dsh")
    upstream = make_upstream(scratch, body="new body")

    class ExplodingClient:
        def latest(self, entry):
            if entry.package == "bad-skill-dsh":
                raise UpstreamError("repository not found or renamed (HTTP 404)")
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

    text = stream.getvalue()
    assert code == EXIT_UPSTREAM
    assert "upstream error" in text
    assert "internal error" not in text
    assert "good-skill-dsh" in text


# ---- found live: the profile scan must survive an unreadable bundle ----


def test_profile_scan_skips_an_unreadable_bundle(scratch, monkeypatch):
    from pathlib import Path as _Path

    profile = scratch / "profile"
    good = profile / "node_modules" / "good-skill-dsh" / "skills" / "alpha"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: d\n---\n\nx\n", encoding="utf-8"
    )
    broken_skills = profile / "node_modules" / "broken-skill-dsh" / "skills"
    broken_skills.mkdir(parents=True)

    real_iterdir = _Path.iterdir

    def guarded(self):
        if self == broken_skills:
            raise PermissionError("access is denied")
        return real_iterdir(self)

    monkeypatch.setattr(_Path, "iterdir", guarded)

    assert skill_sync._profile_skills(profile) == {"alpha": "good-skill-dsh"}

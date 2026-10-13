"""Candidate verification: the six rules that decide whether a rebuild may land.

This runs against a staged ``skills/`` tree before anything is written into a
managed bundle. Every rule *reports* rather than raises, so one run shows every
problem at once; ``blocking()`` then selects the issues that stop a landing, and
the caller decides which codes it is willing to ignore (``--accept-structure-change``
maps to ``structure-changed``).

Rules, in the order they run here:

1. every skill directory has a ``SKILL.md`` whose frontmatter ``name`` matches the
   directory and is kebab-case;
2. no camelCase frontmatter keys (``disableModelInvocation`` and friends make DSH
   skip the skill);
3. the skill set matches what was expected: additions, removals and renames are
   reported;
4. no skill name is already taken by another installed bundle;
5. ``manual`` components are byte-identical to what the bundle ships today;
6. the payload did not collapse (block) or swing wildly (warn).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from core.registry import Entry

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LEGACY_FRONTMATTER_KEYS = ("disableModelInvocation", "modelInvocable", "userInvocable")

SEVERITY_BLOCK = "block"
SEVERITY_WARN = "warn"

SHRINK_BLOCK_RATIO = 0.10
SWING_WARN_LOW = 0.50
SWING_WARN_HIGH = 1.50


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    message: str


class VerifyFailed(RuntimeError):
    """Blocking issues were found; nothing may be written (exit code 5)."""

    def __init__(self, issues) -> None:
        self.issues = list(issues)
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
        super().__init__(summary or "verification failed")


def blocking(issues, ignore_codes=()) -> list[Issue]:
    """Issues that must stop a landing, minus the codes the caller accepts."""
    ignored = set(ignore_codes)
    return [
        issue
        for issue in issues
        if issue.severity == SEVERITY_BLOCK and issue.code not in ignored
    ]


def frontmatter_name(text: str) -> str | None:
    if not text.startswith("---\n"):
        return None
    for line in text.splitlines()[1:15]:
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
        if line.strip() == "---":
            return None
    return None


def tree_size(root: Path) -> int:
    return sum(path.stat().st_size for path in Path(root).rglob("*") if path.is_file())


def verify_candidate(
    staging: Path | str,
    entry: Entry,
    previous: Path | str | None = None,
    profile_skills: set[str] | None = None,
    manual_snapshot: dict[str, str] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    skills_dir = Path(staging) / "skills"
    if not skills_dir.is_dir():
        return [Issue("no-skills", SEVERITY_BLOCK, f"{skills_dir} does not exist")]

    skill_dirs = sorted(
        (path for path in skills_dir.iterdir() if path.is_dir()), key=lambda path: path.name
    )
    if not skill_dirs:
        return [Issue("no-skills", SEVERITY_BLOCK, f"{skills_dir} has no skill directories")]

    names = {path.name for path in skill_dirs}

    # rules 1 and 2
    for path in skill_dirs:
        name = path.name
        if not KEBAB_RE.match(name):
            issues.append(Issue("bad-name", SEVERITY_BLOCK, f"{name} is not kebab-case"))

        skill_md = path / "SKILL.md"
        if not skill_md.is_file():
            issues.append(
                Issue("missing-skill-md", SEVERITY_BLOCK, f"{name}/SKILL.md is missing")
            )
            continue

        text = skill_md.read_text(encoding="utf-8")
        declared = frontmatter_name(text)
        if declared is None:
            issues.append(
                Issue("missing-name", SEVERITY_BLOCK, f"{name}/SKILL.md has no frontmatter name")
            )
        elif declared != name:
            issues.append(
                Issue(
                    "name-mismatch",
                    SEVERITY_BLOCK,
                    f"{name}/SKILL.md declares name: {declared}",
                )
            )
        for key in LEGACY_FRONTMATTER_KEYS:
            if re.search(rf"^{re.escape(key)}\s*:", text, re.MULTILINE):
                issues.append(
                    Issue(
                        "legacy-frontmatter",
                        SEVERITY_BLOCK,
                        f"{name}/SKILL.md uses {key}, which DSH rejects",
                    )
                )

    # rule 3
    expected = _expected_names(entry, previous)
    if expected is not None:
        removed = sorted(expected - names)
        added = sorted(names - expected)
        if removed:
            issues.append(
                Issue("structure-changed", SEVERITY_BLOCK, f"skills disappeared: {removed}")
            )
        if added:
            issues.append(Issue("structure-changed", SEVERITY_BLOCK, f"skills appeared: {added}"))

    # rule 4
    if profile_skills:
        clashes = sorted(names & set(profile_skills))
        if clashes:
            issues.append(
                Issue(
                    "duplicate-skill-name",
                    SEVERITY_BLOCK,
                    f"names already taken by other bundles: {clashes}",
                )
            )

    # rule 5
    for relative, digest in (manual_snapshot or {}).items():
        candidate = skills_dir / relative
        if not candidate.is_file():
            issues.append(
                Issue(
                    "manual-missing",
                    SEVERITY_BLOCK,
                    f"manual file missing from the candidate: {relative}",
                )
            )
        elif hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
            issues.append(
                Issue("manual-changed", SEVERITY_BLOCK, f"manual file changed: {relative}")
            )

    # rule 6
    if previous is not None and Path(previous).is_dir():
        previous_size = tree_size(Path(previous))
        if previous_size > 0:
            ratio = tree_size(skills_dir) / previous_size
            if ratio < SHRINK_BLOCK_RATIO:
                issues.append(
                    Issue(
                        "payload-shrunk",
                        SEVERITY_BLOCK,
                        f"payload fell to {ratio:.1%} of the previous build",
                    )
                )
            elif ratio < SWING_WARN_LOW or ratio > SWING_WARN_HIGH:
                issues.append(
                    Issue(
                        "payload-changed",
                        SEVERITY_WARN,
                        f"payload is {ratio:.0%} of the previous build",
                    )
                )

    return issues


def _expected_names(entry: Entry, previous: Path | str | None) -> set[str] | None:
    if previous is not None:
        path = Path(previous)
        if path.is_dir():
            return {item.name for item in path.iterdir() if item.is_dir()}
    if entry.expected_skills:
        return set(entry.expected_skills)
    return None

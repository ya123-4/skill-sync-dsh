"""Reporting for ``check`` (and, later, ``update``).

Two renderings of the same data: a fixed-width table for a human reading the
terminal, and JSON for anything that wants to consume the result. Non-ASCII
content (upstream descriptions, CJK notes) is preserved verbatim; keeping the
stream writable is the CLI's job (it reconfigures stdout to UTF-8).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from core.upstream import STATUS_UNKNOWN, STATUS_UP_TO_DATE, STATUS_UPDATE_AVAILABLE

_STATUS_LABELS = {
    STATUS_UPDATE_AVAILABLE: "update available",
    STATUS_UP_TO_DATE: "up to date",
    STATUS_UNKNOWN: "unknown",
}

_STATUS_ORDER = (STATUS_UPDATE_AVAILABLE, STATUS_UP_TO_DATE, STATUS_UNKNOWN)


@dataclass(frozen=True)
class CheckRow:
    package: str
    local: str
    remote: str | None
    status: str
    reason: str | None = None
    manual_skills: tuple[str, ...] = ()


def status_label(status: str) -> str:
    return _STATUS_LABELS.get(status, status)


def render_check(rows: list[CheckRow], as_json: bool = False) -> str:
    if as_json:
        payload = [
            {
                "package": row.package,
                "local": row.local,
                "remote": row.remote,
                "status": row.status,
                "reason": row.reason,
            }
            for row in rows
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False)

    width = max((len(row.package) for row in rows), default=0)
    lines = ["skill-sync check", ""]
    for row in rows:
        transition = f"{row.local} -> {row.remote}" if row.remote else row.local
        line = f"  {row.package:<{width}}  {transition:<26}  {status_label(row.status)}"
        if row.reason:
            line += f"  ({row.reason})"
        lines.append(line)

    counts = {status: sum(1 for row in rows if row.status == status) for status in _STATUS_ORDER}
    lines.append("")
    lines.append(
        "summary: "
        + ", ".join(f"{counts[status]} {status_label(status)}" for status in _STATUS_ORDER)
    )
    for row in rows:
        if row.manual_skills:
            names = ", ".join(row.manual_skills)
            lines.append(
                f"note: {row.package} ships manual skill(s) {names} - "
                "upstream changes there are never applied automatically"
            )
    return "\n".join(lines)

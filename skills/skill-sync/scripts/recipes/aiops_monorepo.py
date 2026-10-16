"""aiops_monorepo: take exactly the declared skills out of a monorepo, one directory each.

``aliyun/alibabacloud-aiops-skills`` is not a skill repository; it is a monorepo that
keeps one directory per skill under ``skills/developertools/solutions/`` -- roughly two
hundred skills, 7280 files, 19.6 MB. The ``superpowers`` rule ("copy everything under
``skills_root``") would land all of them and would make every future upstream skill
appear inside this bundle, so this recipe copies only the directories the registry
names in ``expectedSkills``. A declared skill whose directory -- or whose ``SKILL.md``
-- is missing is a structure change (exit code 4), reported for a human rather than
shipped as a bundle that only ``verify`` would notice.

One local deviation is named: the upstream ``alibabacloud-workbench-cli`` text assumes a
generic Linux shell, while this host runs the CLI portably under a sandbox that cannot
write ``~``. The DSH note in ``overlays/alibabacloud-workbench-cli/dsh-notes.txt`` is a
reviewable file, not prose buried in this module. It is inserted after the last row of
upstream's Troubleshooting table and nowhere else: the anchor occurs exactly once, it must
still be the file's last content, the transform is computed in memory and only a successful
result is written, so a rewritten or relocated anchor raises ``AnchorNotFound`` and cannot
leave a half-patched ``SKILL.md`` behind.

Upstream's bytes are copied verbatim; the only thing written by this module is that
inserted block. Any other difference between the bundle and upstream would have no name,
which is exactly what the golden regression forbids.
"""
from __future__ import annotations

from pathlib import Path

from recipes.base import (
    AnchorNotFound,
    RecipeContext,
    RecipeResult,
    StructureChanged,
    insert_after,
    write_tree,
)

# The skills whose upstream text needs the DSH note below. A skill copied by this recipe
# but absent from this set is vendored byte-for-byte.
OVERLAY_TARGETS = frozenset({"alibabacloud-workbench-cli"})

# The last row of upstream's Troubleshooting table, up to the end of its exit-code cell:
#
#     | `STS token expired` | 4 | If using `RamRoleArn` mode, the CLI auto-refreshes. ... |
#
# Note the *absence* of backticks around this row's error name. Every other row of the
# table wraps the error in backticks, but this one does not -- it really is
# ``| STS token expired | 4 |`` in the upstream file (13062 bytes, sha256 5554809F...C2A9).
# A backticked anchor copied from prose describes a line upstream does not have, and would
# fail every rebuild with AnchorNotFound.
#
# This is only the row's *prefix*, and it occurs exactly once in the file: it identifies
# which row to insert after. It is not the insertion point itself, because inserting
# directly after it would splice the note into the middle of that row, leaving the rest of
# upstream's fix column stranded at the end of the file. ``_anchor_row`` below extends it to
# the whole row -- and refuses a row that is no longer the file's last content, so the note
# can only ever land at the document tail.
LAST_TROUBLESHOOTING_ROW_START = "| STS token expired | 4 |"

OVERLAY_DIR = Path(__file__).resolve().parent / "overlays"


def _overlay_text(name: str) -> str:
    """Return the named overlay block, refusing an overlay this plugin cannot apply.

    The overlay file belongs to this plugin, not to upstream, so a missing, undecodable or
    newline-broken one is a packaging fault (``StructureChanged``, exit code 4) and never an
    ``AnchorNotFound`` that would read as upstream having moved its table. Since the note is
    written at the end of the file, the overlay's own final newline is the file's final
    newline, so it has to be exactly one.
    """
    path = OVERLAY_DIR / name / "dsh-notes.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # a packaging error, not an upstream change
        raise StructureChanged(f"overlay text is missing from this plugin: {path}") from exc
    except UnicodeDecodeError as exc:  # also packaging: the overlay is not UTF-8 text
        raise StructureChanged(f"overlay text is not valid UTF-8: {path}") from exc
    if not text.endswith("\n") or text.endswith("\n\n"):
        raise StructureChanged(
            f"overlay text must end in exactly one newline, not {text[-2:]!r}: {path}"
        )
    return text


def _anchor_row(text: str, start: str) -> str:
    """Return the complete newline-terminated line beginning with ``start``.

    Refuses to guess: a ``start`` that is absent, or that occurs more than once, does not
    identify one row, and inserting at a partial row would only look right. The trailing
    newline is part of the returned row so that inserting after it leaves the note as the
    file's own last line rather than re-terminating a line that already ended.

    The row must also be the *last* content in the file. If upstream appends anything to the
    table's end -- another row, an appendix, prose -- then this row no longer names the
    document tail, and inserting after it would drop the note into the middle of upstream's
    text. That is the anchor having moved, so it raises instead of relocating; only trailing
    whitespace is tolerated here, and ``_overlaid`` refuses that after the insertion.
    """
    first = text.find(start)
    if first < 0:
        raise AnchorNotFound(f"anchor not found: {start!r}")
    if text.find(start, first + len(start)) >= 0:
        raise AnchorNotFound(f"anchor is not unique: {start!r}")
    line_end = text.find("\n", first)
    row = text[first:] if line_end < 0 else text[first : line_end + 1]
    trailing = text[first + len(row) :]
    if trailing.strip():
        raise AnchorNotFound(
            f"the anchor row {start!r} is not the file's last content: "
            f"{len(trailing)} more characters follow it, beginning {trailing[:40]!r}"
        )
    return row


def _overlaid(text: str, name: str) -> bytes:
    """Return ``text`` with the named DSH note appended after the anchor row.

    The transform is pure: the caller writes the returned bytes exactly once, so a missing,
    ambiguous or relocated anchor raises before the staged tree has been touched. The note
    goes after the *whole* anchor row -- inserting straight after a row prefix would split
    the row and strand the rest of upstream's fix text at the end of the file.

    ``_anchor_row`` rejects anything readable after the row but tolerates trailing
    *whitespace*; this guard refuses that too, so the note really is the file's last content
    and the file still ends in exactly one newline (``_overlay_text`` guarantees the note's
    own final newline is a single one). When it fires it is upstream's shape that moved: the
    overlay file is not at fault, and the message says so.
    """
    note = _overlay_text(name)
    updated = insert_after(text, _anchor_row(text, LAST_TROUBLESHOOTING_ROW_START), note)
    if not updated.endswith(note) or updated.endswith("\n\n"):
        raise AnchorNotFound(
            f"{name}: SKILL.md carries whitespace after the anchor row, so the note would not "
            f"be the file's last content (the patched file ends {updated[-4:]!r})"
        )
    return updated.encode("utf-8")


def assemble(context: RecipeContext) -> RecipeResult:
    entry = context.entry
    skills_root = context.upstream_dir / entry.skills_root
    if not skills_root.is_dir():
        raise StructureChanged(
            f"{entry.package}: upstream has no {entry.skills_root}/ under {context.upstream_dir}"
        )
    if not entry.expected_skills:
        raise StructureChanged(f"{entry.package}: the registry declares no expected skill to build")

    # Check every declared skill and run every overlay transform before writing
    # anything: a structure change or a moved anchor then leaves no candidate tree
    # behind for a later step to install by mistake.
    for name in entry.expected_skills:
        if not (skills_root / name).is_dir():
            raise StructureChanged(
                f"{entry.package}: upstream {entry.skills_root}/{name} is missing"
            )
        if not (skills_root / name / "SKILL.md").is_file():
            raise StructureChanged(
                f"{entry.package}: upstream {entry.skills_root}/{name}/SKILL.md is missing"
            )

    # Transform before writing, not file by file: a moved anchor must fail the whole
    # build, not leave a partially staged skill behind for a later step to install.
    # read_bytes().decode(), not read_text(): text mode applies universal-newline
    # translation, which would quietly rewrite a CRLF upstream to LF and contradict the
    # "upstream's bytes are copied verbatim" contract above.
    patched: dict[str, bytes] = {}
    for name in entry.expected_skills:
        if name in OVERLAY_TARGETS:
            skill_md = skills_root / name / "SKILL.md"
            try:
                text = skill_md.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise StructureChanged(
                    f"{entry.package}: upstream {entry.skills_root}/{name}/SKILL.md is not UTF-8"
                ) from exc
            patched[name] = _overlaid(text, name)

    staging_skills = context.staging_dir / "skills"
    skills: list[str] = []
    notes: list[str] = []
    for name in entry.expected_skills:
        target = staging_skills / name
        write_tree(skills_root / name, target)
        if name in patched:
            (target / "SKILL.md").write_bytes(patched[name])
            notes.append(f"{name}: applied the DSH overlay ({name}/dsh-notes.txt)")
        skills.append(name)

    return RecipeResult(skills=skills, manual_skills=[], notes=notes)

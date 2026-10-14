"""Release assets as an upstream.

Some upstreams publish a packaged artifact rather than a source tree: archify
ships ``archify.zip`` as a release asset, and its README states the skill
directory is a verbatim snapshot of that release. Fetching the asset is what makes
a ``snapshot`` update possible without inventing a recipe for a directory that has
no local edits to reapply.
"""
from __future__ import annotations

import ssl
import urllib.request
import zipfile
from pathlib import Path

RELEASE_ROOT = "https://github.com"
_TIMEOUT_SECONDS = 300


class ReleaseError(RuntimeError):
    """The release asset could not be fetched, or is not a usable archive."""


def asset_path(cache_dir: Path | str, repo: str, tag: str, asset: str) -> Path:
    safe = f"{repo.replace('/', '__')}__{tag}__{asset}"
    return Path(cache_dir) / "assets" / safe


def fetch_release_asset(
    repo: str,
    tag: str,
    asset: str,
    cache_dir: Path | str,
    http=None,
) -> Path:
    """Download a release asset once and reuse it; returns the local file."""
    target = asset_path(cache_dir, repo, tag, asset)
    if target.is_file() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    url = f"{RELEASE_ROOT}/{repo}/releases/download/{tag}/{asset}"
    try:
        data = (http or _default_http)(url)
    except Exception as exc:  # noqa: BLE001 - every transport failure is a release failure
        raise ReleaseError(f"{url}: {type(exc).__name__}: {exc}") from exc
    if not data:
        raise ReleaseError(f"{url}: empty response")

    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(data)
    temporary.replace(target)
    return target


def extract_subtree(archive: Path | str, subtree: str, target: Path | str) -> int:
    """Extract ``subtree/`` from a zip archive into ``target``.

    Returns the number of files written. Entry names are validated: a member that
    escapes the target (``..``), or an archive with no such subtree, is an error
    rather than a silent partial extraction.
    """
    archive = Path(archive)
    target = Path(target)
    prefix = subtree.strip("/") + "/"
    written = 0
    try:
        with zipfile.ZipFile(archive) as handle:
            members = [
                member
                for member in handle.infolist()
                if member.filename.startswith(prefix) and not member.is_dir()
            ]
            if not members:
                raise ReleaseError(f"{archive.name}: no {prefix}* entries in the archive")
            for member in members:
                relative = member.filename[len(prefix):]
                parts = Path(relative).parts
                if (
                    not relative
                    or relative.startswith(("/", "\\"))
                    or ".." in parts
                    or Path(relative).is_absolute()
                ):
                    raise ReleaseError(f"{archive.name}: refusing entry {member.filename!r}")
                destination = target / relative
                # A leading separator survives the checks above on Windows
                # ("/evil.txt" is not absolute there), so confirm containment.
                resolved = destination.resolve()
                root = target.resolve()
                if resolved != root and root not in resolved.parents:
                    raise ReleaseError(f"{archive.name}: entry escapes the target: {member.filename!r}")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(handle.read(member))
                written += 1
    except zipfile.BadZipFile as exc:
        raise ReleaseError(f"{archive.name}: not a zip archive: {exc}") from exc
    return written


def _default_http(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "skill-sync"})
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=_TIMEOUT_SECONDS) as response:
        return response.read()

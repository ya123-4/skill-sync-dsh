"""Upstream queries and pinned snapshot downloads for a registry entry.

The network layer is injectable -- ``http(url, headers) -> (status, body)`` -- so
every branch is testable without the GitHub API and without burning the 60/h
anonymous quota.

Two questions live here: "is there a newer release?" (GitHub API, cached) and
"give me the bytes of that release" (codeload tarball, cached on disk).
"""
from __future__ import annotations

import json
import re
import shutil
import ssl
import tarfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.registry import Entry
from core.versions import is_newer, normalize_tag, normalize_version

API_ROOT = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 25
USER_AGENT = "skill-sync-dsh"

STATUS_UP_TO_DATE = "up_to_date"
STATUS_UPDATE_AVAILABLE = "update_available"
STATUS_UNKNOWN = "unknown"

RATE_LIMIT_REASON = (
    "GitHub API rate limit reached (60 requests/hour anonymous; "
    "set GITHUB_TOKEN for 5000/hour)"
)

HttpCallable = Callable[[str, dict], "tuple[int, bytes]"]


class UpstreamError(RuntimeError):
    """Upstream could not answer a question we asked (exit code 3 when fatal)."""


@dataclass(frozen=True)
class LatestInfo:
    status: str
    tag: str | None
    commit: str | None
    reason: str | None
    source: str


def default_http(url: str, headers: dict) -> tuple[int, bytes]:
    """Real transport: urllib with the host certificate store."""
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def local_version(entry: Entry) -> str:
    """The upstream version the installed bundle is built from.

    For a GitHub-tagged bundle that is the bundle's own version. A bundle whose
    skills come from an npm package on a *different* version line declares the
    version it vendored in ``optionalDependencies`` -- rea-dsh-plugin ships skills
    from rea-agents 6.x while the plugin itself is 0.1.x -- so that pin is the
    baseline every comparison and rebuild must use. Comparing the plugin's own
    0.1.x against rea-agents 6.x would be meaningless, which is why this entry
    shape is refused anywhere the pin is missing.
    """
    manifest = entry.plugin_dir / "package.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpstreamError(f"{entry.package}: cannot read {manifest}: {exc}") from exc

    if entry.kind == "npm" and entry.mode == "rebuild":
        pinned = (payload.get("optionalDependencies") or {}).get(entry.source)
        if not isinstance(pinned, str) or not pinned:
            raise UpstreamError(
                f"{entry.package}: {manifest} has no optionalDependencies[{entry.source!r}] "
                "pinning the vendored upstream version"
            )
        return pinned

    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise UpstreamError(f"{entry.package}: {manifest} has no version")
    return version


class GithubClient:
    """Answers "is there a newer upstream release?" for one entry at a time."""

    def __init__(
        self,
        http: HttpCallable | None = None,
        token: str | None = None,
        cache_dir: Path | str | None = None,
        ttl_seconds: int = 600,
        now: Callable[[], datetime] | None = None,
        refresh: bool = False,
        offline: bool = False,
    ) -> None:
        self._http = http or default_http
        self._token = token
        self._cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._ttl_seconds = ttl_seconds
        self._now = now or (lambda: datetime.now(timezone.utc))
        self.refresh = refresh
        self.offline = offline

    # ---- public API -----------------------------------------------------

    def latest(self, entry: Entry) -> LatestInfo:
        cached = self._read_cache(entry)

        if self.offline:
            if cached is not None:
                return self._evaluate(entry, cached.get("tag"), cached.get("commit"), "cache")
            return LatestInfo(STATUS_UNKNOWN, None, None, "offline and no cached result", "cache")

        if cached is not None and not self.refresh and self._is_fresh(cached):
            return self._evaluate(entry, cached.get("tag"), cached.get("commit"), "cache")

        try:
            tag, commit, source = self._query(entry)
        except UpstreamError as exc:
            return LatestInfo(STATUS_UNKNOWN, None, None, str(exc), "network")

        if tag is None:
            return LatestInfo(
                STATUS_UNKNOWN,
                None,
                None,
                f"no release tag matching prefix {entry.tag_prefix!r} on {entry.repo}",
                source,
            )
        self._write_cache(entry, tag, commit)
        return self._evaluate(entry, tag, commit, source)

    # ---- query ----------------------------------------------------------

    def _headers(self) -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _call(self, url: str, headers: dict) -> tuple[int, bytes]:
        try:
            return self._http(url, headers)
        except UpstreamError:
            raise
        except Exception as exc:  # noqa: BLE001 - every transport failure is "unknown upstream"
            raise UpstreamError(f"{url}: {type(exc).__name__}: {exc}") from exc

    def _query(self, entry: Entry) -> tuple[str | None, str | None, str]:
        headers = self._headers()

        status, body = self._call(f"{API_ROOT}/repos/{entry.repo}/releases/latest", headers)
        if status in (403, 429):
            raise UpstreamError(RATE_LIMIT_REASON)
        if status == 200:
            try:
                payload = json.loads(body or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise UpstreamError(f"{entry.repo}: malformed release payload: {exc}") from exc
            tag = payload.get("tag_name")
            # A release tag still has to look like one of this entry's versions;
            # otherwise the tag list is the better answer (and may be newer).
            if isinstance(tag, str) and tag and normalize_tag(tag, entry.tag_prefix):
                commit = self._query_commit(entry, headers, tag) if entry.commit_pinned else None
                return tag, commit, "release"

        return self._query_tags(entry, headers)

    def _query_tags(self, entry: Entry, headers: dict) -> tuple[str | None, str | None, str]:
        status, body = self._call(
            f"{API_ROOT}/repos/{entry.repo}/tags?per_page=100", headers
        )
        if status in (403, 429):
            raise UpstreamError(RATE_LIMIT_REASON)
        if status == 404:
            raise UpstreamError(f"{entry.repo}: repository not found or renamed (HTTP 404)")
        if status != 200:
            raise UpstreamError(f"{entry.repo}: GitHub API returned HTTP {status}")

        try:
            tags = json.loads(body or b"[]")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UpstreamError(f"{entry.repo}: malformed tag payload: {exc}") from exc

        # GitHub does not contract an order, so take the greatest matching version
        # rather than the first one on the page.
        best: str | None = None
        best_version: str | None = None
        for item in tags:
            name = item.get("name") if isinstance(item, dict) else None
            if not isinstance(name, str):
                continue
            version = normalize_tag(name, entry.tag_prefix)
            if version is None:
                continue
            if best is None or is_newer(best_version or version, version):
                best, best_version = name, version
        if best is None:
            return None, None, "tags"

        commit = self._query_commit(entry, headers, best) if entry.commit_pinned else None
        return best, commit, "tags"

    def _query_commit(self, entry: Entry, headers: dict, ref: str) -> str | None:
        """The commit a tag points at -- not whatever ``main`` is today."""
        status, body = self._call(f"{API_ROOT}/repos/{entry.repo}/commits/{ref}", headers)
        if status != 200:
            return None
        try:
            return json.loads(body or b"{}").get("sha")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    # ---- evaluation -----------------------------------------------------

    def _evaluate(self, entry: Entry, tag: str | None, commit: str | None, source: str) -> LatestInfo:
        if not tag:
            return LatestInfo(STATUS_UNKNOWN, None, commit, "no cached tag", source)

        local_raw = local_version(entry)
        local = normalize_tag(local_raw, entry.tag_prefix) or local_raw
        remote = normalize_tag(tag, entry.tag_prefix) or tag

        if normalize_version(local) is None or normalize_version(remote) is None:
            return LatestInfo(
                STATUS_UNKNOWN,
                tag,
                commit,
                f"cannot compare local {local!r} with upstream {remote!r}",
                source,
            )

        status = STATUS_UPDATE_AVAILABLE if is_newer(local, remote) else STATUS_UP_TO_DATE
        return LatestInfo(status, tag, commit, None, source)

    # ---- cache ----------------------------------------------------------

    def _cache_path(self, entry: Entry) -> Path | None:
        if self._cache_dir is None:
            return None
        return self._cache_dir / "api" / f"{entry.repo.replace('/', '__')}.json"

    def _read_cache(self, entry: Entry) -> dict | None:
        path = self._cache_path(entry)
        if path is None or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _write_cache(self, entry: Entry, tag: str, commit: str | None) -> None:
        path = self._cache_path(entry)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "repo": entry.repo,
            "tag": tag,
            "commit": commit,
            "fetched_at": self._now().isoformat(),
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _is_fresh(self, payload: dict) -> bool:
        stamp = payload.get("fetched_at")
        if not isinstance(stamp, str):
            return False
        try:
            fetched = datetime.fromisoformat(stamp)
        except ValueError:
            return False
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return (self._now() - fetched).total_seconds() < self._ttl_seconds


# ---- pinned snapshots ---------------------------------------------------

SNAPSHOT_MARKER = ".complete"
_COMMIT_RE = re.compile(r"[0-9a-fA-F]{7,40}")


class SnapshotError(RuntimeError):
    """A pinned upstream snapshot could not be fetched or unpacked (exit code 3)."""


def snapshot_url(repo: str, ref: str) -> str:
    """codeload URL for a tag or a commit SHA (no API quota involved)."""
    if _COMMIT_RE.fullmatch(ref):
        return f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    return f"https://codeload.github.com/{repo}/tar.gz/refs/tags/{ref}"


def snapshot_slug(repo: str, ref: str) -> str:
    return f"{repo.replace('/', '__')}__{re.sub(r'[^A-Za-z0-9._-]+', '_', ref)}"


NPM_REGISTRY_ROOT = "https://registry.npmjs.org"


def npm_tarball_url(package: str, version: str) -> str:
    """Registry tarball URL for one published version.

    No API quota is involved, and the payload has the same shape as a codeload
    tarball: a single top-level directory (``package/``) holding the published
    tree, so unpacking and validation are shared with the GitHub path.
    """
    return f"{NPM_REGISTRY_ROOT}/{package}/-/{package}-{version}.tgz"


def npm_snapshot_slug(package: str, version: str) -> str:
    return f"npm__{package.replace('/', '__')}__{version}"


def fetch_snapshot(
    entry: Entry,
    ref: str,
    cache_dir: Path | str,
    http: HttpCallable | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> Path:
    """Download (once) and unpack ``repo@ref``; return the checkout directory."""
    return _fetch_tarball(
        url=snapshot_url(entry.repo, ref),
        slug=snapshot_slug(entry.repo, ref),
        label=f"{entry.repo}@{ref}",
        cache_dir=Path(cache_dir),
        http=http,
        attempts=attempts,
        sleep=sleep,
    )


def fetch_npm_snapshot(
    entry: Entry,
    version: str,
    cache_dir: Path | str,
    http: HttpCallable | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> Path:
    """Download (once) and unpack ``<source>@<version>`` from the npm registry."""
    return _fetch_tarball(
        url=npm_tarball_url(entry.source, version),
        slug=npm_snapshot_slug(entry.source, version),
        label=f"{entry.source}@{version}",
        cache_dir=Path(cache_dir),
        http=http,
        attempts=attempts,
        sleep=sleep,
    )


def _fetch_tarball(
    url: str,
    slug: str,
    label: str,
    cache_dir: Path,
    http: HttpCallable | None = None,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> Path:
    """Download and unpack one tarball into a cached, single-top-directory tree.

    Both upstream kinds land here: codeload wraps a repository in
    ``<repo>-<ref>/`` and npm wraps a package in ``package/``. A download that
    does not produce exactly one such directory is rejected rather than half-used.

    Transport failures are retried (see ``_download``): this host's TLS
    middlebox truncates larger responses, and one flaky read must not cost a
    whole rebuild.
    """
    root = cache_dir / "snapshots" / slug
    marker = root / SNAPSHOT_MARKER

    if marker.is_file():
        cached = _single_top_dir(root)
        if cached is not None:
            return cached
        raise SnapshotError(f"{slug}: cached snapshot has no single top-level directory")

    _remove_tree(root)  # drop the corpse of any earlier failed attempt
    root.mkdir(parents=True, exist_ok=True)

    try:
        status, body = _download(http or default_http, url, attempts=attempts, sleep=sleep)
    except SnapshotError as exc:
        _remove_tree(root)
        raise SnapshotError(f"{label}: {exc}") from exc

    if status != 200:
        _remove_tree(root)
        raise SnapshotError(f"{label}: download failed with HTTP {status} ({url})")

    tarball = cache_dir / "tarballs" / f"{slug}.tar.gz"
    tarball.parent.mkdir(parents=True, exist_ok=True)
    tarball.write_bytes(body)

    try:
        with tarfile.open(tarball) as archive:
            try:
                archive.extractall(root, filter="data")
            except TypeError:  # Python < 3.12 has no extraction filters
                archive.extractall(root)
    except tarfile.TarError as exc:
        _remove_tree(root)
        raise SnapshotError(f"{label}: not a usable gzip tarball: {exc}") from exc

    checkout = _single_top_dir(root)
    if checkout is None:
        _remove_tree(root)
        raise SnapshotError(f"{label}: tarball has no single top-level directory")

    marker.write_text(f"{label}\n", encoding="utf-8")
    return checkout


def _single_top_dir(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    children = [child for child in root.iterdir() if child.name != SNAPSHOT_MARKER]
    if len(children) != 1 or not children[0].is_dir():
        return None
    return children[0]


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _download(
    call: HttpCallable,
    url: str,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
) -> tuple[int, bytes]:
    """Fetch with a bounded number of retries.

    A transport exception or a non-200 answer is retried: this host's TLS
    middlebox truncates larger responses (``IncompleteRead``), and retrying is
    far cheaper than failing a whole rebuild. A definitive answer -- 403 rate
    limit or 404 missing repository -- is returned immediately, because retrying
    it only burns quota.
    """
    non_retryable = frozenset({403, 404})
    pause = sleep if sleep is not None else time.sleep
    last_error = "no attempt was made"

    for attempt in range(1, attempts + 1):
        try:
            status, body = call(url, {"User-Agent": USER_AGENT})
        except Exception as exc:  # noqa: BLE001 - every transport failure is retryable
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if status == 200 and body:
                return status, body
            if status in non_retryable:
                return status, body
            last_error = f"HTTP {status}"
        if attempt < attempts:
            pause(float(attempt))

    raise SnapshotError(f"download failed after {attempts} attempt(s): {last_error}")

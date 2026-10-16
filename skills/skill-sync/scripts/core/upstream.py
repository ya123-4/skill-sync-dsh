"""Upstream queries and pinned snapshot downloads for a registry entry.

The network layer is injectable -- ``http(url, headers) -> (status, body)`` -- so
every branch is testable without the GitHub API and without burning the 60/h
anonymous quota.

Two questions live here: "is there a newer release?" (GitHub API, cached) and
"give me the bytes of that release" (codeload tarball, cached on disk).
"""
from __future__ import annotations

import json
import os
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

from core import state
from core.registry import Entry
from core.versions import is_newer, normalize_tag, normalize_version

API_ROOT = "https://api.github.com"
DEFAULT_TIMEOUT_SECONDS = 25
USER_AGENT = "skill-sync-dsh"

# Tag lookups page: the tracked upstreams can carry hundreds of tags (the
# alibabacloud-aiops-skills bundle was at 561), so the matching prefix is not
# guaranteed to be on page 1 -- reading only page 1 reported ``unknown`` for a
# release that exists. The cap is not unlimited because these requests spend the
# 60/h anonymous quota: five pages bound one lookup at 5 requests, and a prefix
# that has not appeared by then is reported unknown rather than guessed.
MAX_TAG_PAGES = 5

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


def local_revision(entry: Entry) -> str | None:
    """The upstream revision the installed bundle came from, or ``None``.

    A commit-tracked bundle has no version to compare against, so its baseline is
    the revision recorded in its own state file. A missing, unreadable or empty
    record is a *missing baseline*: the caller reports ``unknown``, never
    "current" -- the same rule that keeps a rate limit from reading as up to date.
    """
    if entry.plugin_dir is None:
        return None
    try:
        payload = state.read(entry.plugin_dir)
    except Exception:  # noqa: BLE001 - an unusable record is a missing baseline
        return None
    if payload is None:
        return None
    commit = payload.get("syncedCommit")
    return commit if isinstance(commit, str) and commit else None


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
        if entry.kind == "github-commits":
            return self._latest_commit(entry)

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

    # ---- commit-tracked upstreams ---------------------------------------

    def _latest_commit(self, entry: Entry) -> LatestInfo:
        """The newest commit on ``entry.branch``, against the bundle's record.

        An upstream with no tags has no version to compare, so the revision *is*
        the release. ``--offline``, rate limits and 404s behave exactly as they do
        for the tag path; the one deliberate difference is that a cached answer is
        never served while online, because a revision -- unlike a tag -- can move.
        """
        if self.offline:
            cached = self._read_cache(entry)
            commit = (cached or {}).get("commit")
            if isinstance(commit, str) and commit:
                return self._evaluate_commit(entry, commit, "cache")
            return LatestInfo(STATUS_UNKNOWN, None, None, "offline and no cached result", "cache")

        # A tag cannot move, so the tag path may answer from its short-lived cache.
        # A branch head can move, and ``update`` lands whatever this returns: while
        # online this asks upstream every time. The cache is still written, because
        # ``--offline`` needs something to fall back to -- it is a fallback, never
        # a shortcut.
        try:
            commit = self._query_head_commit(entry)
        except UpstreamError as exc:
            return LatestInfo(STATUS_UNKNOWN, None, None, str(exc), "network")

        if commit is None:
            return LatestInfo(
                STATUS_UNKNOWN,
                None,
                None,
                f"no commit on {entry.repo}@{entry.branch}",
                "commits",
            )
        self._write_cache(entry, "", commit)
        return self._evaluate_commit(entry, commit, "commits")

    def _query_head_commit(self, entry: Entry) -> str | None:
        """The head commit of the tracked branch -- not whatever a tag points at."""
        headers = self._headers()
        status, body = self._call(
            f"{API_ROOT}/repos/{entry.repo}/commits/{entry.branch}", headers
        )
        if status in (403, 429):
            raise UpstreamError(RATE_LIMIT_REASON)
        if status == 404:
            raise UpstreamError(f"{entry.repo}: repository not found or renamed (HTTP 404)")
        if status != 200:
            raise UpstreamError(f"{entry.repo}: GitHub API returned HTTP {status}")

        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise UpstreamError(f"{entry.repo}: malformed commit payload: {exc}") from exc

        sha = payload.get("sha")
        return sha if isinstance(sha, str) and sha else None

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
        """The greatest matching tag, reading pages until the prefix block ends.

        GitHub returns tags 100 at a time in no contracted order, and the tracked
        upstreams can carry hundreds -- so the matching prefix may sit on page 2
        or later. A full page with no match does not end the search until a match
        has been seen: that is exactly the case that used to be reported unknown.
        """
        best: str | None = None
        best_version: str | None = None
        seen_match = False

        for page in range(1, MAX_TAG_PAGES + 1):
            status, body = self._call(
                f"{API_ROOT}/repos/{entry.repo}/tags?per_page=100&page={page}", headers
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

            # GitHub does not contract an order, so take the greatest matching
            # version rather than the first one on the page.
            matched_here = False
            for item in tags:
                name = item.get("name") if isinstance(item, dict) else None
                if not isinstance(name, str):
                    continue
                version = normalize_tag(name, entry.tag_prefix)
                if version is None:
                    continue
                matched_here = True
                if best is None or is_newer(best_version or version, version):
                    best, best_version = name, version

            if matched_here:
                seen_match = True

            # A short page is the end of the tag list. Once a match has been seen,
            # a full page without one means the prefix block is over.
            if len(tags) < 100:
                break
            if seen_match and not matched_here:
                break

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

    def _evaluate_commit(self, entry: Entry, commit: str, source: str) -> LatestInfo:
        """Compare a revision against the bundle's recorded one (never a version)."""
        local = local_revision(entry)
        if not local:
            return LatestInfo(
                STATUS_UNKNOWN,
                None,
                commit,
                "no local revision recorded (the bundle has no state file)",
                source,
            )
        status = STATUS_UP_TO_DATE if local == commit else STATUS_UPDATE_AVAILABLE
        return LatestInfo(status, None, commit, None, source)

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

# Extended-length path prefixes (see ``_long_path``): a cached snapshot of the
# alibabacloud monorepo reaches 292 characters on this host, past MAX_PATH.
_LONG_PATH_PREFIX = "\\\\?\\"
_UNC_PREFIX = "\\\\"


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

    Two separate contracts, and only the first is unchanged:

    * **Extraction** behaves as it always did -- same cache layout, same
      ``.complete`` marker, same single checkout directory, and on POSIX the same
      path spelling. The extended-length destination below is a Windows detail
      that callers never see.
    * **The returned path is spelled through ``_long_path`` on Windows for every
      checkout, short ones included** -- not only for trees that are themselves
      long. Callers append long relative paths to the result (the alibabacloud
      recipe reaches 292 characters at ``upstream_dir / ... / SKILL.md`` while a
      plain base path is 225), so a length-gated prefix here would hand back the
      short form for exactly the checkouts that need the prefix and re-break the
      read this function exists to make possible. Do not add such a gate: the
      prefix is unconditional, and POSIX is the platform that keeps the plain
      string.
    """
    root = cache_dir / "snapshots" / slug
    marker = root / SNAPSHOT_MARKER

    if marker.is_file():
        cached = _single_top_dir(root)
        if cached is not None:
            return _checkout_path(cached)
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
            # An extended-length destination is not normalised by the OS, so the
            # archive's POSIX member names have to be converted first -- see
            # ``_normalise_member_separators`` for why both halves are required.
            _normalise_member_separators(archive)
            destination = _long_path(root)
            try:
                archive.extractall(destination, filter="data")
            except TypeError:  # Python < 3.12 has no extraction filters
                archive.extractall(destination)
    except tarfile.TarError as exc:
        _remove_tree(root)
        raise SnapshotError(f"{label}: not a usable gzip tarball: {exc}") from exc

    checkout = _single_top_dir(root)
    if checkout is None:
        _remove_tree(root)
        raise SnapshotError(f"{label}: tarball has no single top-level directory")

    marker.write_text(f"{label}\n", encoding="utf-8")
    return _checkout_path(checkout)


def _long_path(path: Path | str) -> str:
    r"""``str(path)`` spelled so Windows can reach past MAX_PATH; unchanged elsewhere.

    ``aliyun/alibabacloud-aiops-skills`` ships a 4476-member monorepo whose needed
    ``SKILL.md`` sits 292 characters below the real cache root, while this host runs
    with ``LongPathsEnabled=0``: the plain Win32 APIs refuse such a path before any
    file is created (``WinError 206``, "the filename or extension is too long"), and
    a shorter cache root cannot help -- the snapshot root is the shorter part.

    ``\\?\`` lifts that limit. It also *disables* path normalisation, so the path
    must already be absolute and free of ``.``/``..`` components, which is what
    ``os.path.abspath`` guarantees. POSIX has no such limit and keeps the plain
    string, so callers stay portable and no test needs a Windows-only branch.
    """
    text = str(path)
    if os.name != "nt" or text.startswith(_LONG_PATH_PREFIX):
        return text
    absolute = os.path.abspath(text)
    if absolute.startswith(_UNC_PREFIX):  # \\server\share -> \\?\UNC\server\share
        return f"{_LONG_PATH_PREFIX}UNC{absolute[1:]}"
    return _LONG_PATH_PREFIX + absolute


def _normalise_member_separators(archive: tarfile.TarFile) -> None:
    """Rewrite every member name in ``archive`` to this platform's separator.

    ``tarfile`` hands over the names as the tarball stores them -- POSIX, with
    ``/`` -- and ``os.path.join`` (which extraction uses) does not convert them.
    For a plain destination the OS normalises the mixed separators itself, but an
    extended-length destination is taken literally, so ``...\\d\\a/b`` fails with
    ``WinError 123`` while the same tree extracts fine through a short path. Both
    halves are required: the prefix alone, or normalisation alone, is not enough.

    On POSIX separators already match, and this is a no-op.
    """
    if os.sep == "/":
        return
    for member in archive.getmembers():
        member.name = member.name.replace("/", os.sep)


def _checkout_path(path: Path) -> Path:
    """The checkout as a ``Path`` spelled for this platform.

    Windows gets the extended-length form for the *directory*, not just for
    extraction, because callers append long relative paths to it: the
    alibabacloud recipe reads ``upstream_dir / "skills/..." / "SKILL.md"`` 292
    characters deep, and a short base would fail that read even though the bytes
    extracted. ``pathlib`` joins both separators and the prefix survives the join,
    which is exactly why string joining would not have worked here.

    Both return sites of ``_fetch_tarball`` (fresh extraction and cache hit) go
    through this, so the two spellings can never diverge.
    """
    return Path(_long_path(path))


def _single_top_dir(root: Path) -> Path | None:
    if not root.is_dir():
        return None
    children = [child for child in root.iterdir() if child.name != SNAPSHOT_MARKER]
    if len(children) != 1 or not children[0].is_dir():
        return None
    return children[0]


def _remove_tree(path: Path) -> None:
    """Drop a cache tree, including descendants past MAX_PATH.

    ``rmtree`` on the plain path stops at the first long descendant and leaves the
    rest of the tree behind, so a failed attempt would not actually be cleaned up
    before the next one extracts over it. The extended-length form is what lets the
    recursion reach every level; POSIX keeps the plain string.
    """
    shutil.rmtree(_long_path(path), ignore_errors=True)


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

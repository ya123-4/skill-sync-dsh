"""Upstream tag normalization and comparison.

Upstream repositories tag releases in shapes this project has to tell apart:

* ``v0.38.2``                    vercel-labs/agent-browser, obra/superpowers
* ``open-design-v0.24.1``        nexu-io/open-design carries the project name in the tag
* ``mattpocock-skills@1.0.0``    another project's tag living in a repo we track
* ``pr-3706-verification-assets``  a moving preview tag, never a release

Only a tag that starts with the entry's configured prefix *and* looks like a
version is a candidate. Everything else is ignored, so a foreign tag can never
be mistaken for the newest release.
"""
from __future__ import annotations

import re

_VERSION_RE = re.compile(
    r"^(?P<core>\d+(?:\.\d+)*)(?:[.\-_]?(?P<pre>alpha|beta|rc)\.?(?P<pre_num>\d*))?$",
    re.IGNORECASE,
)


def normalize_tag(raw: str, tag_prefix: str) -> str | None:
    """Return the version part of ``raw`` when it carries ``tag_prefix``.

    ``normalize_tag("v0.38.2", "v")`` -> ``"0.38.2"``;
    ``normalize_tag("mattpocock-skills@1.0.0", "v")`` -> ``None``.
    """
    if not raw or not tag_prefix or not raw.startswith(tag_prefix):
        return None
    candidate = raw[len(tag_prefix):].strip()
    if not _VERSION_RE.match(candidate):
        return None
    return candidate


def normalize_version(raw: str) -> tuple[int, ...] | None:
    """Numeric core of a version string, or None when it is not a version."""
    match = _VERSION_RE.match(raw.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group("core").split("."))


def is_newer(local: str, remote: str) -> bool:
    """True when ``remote`` is a later release than ``local``.

    Missing trailing components count as zero, so ``1.7`` < ``1.7.1``.
    Unparseable input is never "newer": unknown is reported as unknown upstream.
    """
    local_parts = normalize_version(local)
    remote_parts = normalize_version(remote)
    if local_parts is None or remote_parts is None:
        return False
    width = max(len(local_parts), len(remote_parts))
    padded_local = local_parts + (0,) * (width - len(local_parts))
    padded_remote = remote_parts + (0,) * (width - len(remote_parts))
    return padded_remote > padded_local


def bump_patch(version: str) -> str:
    """The next patch release on a bundle's *own* version line.

    Most bundles take the upstream version as their own, because the upstream tag
    prefix lines up with the plugin's numbering. A bundle that vendors an npm
    package on a different line -- rea-dsh-plugin ships skills from rea-agents 6.x
    while the plugin itself is 0.1.x -- cannot: a rebuild still has to be a new
    release, so its own patch number moves instead and the upstream pin is updated
    separately.
    """
    parts = normalize_version(version)
    if parts is None:
        raise ValueError(f"not a version: {version!r}")
    core = list(parts) + [0] * max(0, 3 - len(parts))
    core[2] += 1
    return ".".join(str(part) for part in core[:3])

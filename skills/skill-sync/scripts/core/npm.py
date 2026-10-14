"""npm registry as an upstream.

Third-party DSH plugins are usually installed from npm rather than rebuilt from a
skill repository, so "is there an update" is answered by the registry's
``dist-tags.latest``. This client is deliberately as defensive as the GitHub one:
a 404, a rate-limited reply, malformed JSON and a dead network all come back as
``unknown`` with a reason, never as an exception and never as "up to date".
"""
from __future__ import annotations

import json
import ssl
import urllib.request

from core.upstream import LatestInfo, STATUS_UNKNOWN, STATUS_UPDATE_AVAILABLE, STATUS_UP_TO_DATE
from core.versions import is_newer

REGISTRY_ROOT = "https://registry.npmjs.org"
_TIMEOUT_SECONDS = 30


class NpmClient:
    def __init__(self, http=None):
        self._http = http or _default_http

    def latest(self, name: str, local: str | None = None) -> LatestInfo:
        """The newest published version of ``name``.

        With ``local`` given the status is a comparison; without it, a successful
        lookup reports ``update available`` because no comparison was possible.
        """
        try:
            status, body = self._http(
                f"{REGISTRY_ROOT}/{name}",
                {"User-Agent": "skill-sync", "Accept": "application/vnd.npm.install-v1+json"},
            )
        except Exception as exc:  # noqa: BLE001 - every transport failure is "unknown"
            return LatestInfo(STATUS_UNKNOWN, None, None, f"{type(exc).__name__}: {exc}", "npm")

        if status == 404:
            return LatestInfo(
                STATUS_UNKNOWN, None, None, f"{name} is not published on npm (HTTP 404)", "npm"
            )
        if status in (429, 403):
            return LatestInfo(
                STATUS_UNKNOWN, None, None, f"npm registry rate limit (HTTP {status})", "npm"
            )
        if status != 200:
            return LatestInfo(
                STATUS_UNKNOWN, None, None, f"npm registry returned HTTP {status}", "npm"
            )

        try:
            payload = json.loads(body or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return LatestInfo(
                STATUS_UNKNOWN, None, None, f"{name}: malformed registry payload: {exc}", "npm"
            )

        tag = (payload.get("dist-tags") or {}).get("latest") if isinstance(payload, dict) else None
        if not isinstance(tag, str) or not tag:
            return LatestInfo(STATUS_UNKNOWN, None, None, f"{name}: no dist-tags.latest", "npm")

        if local is None:
            return LatestInfo(STATUS_UPDATE_AVAILABLE, tag, None, None, "npm")
        if is_newer(local, tag):
            return LatestInfo(STATUS_UPDATE_AVAILABLE, tag, None, None, "npm")
        return LatestInfo(STATUS_UP_TO_DATE, tag, None, None, "npm")


def _default_http(url: str, headers: dict) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context()
    with urllib.request.urlopen(request, context=context, timeout=_TIMEOUT_SECONDS) as response:
        return response.status, response.read()

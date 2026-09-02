"""HTTP access to the Science Live API and the nanopub network.

Stdlib only (urllib) — mirrors replication-radar, so the core stays installable
without a dependency tree.

Base URL precedence: explicit argument > SCIENCELIVE_API_BASE > DEFAULT_API_BASE.

DEFAULT_API_BASE is the **dev** deployment deliberately. As of 2026-09-02 the
production `/np/constellation` route returns HTTP 500 on known-good URIs while
`/health` reports healthy, and api-dev serves the same URIs with HTTP 200.
Flip DEFAULT_API_BASE to production once that is fixed.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API_BASE = "https://api-dev.sciencelive4all.org"
NANOPUB_RESOLVER = "https://w3id.org/np/"
TIMEOUT = 90


class ApiError(RuntimeError):
    """A Science Live API call failed in a way the caller should surface verbatim."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def api_base(explicit: str | None = None) -> str:
    return (explicit or os.environ.get("SCIENCELIVE_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def api_key(explicit: str | None = None) -> str:
    """The API key, or "" when unset.

    `/np/constellation` is a public read as of 2026-09, so an absent key is not
    fatal; we still send one when present because other routes require it.
    """
    return explicit or os.environ.get("SCIENCELIVE_API_KEY") or ""


def get_json(path: str, params: dict[str, str] | None = None, *,
             base: str | None = None, key: str | None = None,
             timeout: int = TIMEOUT) -> dict:
    """GET a JSON document from the Science Live API.

    Raises ApiError with the server's own message where it sent one — the point
    is that the agent relays the real failure, not a paraphrase of it.
    """
    url = f"{api_base(base)}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    headers = {"Accept": "application/json"}
    token = api_key(key)
    if token:
        headers["x-api-key"] = token

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            payload = json.loads(e.read().decode("utf-8"))
            detail = payload.get("error") or ""
        except Exception:
            pass
        raise ApiError(
            f"{path} returned HTTP {e.code}"
            + (f": {detail}" if detail else "")
            + (f" (base={api_base(base)})" if e.code >= 500 else ""),
            status=e.code,
        ) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ApiError(f"{path} unreachable at {api_base(base)}: {e}") from e


def canonical_uri(uri: str) -> str:
    """Normalise a nanopub URI to the bare resolver form.

    The `/sciencelive/np/` form redirects to an HTML single-page viewer that
    answers 200 with an HTML shell, so a status-only check passes even when no
    nanopub is served. The bare `w3id.org/np/` form serves RDF.
    """
    return uri.strip().replace("/sciencelive/np/", "/np/")


def fetch_trig(uri: str, *, timeout: int = 30) -> str:
    """Fetch a nanopub's TriG source.

    Raises ApiError when the response is the HTML viewer rather than RDF, which
    is the failure a status-only check silently passes.
    """
    req = urllib.request.Request(
        canonical_uri(uri), headers={"Accept": "application/trig"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
        raise ApiError(f"{uri} could not be fetched: {e}") from e

    head = body.lstrip()[:16].lower()
    if head.startswith("<!doctype") or head.startswith("<html"):
        raise ApiError(f"{uri} served the HTML viewer, not a nanopub (not RDF)")
    return body

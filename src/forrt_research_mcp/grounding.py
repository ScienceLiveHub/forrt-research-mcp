"""Resolve DOIs and Wikidata terms against their authorities.

Both answer the same shape of question: *does this identifier exist, and is it
the thing you think it is?* An agent drafting a nanopublication can produce a
plausible DOI or QID from memory, and a plausible one is indistinguishable from
a real one until something checks. Since 5b publishes automatically, nothing
downstream catches it — so it gets checked here.

Neither function guesses. `resolve_doi` reports what the DOI registry says or
that it does not resolve; `wikidata_lookup` returns real candidates and, when
given an expected type, says which ones actually are of that type rather than
picking one.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .api import USER_AGENT

DOI_RESOLVER = "https://doi.org/"
CSL_JSON = "application/vnd.citationstyles.csl+json"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
TIMEOUT = 30

# A DOI is "10." + registrant + "/" + suffix. Deliberately permissive on the
# suffix — DOIs legitimately contain almost anything.
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
# Wikidata item, not a property or lexeme.
_QID_RE = re.compile(r"^Q\d+$")


class GroundingError(ValueError):
    """The request itself was malformed (not: the identifier did not resolve)."""


def _get(url: str, accept: str, timeout: int = TIMEOUT) -> tuple[int, str]:
    req = urllib.request.Request(
        url, headers={"Accept": accept, "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError) as e:
        raise GroundingError(f"{url} unreachable: {e}") from e


def normalise_doi(doi: str) -> str:
    """Strip the many prefixes a DOI arrives with, down to the bare `10.…` form."""
    d = (doi or "").strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/",
                   "http://dx.doi.org/", "doi:", "DOI:"):
        if d.lower().startswith(prefix.lower()):
            d = d[len(prefix):]
            break
    return d.strip()


def _year_of(csl: dict) -> str:
    parts = ((csl.get("issued") or {}).get("date-parts") or [[]])[0]
    return str(parts[0]) if parts else ""


def resolve_doi(doi: str) -> dict:
    """Does this DOI resolve, and to what?

    Call it on every DOI destined for a nanopub field or `CITATION.cff`. A
    `resolves: false` result means the DOI does not exist at the registry — do
    not publish it, whatever it looks like.

    Returns the registered metadata (title, authors, year, container, type) so
    the caller can confirm it is the intended paper and not merely a valid one.
    """
    bare = normalise_doi(doi)
    if not bare:
        raise GroundingError("empty DOI")
    if not _DOI_RE.match(bare):
        raise GroundingError(
            f"{bare!r} is not DOI-shaped (expected '10.<registrant>/<suffix>')"
        )

    status, body = _get(DOI_RESOLVER + urllib.parse.quote(bare, safe="/:"), CSL_JSON)
    if status == 404:
        return {
            "doi": bare, "resolves": False, "status": 404,
            "note": ("this DOI is not registered. Do NOT publish it — a "
                     "well-formed DOI is not a real one."),
        }
    if status != 200 or not body:
        return {
            "doi": bare, "resolves": False, "status": status,
            "note": ("the resolver did not return metadata. This may be a "
                     "transient failure rather than a bad DOI; retry before "
                     "concluding the DOI is wrong."),
        }

    try:
        csl = json.loads(body)
    except json.JSONDecodeError:
        return {"doi": bare, "resolves": True, "status": status,
                "note": "resolves, but returned no parsable metadata"}

    title = csl.get("title")
    container = csl.get("container-title")
    return {
        "doi": bare,
        "resolves": True,
        "status": status,
        "url": f"{DOI_RESOLVER}{bare}",
        "title": (title[0] if isinstance(title, list) else title) or "",
        "authors": [
            " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
            or a.get("literal", "")
            for a in (csl.get("author") or [])
        ],
        "year": _year_of(csl),
        "type": csl.get("type", ""),
        "container": (container[0] if isinstance(container, list) else container) or "",
        "publisher": csl.get("publisher", ""),
        "note": ("resolves. Check the title is the paper you mean — a DOI can be "
                 "real and still be the wrong one."),
    }


def _wikidata_api(params: dict) -> dict:
    url = f"{WIKIDATA_API}?{urllib.parse.urlencode({**params, 'format': 'json'})}"
    status, body = _get(url, "application/json")
    if status != 200 or not body:
        raise GroundingError(f"Wikidata API returned HTTP {status}")
    return json.loads(body)


def _types_of(qids: list[str]) -> dict[str, list[dict]]:
    """P31 (instance of) + P279 (subclass of) for each QID, with labels."""
    if not qids:
        return {}
    data = _wikidata_api({
        "action": "wbgetentities", "ids": "|".join(qids),
        "props": "claims", "languages": "en",
    })

    by_qid: dict[str, list[str]] = {}
    for qid, entity in (data.get("entities") or {}).items():
        claims = entity.get("claims") or {}
        found: list[str] = []
        for prop in ("P31", "P279"):
            for statement in claims.get(prop) or []:
                value = (((statement.get("mainsnak") or {}).get("datavalue") or {})
                         .get("value") or {})
                if isinstance(value, dict) and value.get("id"):
                    found.append(value["id"])
        by_qid[qid] = found

    # One extra call to label every type QID we saw.
    type_ids = sorted({t for ts in by_qid.values() for t in ts})
    labels: dict[str, str] = {}
    for i in range(0, len(type_ids), 50):
        batch = type_ids[i:i + 50]
        data = _wikidata_api({
            "action": "wbgetentities", "ids": "|".join(batch),
            "props": "labels", "languages": "en",
        })
        for qid, entity in (data.get("entities") or {}).items():
            labels[qid] = ((entity.get("labels") or {}).get("en") or {}).get("value", "")

    return {q: [{"qid": t, "label": labels.get(t, "")} for t in ts]
            for q, ts in by_qid.items()}


def wikidata_lookup(query: str, expected_type: str = "", limit: int = 5) -> dict:
    """Find real Wikidata items for a term, and type-check them.

    Call it for every Wikidata topic/keyword destined for a nanopub field. It
    returns candidates and their types; it does NOT choose one — picking the
    right sense of an ambiguous label is a judgement, and the point of this tool
    is that the QID you publish is one that actually exists and actually is what
    you say it is.

    `expected_type` is a QID (e.g. Q16521 "taxon", Q11862829 "academic
    discipline"). Each candidate is then marked `typeMatches`, from its real
    P31/P279 statements. Candidates are NOT filtered out — a near miss is often
    the informative result.
    """
    term = (query or "").strip()
    if not term:
        raise GroundingError("empty query")
    if expected_type and not _QID_RE.match(expected_type.strip()):
        raise GroundingError(
            f"expected_type must be a Wikidata item QID like 'Q16521', got "
            f"{expected_type!r}"
        )

    data = _wikidata_api({
        "action": "wbsearchentities", "search": term,
        "language": "en", "uselang": "en", "type": "item",
        "limit": max(1, min(limit, 20)),
    })
    hits = data.get("search") or []
    if not hits:
        return {
            "query": term, "expectedType": expected_type, "count": 0,
            "candidates": [],
            "note": ("no Wikidata item matches this label. Do NOT invent a QID — "
                     "try a different label, or leave the field empty."),
        }

    types = _types_of([h["id"] for h in hits])
    want = expected_type.strip()

    candidates = []
    for h in hits:
        qid = h["id"]
        item_types = types.get(qid, [])
        candidate = {
            "qid": qid,
            "label": h.get("label", ""),
            "description": h.get("description", ""),
            "uri": f"http://www.wikidata.org/entity/{qid}",
            "types": item_types,
        }
        if want:
            candidate["typeMatches"] = any(t["qid"] == want for t in item_types)
        candidates.append(candidate)

    matching = [c for c in candidates if c.get("typeMatches")] if want else []
    return {
        "query": term,
        "expectedType": want,
        "count": len(candidates),
        "candidates": candidates,
        **({"matchingCount": len(matching)} if want else {}),
        "note": (
            "candidates only — choose the right sense yourself; none is asserted "
            "to be correct."
            + (f" {len(matching)} of {len(candidates)} are of type {want}."
               if want else "")
        ),
    }

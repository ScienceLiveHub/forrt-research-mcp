"""Mirror CiTO nanopublications into Wikidata, as a QuickStatements batch to review.

A published CiTO nanopublication says "this replication *confirms* (or
*qualifies*, *disputes*, ...) that paper". Wikidata can say the same thing:
"cites work" (P2860) with the qualifier "has goal" (P3712) set to a citation-
intent item. Each of the 48 intent items carries an exact match (P2888) to its
CiTO URI, so the mapping is read from Wikidata itself rather than kept here.
The statement is referenced with "nanopublication identifier" (P12545) in the
`https://w3id.org/np/<id>` form, which is the convention Scholia already counts
as "annotations defined in nanopublications".

Nothing reaches Wikidata unless someone writes it, and that someone must be a
person: this module never edits Wikidata. It produces a QuickStatements batch
and a report; a human reviews both and submits the batch with their own
account (https://quickstatements.toolforge.org).

Rules, each the answer to a mistake that would otherwise be easy:
  - look items up by DOI (and authors by ORCID) before creating anything;
  - one item per work: Zenodo versions are grouped under their concept DOI,
    so v0.2.0 and v0.2.1 of one deposit do not become two "replications";
  - idempotent: a statement already present is skipped, so the batch can be
    regenerated and re-run; a citation to a work created in the same batch is
    reported as pending and added on the next run;
  - a description that includes the DOI, because Wikidata rejects two items
    with the same label and description, and replications of one paper often
    share a title.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
import urllib.parse
from pathlib import Path

from rdflib import Dataset, URIRef

from .api import ApiError, canonical_uri, fetch_trig
from .chain import parse_published
from .grounding import GroundingError, _get, normalise_doi

CITO = "http://purl.org/spar/cito/"
NP = "http://www.nanopub.org/nschema#"
SL = "https://w3id.org/sciencelive/o/terms/"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
# The FORRT study type says which kind of work this is; the description says the same.
STUDY_KIND = {
    SL + "Reproduction-Study": "reproduction study",
    SL + "Replication-Study": "replication study",
    SL + "Reproduction-Replication-Study": "reproduction and replication study",
}
DEFAULT_KIND = "reproduction or replication study"
_NP_ID = re.compile(r"RA[A-Za-z0-9_-]{43}")
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
ZENODO_API = "https://zenodo.org/api/records/"

CITATION_INTENT = "Q96471816"
# Zenodo resource type -> Wikidata class. Software and data set both satisfy
# the "cites work" subject-type constraint (both are intellectual works).
ZENODO_CLASS = {"software": "Q7397", "dataset": "Q1172284", "publication": "Q591041"}
DEFAULT_CLASS = "Q55915575"  # scholarly work


# --- sources (each one is replaced by a stub in the tests) -------------------

def _json(url: str, params: dict | None = None) -> dict | None:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    try:
        status, body = _get(url, "application/json")
    except GroundingError:
        return None
    if status != 200 or not body:
        return None
    return json.loads(body)


def _assertion(uri: str) -> list[tuple[str, str, str]]:
    """The assertion graph of a nanopublication, as (s, p, o) strings."""
    ds = Dataset()
    ds.parse(data=fetch_trig(uri), format="trig")
    graph_name = None
    for g in ds.graphs():
        for o in g.objects(None, URIRef(NP + "hasAssertion")):
            graph_name = o
    if graph_name is None:
        return []
    return [(str(s), str(p), str(o)) for s, p, o in ds.graph(graph_name)]


def _intents() -> dict[str, str]:
    """CiTO URI -> citation-intent QID, read from Wikidata at run time.

    Wikidata is the authority for this mapping (each intent item carries an
    exact match, P2888, to its CiTO URI), so it is not copied here: a copy
    would go stale without saying so. Read through the main API rather than
    the query service, which times out more often.
    """
    qids: list[str] = []
    offset: int | None = 0
    while offset is not None:  # follow `continue`: the search pages its results
        search = _json(WIKIDATA_API, {"action": "query", "list": "search", "format": "json",
                                      "srlimit": "500", "sroffset": str(offset),
                                      "srsearch": f"haswbstatement:P31={CITATION_INTENT}"})
        qids += [h["title"] for h in (search or {}).get("query", {}).get("search", [])]
        offset = ((search or {}).get("continue") or {}).get("sroffset")
    mapping: dict[str, str] = {}
    for start in range(0, len(qids), 50):  # wbgetentities takes at most 50 ids
        data = _json(WIKIDATA_API, {"action": "wbgetentities", "format": "json",
                                    "props": "claims", "ids": "|".join(qids[start:start + 50])})
        for qid, entity in ((data or {}).get("entities") or {}).items():
            for prop in ("P2888", "P1709"):
                for claim in entity.get("claims", {}).get(prop, []):
                    uri = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value", "")
                    if isinstance(uri, str) and uri.startswith(CITO):
                        mapping[uri] = qid
    if not mapping:
        raise ApiError("could not read the CiTO citation-intent items from Wikidata")
    return mapping


def _find(prop: str, value: str) -> list[str]:
    """Items with `prop` = `value`. CirrusSearch covers all of Wikidata,
    including scholarly articles moved to the separate query service."""
    data = _json(WIKIDATA_API, {"action": "query", "list": "search", "format": "json",
                                "srsearch": f'haswbstatement:"{prop}={value}"'})
    return [h["title"] for h in (data or {}).get("query", {}).get("search", [])]


def _claims(qid: str) -> dict:
    data = _json(WIKIDATA_API, {"action": "wbgetentities", "ids": qid,
                                "props": "claims", "format": "json"})
    return ((data or {}).get("entities", {}).get(qid) or {}).get("claims", {})


def _zenodo(doi: str) -> dict | None:
    """Title, date, type, creators and concept DOI of a Zenodo record, else None."""
    if not doi.lower().startswith("10.5281/zenodo."):
        return None
    data = _json(ZENODO_API + doi.rsplit(".", 1)[-1])
    if not data:
        return None
    meta = data.get("metadata", {})
    concept = data.get("conceptrecid")
    return {
        "concept_doi": f"10.5281/zenodo.{concept}" if concept else doi,
        "title": meta.get("title", ""),
        "date": meta.get("publication_date", ""),
        "type": (meta.get("resource_type") or {}).get("type", ""),
        "creators": [{"name": _display_name(c.get("name", "")), "orcid": c.get("orcid") or ""}
                     for c in meta.get("creators", [])],
    }


# --- pure helpers --------------------------------------------------------------

def _display_name(name: str) -> str:
    """Zenodo's "Family, Given" -> Wikidata's "Given Family"."""
    if "," in name:
        family, given = (part.strip() for part in name.split(",", 1))
        return f"{given} {family}".strip()
    return name.strip()


def _doi(value: str) -> str | None:
    if "doi.org/" in value.lower() or value.lower().startswith("doi:") or value.startswith("10."):
        doi = normalise_doi(value)
        return doi if doi.startswith("10.") else None
    return None


def _np_url(uri: str) -> str:
    """The resolver form Scholia recognises: https://w3id.org/np/<id>."""
    return canonical_uri(uri).replace("http://purl.org/np/", "https://w3id.org/np/")


def _study_kind(study_uri: str) -> str:
    """'reproduction study', 'replication study', ... from the FORRT study's own type."""
    match = _NP_ID.search(study_uri or "")
    if not match:
        return ""
    try:
        triples = _assertion(f"https://w3id.org/np/{match.group(0)}")
    except ApiError:
        return ""
    types = {o for _, p, o in triples if p == RDF_TYPE}
    return next((kind for cls, kind in STUDY_KIND.items() if cls in types), "")


def _is_slug(title: str) -> bool:
    """A repository-style name rather than a title: no spaces at all."""
    return bool(title) and not any(c.isspace() for c in title.strip())


def _q(text: str) -> str:
    return '"' + text.replace('"', "'") + '"'


def _date(iso: str) -> str:
    return f"+{iso}T00:00:00Z/11"


def _has_citation(claims: dict, cited: str, intent: str | None) -> bool:
    for claim in claims.get("P2860", []):
        value = (claim.get("mainsnak", {}).get("datavalue") or {}).get("value") or {}
        if value.get("id") != cited:
            continue
        if intent is None:
            return True
        goals = claim.get("qualifiers", {}).get("P3712", [])
        if any((g.get("datavalue") or {}).get("value", {}).get("id") == intent for g in goals):
            return True
    return False


# --- the generator -------------------------------------------------------------

def cito_uris_from_published(published_path: str) -> list[str]:
    """Every nanopublication in a PUBLISHED.md ledger whose assertion uses CiTO."""
    path = Path(published_path)
    if path.is_dir():
        path = path / "PUBLISHED.md"
    uris = list(dict.fromkeys(parse_published(path.read_text(encoding="utf-8")).values()))
    return [u for u in uris if any(p.startswith(CITO) for _, p, _ in _assertion(u))]


def wikidata_cito(cito_uris: list[str], today: str = "") -> dict:
    """Build the QuickStatements batch mirroring these CiTO nanopublications.

    Returns `quickstatements` (the batch, one statement per line), `works` (one
    entry per replication: existing item or new, and the citations planned),
    `pending` (citations to works created by this same batch: re-run after
    submitting), and `skipped` (what could not be mirrored, and why).
    """
    today = today or datetime.date.today().isoformat()
    intents = _intents()
    works: dict[str, dict] = {}
    skipped: list[str] = []

    for cito_uri in cito_uris:
        for subject, predicate, obj in _assertion(cito_uri):
            if not predicate.startswith(CITO):
                continue
            citing_doi, outcome = _doi(subject), None
            if citing_doi is None:
                # The citing side is the Outcome nanopub; the work is its repository.
                outcome = subject
                outcome_triples = _assertion(subject)
                repo = next((o for _, p, o in outcome_triples
                             if p == SL + "hasOutcomeRepository"), "")
                outcome_label = next((o for _, p, o in outcome_triples if p == RDFS_LABEL), "")
                study = next((o for _, p, o in outcome_triples if p == SL + "isOutcomeOf"), "")
                outcome_kind = _study_kind(study)
                citing_doi = _doi(repo)
                if citing_doi is None:
                    skipped.append(f"{cito_uri}: the outcome repository is not a DOI ({repo or 'none'})")
                    continue
            cited_doi = _doi(obj)
            if cited_doi is None:
                skipped.append(f"{cito_uri}: cited target is not a DOI ({obj})")
                continue
            meta = _zenodo(citing_doi)
            work_doi = meta["concept_doi"] if meta else citing_doi
            work = works.setdefault(work_doi.upper(), {
                "doi": work_doi, "versions": set(), "meta": meta,
                "outcomes": set(), "labels": [], "kinds": [], "citations": []})
            work["versions"].add(citing_doi)
            if outcome:
                work["outcomes"].add(outcome)
                if outcome_label and outcome_label not in work["labels"]:
                    work["labels"].append(outcome_label)
                if outcome_kind and outcome_kind not in work["kinds"]:
                    work["kinds"].append(outcome_kind)
            work["citations"].append({"cited_doi": cited_doi, "cito": predicate,
                                      "intent": intents.get(predicate), "nanopub": cito_uri})

    lines: list[str] = []
    report: list[dict] = []
    pending: list[str] = []
    for key, work in works.items():
        found = _find("P356", key)
        for version in sorted(work["versions"]):
            found = found or _find("P356", version.upper())
        meta = work["meta"]
        entry = {"doi": work["doi"], "versions": sorted(work["versions"]),
                 "item": found[0] if found else None, "title": (meta or {}).get("title", ""),
                 "authors": [], "citations": []}
        report.append(entry)
        subject = found[0] if found else "LAST"
        existing = _claims(found[0]) if found else {}

        if not found:
            if not meta:
                skipped.append(f"{work['doi']}: no Wikidata item, and no Zenodo record to create one from")
                continue
            title = meta["title"]
            # A deposit named after its repository ("my-replication-repo") makes a
            # useless label; the Outcome nanopub's own label says what was done.
            # The title statement (P1476) stays the deposit's real title.
            label = work["labels"][0] if _is_slug(title) and work["labels"] else title
            if label != title:
                entry["title"] = label
                entry["note"] = f"Zenodo title {title!r} looks like a repository name; label taken from the Outcome nanopublication"
            # Wikidata is a secondary knowledge base: every statement cites where it
            # comes from. Here that is the Zenodo record the facts were read from.
            record = sorted(work["versions"])[-1].rsplit(".", 1)[-1]
            source = f"\tS854\t{_q(f'https://zenodo.org/records/{record}')}\tS813\t{_date(today)}"
            # Descriptions are short prose, not a place for identifiers; the DOI is
            # a statement (P356). Year and first author keep same-label items apart.
            first = meta["creators"][0]["name"] if meta["creators"] else ""
            kind = work["kinds"][0] if len(work["kinds"]) == 1 else DEFAULT_KIND
            description = (f"{kind} by {first} ({meta['date'][:4]})"
                           if first else f"{kind} ({meta['date'][:4]})")
            lines += ["CREATE",
                      f"LAST\tLen\t{_q(label[:250])}",
                      f"LAST\tDen\t{_q(description)}",
                      f"LAST\tP31\t{ZENODO_CLASS.get(meta['type'], DEFAULT_CLASS)}{source}",
                      f"LAST\tP1476\ten:{_q(title)}{source}",
                      f"LAST\tP356\t{_q(key)}{source}"]
            if meta["date"]:
                lines.append(f"LAST\tP577\t{_date(meta['date'])}{source}")
            for n, creator in enumerate(meta["creators"], 1):
                person = _find("P496", creator["orcid"]) if creator["orcid"] else []
                if person:
                    lines.append(f'LAST\tP50\t{person[0]}\tP1545\t"{n}"{source}')
                else:
                    lines.append(f'LAST\tP2093\t{_q(creator["name"])}\tP1545\t"{n}"{source}')
                entry["authors"].append({"name": creator["name"], "item": person[0] if person else None})
            for outcome in sorted(work["outcomes"]):
                lines.append(f"LAST\tP973\t{_q(_np_url(outcome))}")

        planned: set[tuple[str, str | None]] = set()
        for citation in work["citations"]:
            cited_meta = _zenodo(citation["cited_doi"])
            cited_doi = cited_meta["concept_doi"] if cited_meta else citation["cited_doi"]
            cited = _find("P356", cited_doi.upper())
            relation = citation["cito"].rsplit("/", 1)[-1]
            if not cited:
                if cited_doi.upper() in works:
                    pending.append(f"{work['doi']} {relation} {cited_doi}: created in this batch; re-run after submitting")
                else:
                    skipped.append(f"{work['doi']} {relation} {cited_doi}: the cited work has no Wikidata item yet")
                continue
            if (cited[0], citation["intent"]) in planned:
                continue
            planned.add((cited[0], citation["intent"]))
            if _has_citation(existing, cited[0], citation["intent"]):
                entry["citations"].append({"cited": cited[0], "relation": relation, "status": "already present"})
                continue
            line = f"{subject}\tP2860\t{cited[0]}"
            if citation["intent"]:
                line += f"\tP3712\t{citation['intent']}"
            elif relation != "cites":
                skipped.append(f"{work['doi']}: no Wikidata intent for cito:{relation}; added as plain 'cites work'")
            line += f"\tS12545\t{_q(_np_url(citation['nanopub']))}\tS813\t{_date(today)}"
            lines.append(line)
            entry["citations"].append({"cited": cited[0], "cited_doi": cited_doi, "relation": relation,
                                       "intent": citation["intent"], "nanopub": _np_url(citation["nanopub"]),
                                       "status": "added"})

    return {"quickstatements": "\n".join(lines) + ("\n" if lines else ""),
            "statements": len(lines), "works": report, "pending": pending, "skipped": skipped}


def _report_markdown(result: dict) -> str:
    out = ["# Wikidata mirror of the CiTO nanopublications", "",
           "Review this, then submit `quickstatements.txt` at "
           "https://quickstatements.toolforge.org (log in with your own Wikidata account).", ""]
    for work in result["works"]:
        status = f"existing item {work['item']}" if work["item"] else "NEW ITEM"
        out.append(f"## {work['title'] or work['doi']}")
        out.append(f"- DOI {work['doi']} ({status}); versions cited: {', '.join(work['versions'])}")
        if work.get("note"):
            out.append(f"- note: {work['note']}")
        for author in work["authors"]:
            out.append(f"- author: {author['name']} -> {author['item'] or 'name string (no Wikidata item)'}")
        for c in work["citations"]:
            out.append(f"- cites {c['cited']} as {c['relation']}: {c['status']}")
        out.append("")
    for title, items in (("Pending (re-run after submitting)", result["pending"]),
                         ("Skipped", result["skipped"])):
        if items:
            out += [f"## {title}", *[f"- {i}" for i in items], ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="forrt-wikidata-cito",
        description="Write a QuickStatements batch mirroring CiTO nanopublications into Wikidata. "
                    "Never edits Wikidata: review the report, then submit the batch yourself.")
    parser.add_argument("uris", nargs="*", help="CiTO nanopublication URIs")
    parser.add_argument("--published", help="a nanopubs/PUBLISHED.md ledger (or its directory)")
    parser.add_argument("--out", default="wikidata", help="output directory (default: wikidata)")
    args = parser.parse_args(argv)

    try:
        uris = list(args.uris)
        if args.published:
            uris += cito_uris_from_published(args.published)
        if not uris:
            parser.error("give CiTO nanopublication URIs or --published")
        result = wikidata_cito(uris)
    except (ApiError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "quickstatements.txt").write_text(result["quickstatements"], encoding="utf-8")
    (out / "report.md").write_text(_report_markdown(result), encoding="utf-8")
    print(_report_markdown(result))
    print(f"{result['statements']} QuickStatements lines -> {out / 'quickstatements.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

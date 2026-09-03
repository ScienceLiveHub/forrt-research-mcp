"""The live FORRT template schemas, and the controlled vocabularies in them.

A nanopub template *is* the schema for its chain step: the field ids, the
prompts, the length caps, and the exact set of allowed values for every choice
field. So "never invent a field name" and "never invent a claim type" stop being
prose rules an agent might skip, and become a lookup that can only return real
values.

Two sources, in order:

1. **Live** — fetch the template nanopub's TriG from the nanopub network and
   parse it. Authoritative, and what publishing should be checked against.
2. **Bundled snapshot** — a vendored copy of the extracted specs, used when the
   network is unavailable.

Every result says which was used, and a live result reports whether it has
drifted from the snapshot. A caller is never left guessing whether it is
looking at current values.

Vocabularies live in two shapes. Most are inline `possibleValue` enumerations on
a restricted-choice field. The CiTO relations are not: that field points at a
separate *value-list nanopub* via `possibleValuesFrom`, which is a flat set of
`<uri> rdfs:label "…"` triples. Both are resolved here.
"""
from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

from .api import ApiError, fetch_trig
from .template_spec import parse_template, spec_to_dict

# vocabulary name -> (chain step, field id). Each names a restricted-choice
# field on a real template; nothing here is a hand-copied list of terms.
#
# Note `pico_question_type` is PICO-specific on purpose. Step 01 has three
# alternative anchors and they are not variations of one form:
#   01_quote  paper + quotation + comment          (no type, no label)
#   01_pico   + type [5 options] + P/I/C/O descriptions
#   01_pcc    + P/C/C descriptions, and NO type field at all
# A PCC question therefore has no type vocabulary to look up, and naming this
# `question_type` would imply otherwise.
VOCABULARIES: dict[str, tuple[str, str]] = {
    "claim_type": ("03_claim", "forrtType"),
    "study_type": ("04_study", "type"),
    "validation_status": ("05_outcome", "validationStatus"),
    "confidence_level": ("05_outcome", "confidenceLevel"),
    "cito_relation": ("06_citation", "cites"),
    "pico_question_type": ("01_pico", "type"),
}

# Alternative anchors for step 01. All three are valid chain starts; which one
# fits is independent of whether the study is a reproduction, a replication or
# original research.
ANCHOR_STEPS = ("01_quote", "01_pico", "01_pcc")


def _data(name: str) -> dict:
    with resources.files(f"{__package__}.data").joinpath(name).open() as fh:
        return json.load(fh)


@lru_cache(maxsize=1)
def registry() -> dict:
    """The pinned template URI for each chain step."""
    return _data("registry.json")


@lru_cache(maxsize=1)
def snapshot() -> dict:
    """Vendored field specs, keyed by chain step."""
    return _data("fields.snapshot.json").get("steps", {})


def steps() -> list[str]:
    return sorted(registry()["steps"])


def _resolve_step(step: str) -> str:
    """Accept '05_outcome', '05', or 'outcome'."""
    known = registry()["steps"]
    if step in known:
        return step
    candidates = [s for s in known
                  if s.split("_", 1)[0] == step or s.split("_", 1)[1] == step]
    if len(candidates) == 1:
        return candidates[0]
    raise ApiError(
        f"unknown chain step {step!r}. Known steps: {', '.join(sorted(known))}"
    )


@lru_cache(maxsize=32)
def _live_spec(template_uri: str) -> dict:
    return spec_to_dict(parse_template(fetch_trig(template_uri), template_uri))


def template_fields(step: str, *, live: bool = True) -> dict:
    """The field specification for one chain step.

    Falls back to the bundled snapshot when the network is unavailable, and
    always reports which source was used.
    """
    key = _resolve_step(step)
    meta = registry()["steps"][key]
    uri = meta["current"]

    if live:
        try:
            spec = _live_spec(uri)
        except ApiError as e:
            fallback = snapshot().get(key)
            if fallback is None:
                raise
            return {
                "step": key, "templateUri": uri, "source": "bundled-snapshot",
                "warning": (f"could not fetch the live template ({e}); these are "
                            "vendored values and may be out of date"),
                **fallback,
            }
        drifted = snapshot().get(key) != spec
        return {
            "step": key, "templateUri": uri, "source": "live",
            "driftedFromSnapshot": drifted,
            **({"note": ("the live template no longer matches this package's "
                         "vendored snapshot — the live values are authoritative, "
                         "and forrt-research-mcp should be re-vendored")}
               if drifted else {}),
            **spec,
        }

    fallback = snapshot().get(key)
    if fallback is None:
        raise ApiError(f"no vendored snapshot for step {key!r}")
    return {"step": key, "templateUri": uri, "source": "bundled-snapshot", **fallback}


def _value_list(uri: str) -> list[dict]:
    """Terms from a value-list nanopub — a flat set of `<uri> rdfs:label` triples.

    Used by choice fields whose options are maintained separately from the
    template (the CiTO relations are ~40 terms shared across templates).
    """
    from rdflib import RDFS, Dataset, Graph

    dataset = Dataset()
    dataset.parse(data=fetch_trig(uri), format="trig")
    graph = Graph()
    for ctx in dataset.graphs():
        graph += ctx

    out = [{"uri": str(s), "label": str(o)}
           for s, o in graph.subject_objects(RDFS.label)
           if str(s) != uri and not str(s).startswith(f"{uri}#")]
    out.sort(key=lambda t: t["uri"])
    return out


def vocabulary(name: str, *, live: bool = True) -> dict:
    """The allowed values for one controlled vocabulary, from its template.

    Never a hand-maintained list: each vocabulary names a real restricted-choice
    field, and the terms come from that field's enumeration or from the
    value-list nanopub it points at.
    """
    if name not in VOCABULARIES:
        raise ApiError(
            f"unknown vocabulary {name!r}. Known: {', '.join(sorted(VOCABULARIES))}"
        )
    step, field_id = VOCABULARIES[name]
    spec = template_fields(step, live=live)

    field = next((f for f in spec.get("fields", []) if f["id"] == field_id), None)
    if field is None:
        raise ApiError(
            f"field {field_id!r} is no longer on the {step} template — the "
            f"vocabulary mapping in forrt-research-mcp needs updating"
        )

    values = list(field.get("possible_values") or [])
    source = spec["source"]

    # Options held in a separate value-list nanopub (CiTO relations).
    if not values and field.get("values_from"):
        if not live:
            return {
                "vocabulary": name, "step": step, "field": field_id,
                "label": field.get("label", ""), "values": [],
                "source": "unavailable-offline",
                "warning": ("this vocabulary lives in a separate value-list "
                            f"nanopub ({field['values_from'][0]}) and cannot be "
                            "resolved offline; call again with live=True"),
            }
        try:
            values = _value_list(field["values_from"][0])
            source = "live-value-list"
        except ApiError as e:
            return {
                "vocabulary": name, "step": step, "field": field_id,
                "label": field.get("label", ""), "values": [],
                "source": "unavailable",
                "warning": f"could not fetch the value list: {e}",
            }

    return {
        "vocabulary": name,
        "step": step,
        "field": field_id,
        "label": field.get("label", ""),
        "required": field.get("required", True),
        "templateUri": spec["templateUri"],
        "source": source,
        "count": len(values),
        "values": values,
    }

"""Check `nanopubs/chain-draft.json` — the artifact that actually gets published.

The markdown drafts under `nanopubs/drafts/` are the *authoring* format.
`build_chain_draft.py` turns them (plus `CITATION.cff` and the templates) into
`chain-draft.json`, which is pushed and handed to the Science Live chain wizard
by URL. The wizard pre-fills each step from it, a human reviews and publishes,
and each published URI is carried into the next step's back-reference.

So this file — not the markdown — is what a human sees and signs. Validating the
markdown checks the input; validating this checks the artifact.

Three things make a naive checker useless here, and each is handled explicitly:

1. **`prefill` keys are the platform's form-field names, not template placeholder
   names.** They usually coincide, but not always: `06_citation` uses
   `st02: [{cites, cited}]` in place of flat `cites`/`cited`, and several steps
   use array- or object-shaped selection widgets.
2. **Carry-forward fields are deliberately absent.** `02_aida` has no `project`,
   `03_claim` no `aida`, `04_study` no `claim` — the wizard fills them from the
   previously published step. `carry_forward[]` names exactly which, so they are
   exempted from the required-field check rather than reported missing.
3. **A stale `template_uri` is invisible in the file but fatal at the wizard.**
   If a template has been superseded upstream, the wizard pre-fills the old form.
   Comparing against the registry's current URI is the check that catches it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .api import ApiError
from .grounding import GroundingError, resolve_doi
from .templates import VOCABULARIES, matches_term, registry, template_fields, vocabulary

SCHEMA_KIND = "forrt-chain-draft"
KNOWN_SCHEMA_VERSIONS = ("1.0",)

# Form fields whose prefill key is NOT a template placeholder name, with the
# shape the platform component expects. From docs/chain-draft-contract.md
# § Repeatable and complex fields, verified against the platform's template
# components. `min_items` of 1 means the form will not submit without an entry.
COMPLEX_FIELDS: dict[tuple[str, str], dict] = {
    ("06_citation", "st02"): {"shape": "list[obj]", "keys": ("cites", "cited"),
                              "min_items": 1, "replaces": ("cites", "cited")},
    ("08_synthesis", "sources"): {"shape": "list[obj]", "keys": ("source",),
                                  "min_items": 1},
    ("08_synthesis", "topicSelection"): {"shape": "list[obj]",
                                         "keys": ("uri", "label"), "min_items": 1},
    ("02_aida", "st3"): {"shape": "list[obj]", "keys": ("dataset",)},
    ("02_aida", "st4"): {"shape": "list[obj]", "keys": ("publication",)},
    ("02_aida", "topic"): {"shape": "list[obj]", "keys": ("uri", "label")},
    ("04_study", "keywordSelection"): {"shape": "list[obj]",
                                       "keys": ("uri", "label")},
    # NOT an array — a single object. The one asymmetry in the contract.
    ("04_study", "disciplineSelection"): {"shape": "obj", "keys": ("uri", "label")},
    ("07_research_software", "datasets"): {"shape": "list[str]"},
    ("07_research_software", "researchOutputs"): {"shape": "list[str]"},
}

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s)\]\"'<>,;`*]+")
_PLACEHOLDER_RE = re.compile(r"«[^»]*»|\{\{[A-Z0-9_]+\}\}|_not yet published_")
# YYYY-MM-DD; the wizard converts this to a JS Date before passing it on.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _finding(severity: str, check: str, message: str, **extra) -> dict:
    return {"severity": severity, "check": check, "message": message, **extra}


def _carry_forward_targets(draft: dict) -> dict[str, set[str]]:
    """step -> field names the wizard fills, which must NOT be prefilled."""
    targets: dict[str, set[str]] = {}
    for edge in draft.get("carry_forward") or []:
        into, field = edge.get("into"), edge.get("field")
        if into and field:
            targets.setdefault(into, set()).add(field)
    return targets


def _check_envelope(draft: dict) -> list[dict]:
    out: list[dict] = []
    if draft.get("kind") != SCHEMA_KIND:
        out.append(_finding("error", "envelope",
                            f"`kind` is {draft.get('kind')!r}, expected "
                            f"{SCHEMA_KIND!r} — the wizard will not load this"))
    version = str(draft.get("schema_version", ""))
    if version not in KNOWN_SCHEMA_VERSIONS:
        out.append(_finding("warning", "envelope",
                            f"schema_version {version!r} is not one this checker "
                            f"knows ({', '.join(KNOWN_SCHEMA_VERSIONS)}); the "
                            f"contract may have moved on"))
    if not draft.get("steps"):
        out.append(_finding("error", "envelope", "`steps` is empty — nothing to publish"))
    return out


def _check_template_uri(step: str, declared: str) -> list[dict]:
    """A superseded template_uri pre-fills the wrong form at the wizard."""
    known = registry()["steps"]
    if step not in known:
        return [_finding("warning", "template-uri",
                         f"step {step!r} is not in the template registry; its "
                         f"template_uri cannot be checked", step=step)]
    current = known[step]["current"]
    if declared == current:
        return [_finding("info", "template-uri",
                         f"{step} targets the current template", step=step)]

    superseded = registry().get("superseded", {}).get(step, [])
    if declared in superseded:
        return [_finding("error", "template-uri",
                         f"{step} targets a SUPERSEDED template ({declared}). The "
                         f"wizard would pre-fill the old form. Re-run "
                         f"`pixi run build-chain-draft` to pick up {current}",
                         step=step, declared=declared, current=current)]
    return [_finding("error", "template-uri",
                     f"{step} targets {declared}, which is neither the current "
                     f"template nor a known superseded one. Current is {current}",
                     step=step, declared=declared, current=current)]


def _check_shape(step: str, key: str, value, spec: dict) -> list[dict]:
    """A complex field must match the shape the platform component expects."""
    shape = spec["shape"]
    where = f"{step}.{key}"

    if shape == "obj":
        if not isinstance(value, dict):
            return [_finding("error", "shape",
                             f"{where} must be a single object "
                             f"{{{', '.join(spec['keys'])}}}, not a "
                             f"{type(value).__name__} — it is the one selection "
                             f"field that is NOT an array", step=step, field=key)]
        missing = [k for k in spec["keys"] if k not in value]
        return [_finding("error", "shape",
                         f"{where} is missing {missing}", step=step, field=key)] if missing else []

    if not isinstance(value, list):
        return [_finding("error", "shape",
                         f"{where} must be a list, not a {type(value).__name__}",
                         step=step, field=key)]

    out: list[dict] = []
    if len(value) < spec.get("min_items", 0):
        out.append(_finding("error", "shape",
                            f"{where} needs at least {spec['min_items']} entry — "
                            f"the form will not submit without it",
                            step=step, field=key))
    if shape == "list[str]":
        if any(not isinstance(v, str) for v in value):
            out.append(_finding("error", "shape",
                                f"{where} must be a list of plain strings",
                                step=step, field=key))
        return out

    for i, entry in enumerate(value):
        if not isinstance(entry, dict):
            out.append(_finding("error", "shape",
                                f"{where}[{i}] must be an object with "
                                f"{{{', '.join(spec['keys'])}}}", step=step, field=key))
            continue
        missing = [k for k in spec["keys"] if not entry.get(k)]
        if missing:
            out.append(_finding("error", "shape",
                                f"{where}[{i}] is missing or empty: {missing}",
                                step=step, field=key))
    return out


def _check_step(step_obj: dict, carried: set[str], *, live: bool) -> list[dict]:
    step = step_obj.get("step", "")
    prefill = step_obj.get("prefill") or {}
    out: list[dict] = _check_template_uri(step, step_obj.get("template_uri", ""))

    try:
        spec = template_fields(step, live=live)
    except ApiError as e:
        return out + [_finding("warning", "template",
                               f"could not load the template for {step}: {e}",
                               step=step)]

    by_id = {f["id"]: f for f in spec["fields"]}
    complex_here = {k for (s, k) in COMPLEX_FIELDS if s == step}
    # Placeholder fields a complex form-field stands in for (flat cites/cited).
    replaced = {r for (s, k), v in COMPLEX_FIELDS.items() if s == step
                for r in v.get("replaces", ())}

    for key, value in prefill.items():
        spec_complex = COMPLEX_FIELDS.get((step, key))
        if spec_complex:
            out += _check_shape(step, key, value, spec_complex)
            continue
        if key not in by_id:
            out.append(_finding("error", "field-exists",
                                f"{step}.{key!r} is neither a field on the template "
                                f"nor a known form-field for this step. The wizard "
                                f"will drop it. Real fields: {', '.join(by_id)}",
                                step=step, field=key))
            continue
        out += _check_value(step, key, value, by_id[key], live=live)

    # Required fields must be present unless carried forward or replaced.
    missing = [fid for fid, f in by_id.items()
               if f.get("required") and fid not in prefill
               and fid not in carried and fid not in replaced
               and fid not in complex_here]
    for fid in missing:
        out.append(_finding("error", "required",
                            f"{step}.{fid} is required by the template but is not "
                            f"prefilled and is not carried forward — the wizard "
                            f"will show it blank", step=step, field=fid))
    return out


def _check_value(step: str, key: str, value, field: dict, *, live: bool) -> list[dict]:
    out: list[dict] = []
    if not isinstance(value, str):
        return [_finding("warning", "value",
                         f"{step}.{key} is a {type(value).__name__}; this checker "
                         f"only validates string values for simple fields",
                         step=step, field=key)]

    if _PLACEHOLDER_RE.search(value):
        out.append(_finding("error", "placeholder",
                            f"{step}.{key} still holds a placeholder ({value[:60]}) "
                            f"— it would be signed literally",
                            step=step, field=key))
        return out

    regex = field.get("regex")
    if regex:
        try:
            if not re.fullmatch(regex, value, re.S):
                out.append(_finding("error", "constraint",
                                    f"{step}.{key} violates the template constraint "
                                    f"(regex {regex!r}); {len(value)} characters",
                                    step=step, field=key, length=len(value)))
        except re.error:
            pass

    if key == "date" and not _DATE_RE.match(value):
        out.append(_finding("error", "value",
                            f"{step}.date must be YYYY-MM-DD (the wizard converts "
                            f"it to a Date); got {value!r}", step=step, field=key))

    vocab = next((n for n, (s, f) in VOCABULARIES.items()
                  if s == step and f == key), None)
    if vocab:
        allowed = vocabulary(vocab, live=live).get("values") or []
        if allowed and not any(matches_term(value, a) for a in allowed):
            out.append(_finding("error", "vocabulary",
                                f"{step}.{key} = {value!r} is not one of the "
                                f"{len(allowed)} values this field accepts",
                                step=step, field=key, vocabulary=vocab))
    return out


def _check_carry_forward(draft: dict) -> list[dict]:
    """The topology the wizard walks: each edge must join two real steps."""
    out: list[dict] = []
    steps = [s.get("step") for s in draft.get("steps") or []]
    order = {name: i for i, name in enumerate(steps)}
    for edge in draft.get("carry_forward") or []:
        src, into, field = edge.get("from"), edge.get("into"), edge.get("field")
        if src not in order or into not in order:
            out.append(_finding("error", "carry-forward",
                                f"edge {src} -> {into} names a step that is not in "
                                f"this draft", edge=edge))
            continue
        if order[src] >= order[into]:
            out.append(_finding("error", "carry-forward",
                                f"edge {src} -> {into} runs backwards; the wizard "
                                f"publishes in order and cannot carry a URI from a "
                                f"step it has not reached", edge=edge))
        prefill = next((s.get("prefill") or {} for s in draft["steps"]
                        if s.get("step") == into), {})
        if field in prefill:
            out.append(_finding("warning", "carry-forward",
                                f"{into}.{field} is both prefilled and carried "
                                f"forward from {src}; the wizard's value wins, so "
                                f"the prefilled one is dead weight", edge=edge))
    return out


def _check_dois(draft: dict) -> list[dict]:
    out: list[dict] = []
    blob = json.dumps(draft)
    found = set()
    for m in _DOI_RE.finditer(blob):
        if blob[m.end():m.end() + 1] == "<":
            continue
        found.add(m.group(0).rstrip('.,;:`*)]}>"\\'))
    for doi in sorted(found):
        try:
            result = resolve_doi(doi)
        except GroundingError as e:
            out.append(_finding("error", "doi", f"{doi} is malformed: {e}", doi=doi))
            continue
        if result["resolves"]:
            out.append(_finding("info", "doi", f"{doi} -> {result['title'][:60]}",
                                doi=doi))
        elif result["status"] == 404:
            out.append(_finding("error", "doi",
                                f"{doi} is NOT REGISTERED — it would be signed into "
                                f"the chain", doi=doi))
        else:
            out.append(_finding("warning", "doi",
                                f"{doi} unconfirmed (HTTP {result['status']}); may "
                                f"be transient", doi=doi))
    return out


def validate_chain_draft(path: str, *, live: bool = True) -> dict:
    """Check a `chain-draft.json` before it is handed to the chain wizard."""
    target = Path(path).expanduser()
    if target.is_dir():
        target = target / "chain-draft.json"
    if not target.is_file():
        raise ApiError(f"no chain draft at {target}")

    try:
        draft = json.loads(target.read_text())
    except json.JSONDecodeError as e:
        raise ApiError(f"{target} is not valid JSON: {e}") from e

    findings = _check_envelope(draft)
    if draft.get("steps"):
        carried = _carry_forward_targets(draft)
        for step_obj in draft["steps"]:
            findings += _check_step(step_obj, carried.get(step_obj.get("step", ""), set()),
                                    live=live)
        findings += _check_carry_forward(draft)
        findings += _check_dois(draft)

    counts = {level: sum(1 for f in findings if f["severity"] == level)
              for level in ("error", "warning", "info")}
    return {
        "draft": str(target),
        "chainShape": draft.get("chain_shape", ""),
        "schemaVersion": draft.get("schema_version", ""),
        "steps": [s.get("step") for s in draft.get("steps") or []],
        "readyForWizard": counts["error"] == 0,
        "counts": counts,
        "findings": sorted(findings,
                           key=lambda f: ("error", "warning", "info").index(f["severity"])),
    }

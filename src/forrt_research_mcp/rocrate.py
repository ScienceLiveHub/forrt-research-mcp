"""validate_rocrate: check an RO-Crate against an RO-Crate profile, and whether it is ready as a FAIR Digital Object.

Two layers, both deterministic:

1. **Specification** — rocrate-validator (roc-validator) against the requested profile and severity
   (default RO-Crate 1.2, REQUIRED). `passed` is its verdict.
2. **FDO readiness** — what a replication crate needs beyond the specification to be deposited and found as a
   FAIR Digital Object (the conventions of forrt-replication-template's scripts/build_rocrate.py): a persistent
   identifier, a licence, a main workflow, conformance to a community FAIR Implementation Profile, the FORRT
   nanopublication chain, and no `ro-crate-metadata.json` among its parts (ROHub reserves that name: listing it
   breaks the Research Object's download and preview). These are advisory; they do not change `passed`.
"""

from __future__ import annotations

import json
from pathlib import Path

NANOPUB = ("w3id.org/np/", "w3id.org/sciencelive/np/")
LEVELS = ("REQUIRED", "RECOMMENDED", "OPTIONAL")


class RoCrateError(ValueError):
    """The path is not an RO-Crate the validator can read."""


def _crate_root(path: str) -> Path:
    p = Path(path).expanduser()
    if p.is_file() and p.name == "ro-crate-metadata.json":
        p = p.parent
    if not (p / "ro-crate-metadata.json").is_file():
        raise RoCrateError(f"no ro-crate-metadata.json in {p}")
    return p


def _ids(value) -> list[str]:
    items = value if isinstance(value, list) else [value] if value else []
    return [v["@id"] if isinstance(v, dict) else v for v in items]


def _types(entity: dict) -> list[str]:
    t = entity.get("@type", [])
    return t if isinstance(t, list) else [t]


def fdo_checks(metadata: dict) -> list[dict]:
    """Advisory checks a crate needs to be deposited and reused as a FAIR Digital Object."""
    graph = {e["@id"]: e for e in metadata.get("@graph", [])}
    descriptor = graph.get("ro-crate-metadata.json", {})
    root = graph.get(descriptor.get("about", {}).get("@id", "./"), {})
    main = root.get("mainEntity", {})
    main_id = main.get("@id") if isinstance(main, dict) else None
    fips = [u for u in _ids(root.get("conformsTo")) if any(n in u for n in NANOPUB)]
    chain = [u for u in _ids(root.get("subjectOf")) if any(n in u for n in NANOPUB)]
    checks = [
        ("persistent_identifier", bool(root.get("identifier")),
         "root has an identifier (the release's version DOI)", "add the version DOI as the root's identifier"),
        ("licence", bool(root.get("license")), "root has a licence", "add a licence to the root"),
        ("main_workflow", bool(main_id) and "ComputationalWorkflow" in _types(graph.get(main_id, {})),
         f"mainEntity {main_id} is a ComputationalWorkflow",
         "set mainEntity to the workflow, typed ComputationalWorkflow"),
        ("community_fip", bool(fips), f"conforms to {len(fips)} FAIR Implementation Profile nanopublication(s)",
         "add the community FIP nanopublication to conformsTo (rocrate.json 'profiles')"),
        ("forrt_chain", bool(chain), f"subjectOf lists {len(chain)} FORRT nanopublication(s)",
         "list the published chain (nanopubs/PUBLISHED.md) in subjectOf"),
        ("rohub_safe", "ro-crate-metadata.json" not in _ids(root.get("hasPart")),
         "ro-crate-metadata.json is not listed as a part",
         "remove ro-crate-metadata.json from hasPart: ROHub reserves that name"),
    ]
    return [{"check": name, "ok": ok, "detail": ok_text if ok else fix} for name, ok, ok_text, fix in checks]


def validate_rocrate(path: str, profile: str = "ro-crate-1.2", level: str = "REQUIRED",
                     metadata_only: bool = False) -> dict:
    """Validate the crate at `path` (its directory or its ro-crate-metadata.json)."""
    from rocrate_validator import models, services  # heavy import, only when the tool is used

    level = level.upper()
    if level not in LEVELS:
        raise RoCrateError(f"level must be one of {', '.join(LEVELS)}")
    root = _crate_root(path)
    try:
        metadata = json.loads((root / "ro-crate-metadata.json").read_text())
    except json.JSONDecodeError as e:
        raise RoCrateError(f"ro-crate-metadata.json is not valid JSON: {e}") from e
    try:
        result = services.validate(models.ValidationSettings(
            rocrate_uri=str(root), profile_identifier=profile, requirement_severity=models.Severity[level],
            metadata_only=metadata_only, offline=True))
    except Exception as e:  # unknown profile, unreadable crate
        raise RoCrateError(f"validator could not run: {e}") from e
    issues = [{"severity": i.severity.name, "check": i.check.identifier, "name": i.check.name,
               "message": i.message, "entity": getattr(i, "violatingEntity", None)}
              for i in result.get_issues()]
    fdo = fdo_checks(metadata)
    missing = [c["check"] for c in fdo if not c["ok"]]
    return {
        "path": str(root), "profile": profile, "level": level, "passed": result.passed(),
        "issues": issues, "fdo_checks": fdo, "fdo_ready": not missing,
        "summary": (f"{'valid' if result.passed() else 'NOT valid'} against {profile} at {level} level "
                    f"({len(issues)} issue(s)); FDO readiness: "
                    + ("all checks pass" if not missing else "missing " + ", ".join(missing))),
    }

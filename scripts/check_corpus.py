#!/usr/bin/env python3
"""Run every tool against every real artefact that can be found.

The test suite is hermetic: it pins behaviour against recorded data, which
prevents regressions but *cannot discover a wrong premise*. Every serious bug in
this server came from a wrong premise, and every one surfaced only when the
tools met real data they had not seen:

    Cloudflare answers urllib's default User-Agent with 403
    `python -m build` failed on every branch, including main
    drafts are keyed by `###` heading, not by `<!-- field: -->` markers
    Wikidata QIDs live in chain-draft.json, not in the drafts

So "have we tested comprehensively?" must not be a judgement call. This script
makes it a computation with a visible denominator: it discovers every repository
under a root that has a `nanopubs/` directory, runs the applicable tools against
each artefact, and prints what passed out of what was found.

    python scripts/check_corpus.py /path/to/root
    python scripts/check_corpus.py /path/to/root --offline   # skip network checks

**Findings are not automatically failures.** A draft with an empty required
field is a true finding about the draft. What this script treats as a *tool*
problem — and exits non-zero for — is a crash or a parse failure, because those
mean the tool could not read a real artefact at all. Everything else is printed
for a human to triage.

The denominator is only as good as what is on disk. If a chain exists that this
root does not contain, it is not covered, and no green run says otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from forrt_research_mcp.api import ApiError  # noqa: E402
from forrt_research_mcp.chain import verify_chain  # noqa: E402
from forrt_research_mcp.chain_draft import validate_chain_draft  # noqa: E402
from forrt_research_mcp.drafts import validate_drafts  # noqa: E402
from forrt_research_mcp.templates import steps, template_fields  # noqa: E402


class Tally:
    """Counts with a visible denominator, and tool failures kept separate.

    The denominator counts only artefacts that EXIST to be checked. A repo with
    an empty ledger has no chain, so counting it as an unclean chain would say
    something false — a first version printed "3/12 clean" for 4 published
    chains of which 3 were green, which reads as nine broken chains. Skipped
    rows are reported beside the count, never inside it, and detail lines hang
    off their row rather than becoming rows of their own.
    """

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.tool_failures: list[str] = []

    def add(self, artefact: str, where: str, status: str, detail: str = "",
            notes: list[str] | None = None) -> None:
        self.rows.append({"artefact": artefact, "where": where, "status": status,
                          "detail": detail, "notes": notes or []})

    def fail(self, what: str) -> None:
        self.tool_failures.append(what)

    def of(self, artefact: str) -> list[dict]:
        return [r for r in self.rows if r["artefact"] == artefact]

    def counts(self, artefact: str) -> tuple[int, int, int]:
        """(clean, checkable, skipped) — skipped is NOT in the denominator."""
        rows = self.of(artefact)
        skipped = sum(1 for r in rows if r["status"] == "skip")
        checkable = [r for r in rows if r["status"] != "skip"]
        return sum(1 for r in checkable if r["status"] == "ok"), len(checkable), skipped


def discover(root: Path) -> list[Path]:
    """Every repository under `root` with a `nanopubs/` directory."""
    return sorted({p.parent for p in root.glob("*/nanopubs")}
                  | {p.parent for p in root.glob("*/*/nanopubs")})


def check_chain(repo: Path, tally: Tally) -> None:
    ledger = repo / "nanopubs" / "PUBLISHED.md"
    if not ledger.is_file():
        return
    try:
        result = verify_chain(str(ledger))
    except ApiError as e:
        # "nothing published yet" is a legitimate state, not a tool failure.
        if "no published URIs" in str(e):
            tally.add("chains", repo.name, "skip", "nothing published yet")
        else:
            tally.add("chains", repo.name, "ERROR", str(e)[:70])
            tally.fail(f"verify_chain({repo.name}): {e}")
        return
    except Exception as e:  # noqa: BLE001
        tally.add("chains", repo.name, "CRASH", f"{type(e).__name__}: {e}")
        tally.fail(f"verify_chain({repo.name}) crashed: {e}\n{traceback.format_exc()}")
        return

    notes = [r["message"] for r in result["rows"] if r["status"] == "fail"]
    tally.add("chains", repo.name, "ok" if result["green"] else "finding",
              f"mode={result['mode']}, {result['counts']['pass']} pass / "
              f"{result['counts']['fail']} fail", notes)


def check_drafts(repo: Path, tally: Tally, *, live: bool) -> None:
    folder = repo / "nanopubs" / "drafts"
    if not folder.is_dir():
        return
    try:
        result = validate_drafts(str(folder), live=live)
    except ApiError as e:
        tally.add("drafts", repo.name, "skip", str(e)[:60])
        return
    except Exception as e:  # noqa: BLE001
        tally.add("drafts", repo.name, "CRASH", f"{type(e).__name__}: {e}")
        tally.fail(f"validate_drafts({repo.name}) crashed: {e}")
        return

    # A draft the checker cannot read at all is a TOOL problem, not a finding.
    unreadable = [d["step"] for d in result["drafts"]
                  if any(f["check"] == "parse" for f in d["findings"])]
    for step in unreadable:
        tally.fail(f"validate_drafts({repo.name}): could not parse {step}")

    status = "ok" if result["counts"]["error"] == 0 else "finding"
    if unreadable:
        status = "UNREADABLE"
    tally.add("drafts", repo.name, status,
              f"{result['draftsChecked']} drafts, {result['counts']['error']} err"
              + (f", unreadable: {unreadable}" if unreadable else ""))


def check_chain_draft(repo: Path, tally: Tally, *, live: bool) -> None:
    path = repo / "nanopubs" / "chain-draft.json"
    if not path.is_file():
        return
    try:
        result = validate_chain_draft(str(path), live=live)
    except Exception as e:  # noqa: BLE001
        tally.add("chain-drafts", repo.name, "CRASH", f"{type(e).__name__}: {e}")
        tally.fail(f"validate_chain_draft({repo.name}) crashed: {e}")
        return
    tally.add("chain-drafts", repo.name,
              "ok" if result["readyForWizard"] else "finding", str(result["counts"]))


def check_templates(tally: Tally, *, live: bool) -> None:
    for step in steps():
        try:
            spec = template_fields(step, live=live)
        except Exception as e:  # noqa: BLE001
            tally.add("templates", step, "CRASH", f"{type(e).__name__}: {e}")
            tally.fail(f"template_fields({step}) crashed: {e}")
            continue
        drifted = spec.get("driftedFromSnapshot")
        tally.add("templates", step, "finding" if drifted else "ok",
                  f"source={spec['source']}" + (" DRIFTED" if drifted else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("root", type=Path, help="directory holding the study repos")
    parser.add_argument("--offline", action="store_true",
                        help="use the bundled snapshot; skip network checks")
    args = parser.parse_args(argv)

    live = not args.offline
    repos = discover(args.root)
    if not repos:
        print(f"No repository with a nanopubs/ directory under {args.root}",
              file=sys.stderr)
        return 2

    tally = Tally()
    print(f"Corpus: {len(repos)} repositories under {args.root}\n")
    for repo in repos:
        check_drafts(repo, tally, live=live)
        check_chain_draft(repo, tally, live=live)
        if live:
            check_chain(repo, tally)
    check_templates(tally, live=live)

    width = max((len(r["where"]) for r in tally.rows), default=20)
    NOT_APPLICABLE = {"chains": "have nothing published",
                      "drafts": "have no recognisable drafts"}
    for artefact in ("chains", "chain-drafts", "drafts", "templates"):
        rows = tally.of(artefact)
        if not rows:
            continue
        ok, checkable, skipped = tally.counts(artefact)
        headline = f"{artefact}  —  {ok}/{checkable} clean"
        if skipped:
            headline += (f"   ({skipped} of {len(rows)} repos "
                         f"{NOT_APPLICABLE.get(artefact, 'not applicable')}, "
                         f"so not counted)")
        print(headline)
        for row in rows:
            mark = {"ok": "  ok  ", "finding": " find ", "skip": " n/a  "}.get(
                row["status"], f" {row['status'][:5]:5}")
            print(f"  {mark} {row['where']:<{width}}  {row['detail']}")
            for note in row["notes"]:
                print(f"         {'':<{width}}  - {note[:96]}")
        print()

    if tally.tool_failures:
        print("TOOL FAILURES — the tool could not read a real artefact:")
        for f in tally.tool_failures:
            print(f"  ! {f}")
        print("\nThese are bugs in this server, not findings about the artefacts.")
        return 1

    print("No tool failures: every artefact found was readable.")
    print("Findings above are statements about the artefacts, for a human to triage.")
    print(f"\nCoverage is {len(repos)} repositories AS FOUND ON DISK. A chain that "
          f"is not here is not covered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

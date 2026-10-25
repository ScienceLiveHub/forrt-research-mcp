"""validate_rocrate: the RO-Crate specification check and the FDO-readiness checks.

The crate fixtures are minimal versions of what forrt-replication-template's
scripts/build_rocrate.py writes. The validator runs offline on its bundled profiles.
"""

from __future__ import annotations

import json

import pytest

from forrt_research_mcp.rocrate import RoCrateError, fdo_checks, validate_rocrate

NP = "https://w3id.org/sciencelive/np/RA"
FIP = "https://w3id.org/np/RAQXwdUrfZ40k2MdeDv9fokzyh35UKuN3w17G2yO4Wm8A"


def crate(**root_overrides) -> dict:
    root = {"@id": "./", "@type": "Dataset", "name": "demo replication", "description": "A replication.",
            "datePublished": "2026-10-25", "license": {"@id": "https://spdx.org/licenses/MIT"},
            "identifier": "https://doi.org/10.5281/zenodo.102", "mainEntity": {"@id": "Snakefile"},
            "hasPart": [{"@id": "Snakefile"}], "conformsTo": [{"@id": FIP}],
            "subjectOf": [{"@id": f"{NP}{i}xyz"} for i in range(1, 7)]}
    root.update(root_overrides)
    root = {k: v for k, v in root.items() if v is not None}
    return {"@context": "https://w3id.org/ro/crate/1.2/context", "@graph": [
        {"@id": "ro-crate-metadata.json", "@type": "CreativeWork", "about": {"@id": "./"},
         "conformsTo": {"@id": "https://w3id.org/ro/crate/1.2"}},
        root,
        {"@id": "Snakefile", "@type": ["File", "SoftwareSourceCode", "ComputationalWorkflow"], "name": "Snakefile"},
        {"@id": "https://spdx.org/licenses/MIT", "@type": "CreativeWork", "name": "MIT License"},
        {"@id": FIP, "@type": ["CreativeWork", "Profile"], "name": "EO FIP"},
        *[{"@id": f"{NP}{i}xyz", "@type": "CreativeWork", "name": f"step {i}"} for i in range(1, 7)],
    ]}


@pytest.fixture
def write(tmp_path):
    def _write(metadata: dict):
        (tmp_path / "ro-crate-metadata.json").write_text(json.dumps(metadata))
        (tmp_path / "Snakefile").write_text("rule all:\n")
        return tmp_path
    return _write


def test_a_complete_crate_passes_and_is_fdo_ready(write):
    result = validate_rocrate(str(write(crate())))
    assert result["passed"], result["issues"]
    assert result["fdo_ready"]
    assert "all checks pass" in result["summary"]


def test_the_metadata_file_path_is_accepted_too(write):
    root = write(crate())
    assert validate_rocrate(str(root / "ro-crate-metadata.json"))["passed"]


def test_a_missing_required_property_is_reported_with_its_check_and_entity(write):
    result = validate_rocrate(str(write(crate(name=None))))
    assert not result["passed"]
    issue = next(i for i in result["issues"] if "name" in i["name"])
    assert issue["severity"] == "REQUIRED" and issue["entity"] == "./"


def test_fdo_gaps_are_advisory_and_say_how_to_fix(write):
    result = validate_rocrate(str(write(crate(conformsTo=None, subjectOf=None))))
    assert result["passed"]  # still a valid RO-Crate
    assert not result["fdo_ready"]
    failing = {c["check"]: c["detail"] for c in result["fdo_checks"] if not c["ok"]}
    assert set(failing) == {"community_fip", "forrt_chain"}
    assert "conformsTo" in failing["community_fip"]


def test_listing_the_metadata_file_as_a_part_is_flagged_for_rohub():
    metadata = crate(hasPart=[{"@id": "Snakefile"}, {"@id": "ro-crate-metadata.json"}])
    rohub = next(c for c in fdo_checks(metadata) if c["check"] == "rohub_safe")
    assert not rohub["ok"] and "ROHub" in rohub["detail"]


def test_a_workflow_that_is_not_typed_as_one_is_flagged():
    metadata = crate()
    metadata["@graph"][2]["@type"] = "File"
    main = next(c for c in fdo_checks(metadata) if c["check"] == "main_workflow")
    assert not main["ok"]


@pytest.mark.parametrize("path,level,message", [
    ("/nope", "REQUIRED", "no ro-crate-metadata.json"),
    (None, "MUST", "level must be one of"),
])
def test_bad_input_raises_a_clear_error(write, path, level, message):
    with pytest.raises(RoCrateError, match=message):
        validate_rocrate(path or str(write(crate())), level=level)

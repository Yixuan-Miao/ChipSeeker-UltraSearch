#!/usr/bin/env python3
"""Replay the public circuit-review acceptance sample offline with stdlib only."""

import argparse
import json
import tempfile
from pathlib import Path

from research_output import DATA_DIR, HTML_NAME, MD_NAME, init_workspace, render_workspace
from work_versions import discover_reference_gaps, expand_verified_versions


def verify(parent, fixture):
    payload = json.loads(Path(fixture).read_text(encoding="utf-8"))
    workspace = init_workspace(parent, "低温放大器验收样例")
    (workspace / DATA_DIR / "papers.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result = render_workspace(workspace)
    model = json.loads((workspace / DATA_DIR / "report-data.json").read_text(encoding="utf-8"))
    expected = payload["acceptance"]
    assert len(model["papers"]) == expected["expected_works"]
    publications = [p for work in model["papers"] for p in [work, *work.get("versions", [])]]
    assert len(publications) == expected["expected_publications"]
    assert {p["doi"] for p in publications} == set(expected["positive_dois"])
    audit = {p["doi"]: p for p in model["screening_audit"]}
    for case in expected["negative_cases"]:
        assert audit[case["doi"]]["decision"] == case["decision"]
    for filename in (MD_NAME, HTML_NAME):
        report = (workspace / filename).read_text(encoding="utf-8")
        for p in publications:
            assert p["doi"] in report
    ris = (workspace / DATA_DIR / "publications.ris").read_text(encoding="utf-8")
    assert ris.count("ER  -") == len(publications)
    for p in publications:
        assert ris.count("DO  - " + p["doi"] + "\n") == 1
    catalog = payload["replay_catalog"]
    by_doi = {p["doi"]: p for p in catalog["papers"]}
    directions = 0
    for relation in catalog["version_relations"]:
        if relation["status"] != "confirmed":
            continue
        for endpoint in ("from_doi", "to_doi"):
            found = expand_verified_versions([by_doi[relation[endpoint]]], catalog["papers"], catalog["version_relations"])
            assert {p["doi"] for p in found["papers"]} == {relation["from_doi"], relation["to_doi"]}
            directions += 1
    seeds = [by_doi[doi] for source in catalog["gap_sources"] for doi in source["seed_publication_dois"]]
    gaps = discover_reference_gaps(seeds, catalog["gap_sources"], catalog["papers"])
    assert gaps["papers"] and all(p.get("gap_discovery_evidence") for p in gaps["papers"])
    return {**result, "formal_publications": len(publications), "counterexamples": len(expected["negative_cases"]),
            "version_directions_verified": directions, "reference_gaps_recovered": len(gaps["papers"]), "network_requests": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", help="Retain the paired sample reports under this folder.")
    parser.add_argument("--fixture", default=str(Path(__file__).resolve().parents[1] / "examples/circuit-review.json"))
    args = parser.parse_args()
    if args.parent:
        result = verify(args.parent, args.fixture)
    else:
        with tempfile.TemporaryDirectory(prefix="chipseeker-review-") as parent:
            result = verify(parent, args.fixture)
        result.pop("workspace", None)
        result.pop("markdown", None)
        result.pop("html", None)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

"""Replay real publication evidence without dependence on live search rankings."""

import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/chipseeker-ultra-search/scripts/work_versions.py"
SPEC = importlib.util.spec_from_file_location("work_versions_test", SCRIPT)
versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(versions)


@pytest.fixture
def evidence():
    return json.loads((ROOT / "tests/fixtures/publication_evidence.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("pair", [0, 1])
@pytest.mark.parametrize("direction", ["from_doi", "to_doi"])
def test_single_conference_or_journal_seed_recovers_other_version(evidence, pair, direction):
    relation = evidence["version_relations"][pair]
    seed = next(paper for paper in evidence["papers"] if paper["doi"] == relation[direction])
    result = versions.expand_verified_versions([seed], evidence["papers"], evidence["version_relations"])
    found = result["papers"]
    assert {paper["doi"] for paper in found} == {relation["from_doi"], relation["to_doi"]}
    assert len({paper["work_id"] for paper in found}) == 1
    assert all(paper["work_id_confirmed"] for paper in found)
    grouped = versions.group_work_versions(found, result["relations"])
    assert len(grouped["papers"]) == 1
    work = grouped["papers"][0]
    assert work["publication_status"] == "journal-article"
    assert len(work["versions"]) == 1
    assert work["versions"][0]["publication_status"] == "proceedings-article"
    assert work["doi"] != work["versions"][0]["doi"]
    assert work["measurement_summary"] != work["versions"][0]["measurement_summary"]
    assert work["venue"] != work["versions"][0]["venue"]


def test_tian_similar_journal_designs_stay_distinct(evidence):
    papers = [paper for paper in evidence["papers"] if "Tian" in " ".join(paper["authors"])]
    for paper in papers:
        paper["query_family"] = "same-query-is-not-a-work-family"
    grouped = versions.group_work_versions(papers, evidence["version_relations"])
    assert len(grouped["papers"]) == 2
    assert all(not paper["versions"] for paper in grouped["papers"])
    assert len({paper["doi"] for paper in grouped["papers"]}) == 2
    assert all(not paper["work_id_confirmed"] for paper in grouped["papers"])


@pytest.mark.parametrize("kind", ["citation", "shared_authors", "similar_title", "chip_photo_similarity"])
def test_similarity_or_citation_never_confirms_same_work(evidence, kind):
    relation = deepcopy(evidence["version_relations"][0])
    relation["evidence"][0]["kind"] = kind
    assert versions.confirmed_relations([relation]) == []
    assert len(versions.group_work_versions(evidence["papers"][:2], [relation])["papers"]) == 2


def test_missing_source_location_and_unreadable_endpoints_remain_auditable(evidence):
    relation = deepcopy(evidence["version_relations"][0])
    relation["evidence"][0]["location"] = ""
    assert versions.group_work_versions(evidence["papers"][:2], [relation])["relation_audit"][0]["reason"] == "explicit_version_evidence_required"
    papers = deepcopy(evidence["papers"][:2])
    papers[1]["publication_status"] = "preprint"
    assert len(versions.group_work_versions(papers, evidence["version_relations"])["papers"]) == 2


def test_publication_record_must_have_formal_provenance(evidence):
    relation = evidence["version_relations"][0]
    papers = deepcopy(evidence["papers"][:2])
    papers[1]["publication_evidence"] = []
    result = versions.expand_verified_versions([papers[0]], papers, [relation])
    assert len(result["papers"]) == 1
    assert result["unresolved"][0]["reason"] == "formal_publication_evidence_required"


def test_reference_path_finds_tian_without_giving_target_doi(evidence):
    seed = evidence["papers"][2]  # Already collected Zeng journal LNA.
    assert "Tian" not in " ".join(seed["authors"])
    assert "doi" not in evidence["gap_sources"][0]["references"][0]
    result = versions.discover_reference_gaps([seed], evidence["gap_sources"], evidence["papers"])
    assert result["inspected_references"] == 1
    assert len(result["papers"]) == 1
    assert result["papers"][0]["doi"] == "10.1109/tasc.2024.3514608"
    lead = result["leads"][0]
    assert lead["status"] == "formal_record_found"
    assert "Table 3.1" in lead["evidence"]["location"] and "[63]" in lead["evidence"]["location"]
    assert "https://research.chalmers.se/" in lead["evidence"]["url"]


def test_reference_miss_and_budget_are_not_zero_relevant_papers(evidence):
    seed = evidence["papers"][2]
    result = versions.discover_reference_gaps([seed], evidence["gap_sources"], [])
    assert result["papers"] == []
    assert result["leads"][0]["status"] == "title_lookup_required"
    limited = versions.discover_reference_gaps([seed], evidence["gap_sources"], [], max_references=0)
    assert limited["leads"][0]["status"] == "budget_deferred"
    assert limited["inspected_references"] == 0


def test_link_budget_is_bounded_and_does_not_mutate_evidence(evidence):
    before = deepcopy(evidence)
    seed = evidence["papers"][0]
    result = versions.expand_verified_versions([seed], evidence["papers"], evidence["version_relations"], max_publications=1)
    assert len(result["papers"]) == 1
    assert len(result["budget_limited_relations"]) == 1
    assert evidence == before


def test_saved_exclusion_can_resolve_work_when_old_seed_is_absent(evidence):
    relation = evidence["version_relations"][0]
    first, second = evidence["papers"][:2]
    initial = versions.assign_work_ids([first], [relation])[0]
    refreshed = versions.assign_work_ids([second], [relation])[0]
    assert initial["work_id"] == refreshed["work_id"]
    assert initial["work_id_confirmed"] is refreshed["work_id_confirmed"] is True


def test_metadata_uses_issue_year_and_preserves_online_year(evidence):
    heinz = evidence["papers"][0]
    assert heinz["year"] == 2024 and heinz["online_year"] == 2023
    tian = evidence["papers"][4]
    assert tian["year"] == 2025 and tian["volume"] == "35" and tian["issue"] == "2"


def test_doi_url_typography_and_case_do_not_change_publication_identity():
    assert versions.normalize_doi("https://doi.org/10.1109%2FTMTT.2023.3312471") == "10.1109/tmtt.2023.3312471"


def test_unconfirmed_relation_or_same_doi_is_not_a_version_group(evidence):
    relation = deepcopy(evidence["version_relations"][0])
    relation["status"] = "pending"
    assert not versions.confirmed_relations([relation])
    relation["status"] = "confirmed"
    relation["to_doi"] = relation["from_doi"]
    assert not versions.confirmed_relations([relation])


def test_same_doi_prefers_formal_record_and_keeps_preprint_source(evidence):
    journal = {**evidence["papers"][0], "source_in_current_corpus": False, "origin": "external_primary_source"}
    preprint = {**deepcopy(journal), "venue": "arXiv", "venue_name": "arXiv", "year": 2023,
                "publication_status": "preprint", "publication_evidence": [], "source_in_current_corpus": True}
    before = deepcopy([preprint, journal])
    result = versions.group_work_versions([preprint, journal], [])
    assert len(result["papers"]) == 1
    record = result["papers"][0]
    assert record["venue"] == journal["venue"] and record["year"] == 2024
    assert len(record["publication_sources"]) == 2
    assert record["versions"] == []
    assert record["source_in_current_corpus"] is False
    assert record["origin"] == "external_primary_source"
    assert any(source["venue"] == "arXiv" and source["source_in_current_corpus"] for source in record["publication_sources"])
    assert [preprint, journal] == before


def test_unidentified_same_titles_do_not_create_a_confirmed_group():
    records = [{"title": "A Cryogenic LNA", "year": 2025, "venue": venue} for venue in ["Conference", "Journal"]]
    result = versions.group_work_versions(records, [])
    assert len(result["papers"]) == 2
    assert all(not paper["versions"] for paper in result["papers"])

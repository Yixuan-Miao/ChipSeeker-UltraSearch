"""Integration checks at the saved-data to MD/HTML/JSON/RIS report boundary."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills/chipseeker-ultra-search/scripts"
SPEC = importlib.util.spec_from_file_location("review_report_output", SCRIPTS / "research_output.py")
output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(output)
SPEC = importlib.util.spec_from_file_location("review_report_policy", SCRIPTS / "review_policy.py")
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)
EVIDENCE = json.loads((ROOT / "tests/fixtures/publication_evidence.json").read_text(encoding="utf-8"))


def reviewed_heinz_pair():
    """Use the independently verified pair and preserve per-publication evidence."""
    papers = deepcopy(EVIDENCE["papers"][:2])
    relation = deepcopy(EVIDENCE["version_relations"][0])
    for paper in papers:
        paper["review_evidence"] = {
            "contribution": "target_circuit", "relevance": "strong", "measured_target": True,
            "publisher": "IEEE", "publication_type": paper["publication_status"],
            "publication_evidence": deepcopy(paper["publication_evidence"]),
            "evidence": [{"url": paper["measurement_summary"]["source_url"],
                          "location": paper["measurement_summary"]["location"],
                          "note": "The primary measured contribution is the low-noise amplifier design."}],
        }
    return {"schema": "ultrasearch-screened-papers/v2", "generated_at": "2026-11-17",
            "review_policy": {"profile": "circuit_review"}, "papers": papers, "version_relations": [relation]}


def test_pending_queries_never_render_as_completed(tmp_path):
    payload = {"papers": [], "summary": {"status": "running", "pending_queries": 2, "failed_queries": 1,
                                          "unresolved_query_ids": ["a", "b", "c"]}}
    result = output.normalize_report(payload, tmp_path)
    assert result["partial"] and result["failures"] == 3
    payload["summary"]["status"] = "completed"
    assert output.normalize_report(payload, tmp_path)["partial"]


def test_report_prefers_formal_source_even_if_preprint_arrives_last(tmp_path):
    journal = reviewed_heinz_pair()["papers"][0]
    journal.update(source_in_current_corpus=False, origin="external_primary_source")
    preprint = {"doi": journal["doi"], "title": "Manuscript", "venue": "arXiv", "publication_status": "preprint",
                "source_in_current_corpus": True, "pdf_link": "https://arxiv.org/pdf/2401.00001"}
    for records in ([journal, preprint], [preprint, journal]):
        result = output.normalize_report({"schema": "ultrasearch-screened-papers/v2", "papers": records}, tmp_path)
        row, = result["papers"]
        assert row["title"] == journal["title"]
        assert row["source_in_current_corpus"] is False
        assert row.get("pdf_link") == journal.get("pdf_link")
        assert any(source.get("pdf_link") == preprint["pdf_link"] for source in row["publication_sources"])


def test_legacy_manual_tombstone_survives_hydration_and_verified_new_version(tmp_path):
    payload = reviewed_heinz_pair()
    payload.pop("review_policy")
    payload["schema"] = "ultrasearch-screened-papers/v1"
    no_doi = deepcopy(payload["papers"][0])
    no_doi.pop("doi")
    payload = policy.record_manual_exclusion(payload, no_doi, "用户已删除这一项工作")
    result = output.normalize_report(payload, tmp_path)
    assert result["papers"] == []
    assert {entry["decision"] for entry in result["audit"]} == {"manual_exclusion"}
    assert output.render_ris(result) == ""
    assert all("用户已删除" in entry["reason_zh"] for entry in result["audit"])


def test_old_confirmed_conference_stays_an_annotated_link_with_journal_primary_in_all_formats(tmp_path):
    payload = reviewed_heinz_pair()
    payload["review_policy"] = {"years": [2024, 2026], "max_results": 1, "attach_out_of_scope_versions": True}
    workspace = output.init_workspace(tmp_path, "版本回归")
    (workspace / output.DATA_DIR / "papers.json").write_text(json.dumps(payload), encoding="utf-8")
    result = output.render_workspace(workspace)
    structured = json.loads((workspace / output.DATA_DIR / "report-data.json").read_text(encoding="utf-8"))
    assert result["retained_papers"] == len(structured["papers"]) == 1
    work = structured["papers"][0]
    assert work["doi"] == payload["papers"][0]["doi"]
    assert work["publication_status"] == "journal-article"
    conference, = work["versions"]
    assert conference["doi"] == payload["papers"][1]["doi"]
    assert "超出时间窗" in conference["scope_note_zh"]
    assert work["measurement_summary"] == payload["papers"][0]["measurement_summary"]
    assert conference["measurement_summary"] == payload["papers"][1]["measurement_summary"]
    for name in (output.MD_NAME, output.HTML_NAME):
        document = (workspace / name).read_text(encoding="utf-8")
        assert "超出时间窗" in document
        assert payload["papers"][0]["doi"] in document
        assert payload["papers"][1]["doi"] in document
        assert payload["version_relations"][0]["evidence"][0]["location"] in document
    ris = (workspace / output.DATA_DIR / "publications.ris").read_text(encoding="utf-8")
    assert ris.count("TY  - ") == ris.count("ER  -") == 2
    assert "TY  - JOUR" in ris and "TY  - CONF" in ris
    for paper in payload["papers"]:
        assert ris.count("DO  - " + paper["doi"]) == 1


def test_explicit_window_also_limits_exported_versions_by_default(tmp_path):
    payload = reviewed_heinz_pair()
    payload["review_policy"]["years"] = [2024, 2026]
    result = output.normalize_report(payload, tmp_path)
    assert len(result["papers"]) == 1 and not result["papers"][0]["versions"]
    assert output.render_ris(result).count("TY  - ") == 1


@pytest.mark.parametrize("invalid_field", ["publication_status", "publication_evidence"])
def test_complete_nested_publication_evidence_has_consistent_work_limit_and_grouping(tmp_path, invalid_field):
    payload = reviewed_heinz_pair()
    payload["review_policy"] = {"max_results": 1}
    # The complete nested record is equivalent to the top-level evidence.
    payload["papers"][1].pop(invalid_field)
    result = output.normalize_report(payload, tmp_path)
    assert len(result["papers"]) == 1
    assert len(result["papers"][0]["versions"]) == 1
    assert result["relation_audit"][0]["reason"] == "confirmed"
    assert output.render_ris(result).count("TY  - ") == 2


def test_non_publication_evidence_cannot_confirm_a_work_or_bypass_the_display_limit(tmp_path):
    payload = reviewed_heinz_pair()
    payload["review_policy"] = {"profile": "broad_discovery", "max_results": 1}
    conference = payload["papers"][1]
    conference["publication_evidence"][0]["kind"] = "source_abstract"
    conference["review_evidence"]["publication_evidence"][0]["kind"] = "source_abstract"
    result = output.normalize_report(payload, tmp_path)
    assert len(result["papers"]) == 1
    assert result["papers"][0]["versions"] == []
    assert result["relation_audit"][0]["reason"] == "formal_publication_evidence_required"
    assert any(entry["decision"] == "output_limit" for entry in result["audit"])


def test_explicit_preprint_status_is_not_overridden_by_stale_nested_formal_claim(tmp_path):
    payload = reviewed_heinz_pair()
    payload["review_policy"] = {"profile": "broad_discovery", "max_results": 1}
    payload["papers"][1]["publication_status"] = "preprint"
    result = output.normalize_report(payload, tmp_path)
    assert len(result["papers"]) == 1
    assert result["papers"][0]["versions"] == []
    assert result["relation_audit"][0]["reason"] == "formal_publication_evidence_required"


def test_actual_cicc_record_is_displayed_and_exported_with_its_publisher_venue(tmp_path):
    # Public Crossref metadata independently checked on 2026-11-17. The broad
    # profile is explicit because this ADC is outside the amplifier main table.
    paper = {
        "doi": "10.1109/cicc63670.2025.10983297",
        "title": "A 4.6-373K Functional 800MS/s 12b Buffer-then-Amplify Charge-Pump-Based Pipelined TI-SAR ADC with Integrated-Active-Hold Technique",
        "year": 2025,
        "venue": "2025 IEEE Custom Integrated Circuits Conference (CICC)",
        "publication_status": "proceedings-article",
        "primary_source": "https://api.crossref.org/works/10.1109/cicc63670.2025.10983297",
    }
    result = output.normalize_report({"generated_at": "2026-11-17",
                                      "review_policy": {"profile": "broad_discovery"},
                                      "papers": [paper]}, tmp_path)
    assert result["papers"][0]["_venue"] == paper["venue"]
    for document in (output.render_markdown(result), output.render_html(result), output.render_ris(result)):
        assert "Custom Integrated Circuits Conference (CICC)" in document
        assert "ISSCC" not in document
        assert paper["doi"] in document

"""Offline acceptance cases with fixed records and explicit evidence locations."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills/chipseeker-ultra-search/scripts/review_policy.py"
SPEC = importlib.util.spec_from_file_location("review_policy_test", SCRIPT)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
FIXTURE = json.loads((ROOT / "tests/fixtures/review_policy_cases.json").read_text(encoding="utf-8"))


def report(papers, **options):
    return {"generated_at": FIXTURE["generated_at"],
            "review_policy": {"profile": "circuit_review", **options}, "papers": deepcopy(papers)}


def core_paper():
    # A real measured LNA remains eligible when the user explicitly permits SPIE.
    return deepcopy(next(case["paper"] for case in FIXTURE["cases"] if case["case_id"] == "mathewson-spie-2024"))


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["case_id"])
def test_fixed_real_counterexamples_have_visible_exclusion_reasons(case):
    result = review.apply_review_policy(report([case["paper"]]))
    assert not result["papers"]
    audit = result["screening_audit"][0]
    assert audit["decision"] == case["expected_decision"]
    assert audit["reason_zh"] and audit["evidence"] and audit["publication_evidence"]
    assert audit["source_origin"] == "external_primary_source"
    assert audit["readability"] == case["paper"]["review_evidence"]["readability"]


def test_prestige_and_measured_supporting_lna_do_not_override_main_contribution():
    cases = [case["paper"] for case in FIXTURE["cases"] if case["case_id"] in {"wang-nature-interconnect", "ibberson-icecs-2024"}]
    for paper in cases:
        paper = deepcopy(paper)
        paper["similarity"] = 1.0
        paper["venue_tier"] = "S+"
        result = review.apply_review_policy(report([paper], preferred_publishers=["IEEE", "Springer Nature"]))
        assert not result["papers"]
        assert result["screening_audit"][0]["decision"] == "system_contribution"


def test_non_ieee_exception_requires_relevance_main_circuit_measurement_and_reason():
    paper = core_paper()
    paper["review_evidence"]["exception_reason"] = "User-approved measured LNA relevant to this circuit review."
    result = review.apply_review_policy(report([paper]))
    assert len(result["papers"]) == 1
    assert result["screening_audit"][0]["decision"] == "core"
    paper["review_evidence"]["measured_target"] = False
    assert not review.apply_review_policy(report([paper]))["papers"]
    paper["review_evidence"].update(measured_target=True, contribution="system")
    assert not review.apply_review_policy(report([paper]))["papers"]


def test_title_and_formal_doi_on_preprint_never_supply_missing_provenance():
    paper = core_paper()
    paper["review_evidence"] = {"publication_type": "preprint", "publisher": "IEEE"}
    paper["venue_name"] = "arXiv"
    paper["title"] = "A measured cryogenic low-noise amplifier"
    result = review.apply_review_policy(report([paper]))
    decision = result["screening_audit"][0]
    assert "source_priority" in decision["reasons"]
    assert "insufficient_evidence" in decision["reasons"]
    assert not result["papers"]


def test_unread_source_and_missing_contribution_are_pending_not_guessed_from_title():
    paper = core_paper()
    paper["review_evidence"].pop("contribution")
    paper["review_evidence"]["readability"] = "metadata_only"
    result = review.apply_review_policy(report([paper], preferred_publishers=["SPIE"]))
    assert not result["papers"]
    assert result["screening_audit"][0]["decision"] == "insufficient_evidence"
    assert result["screening_audit"][0]["readability"] == "metadata_only"


def test_ten_year_window_uses_saved_search_date_and_honors_explicit_years():
    policy = review.resolve_policy({"profile": "circuit_review"}, "2026-11-17T08:00:00Z")
    assert policy["start_date"] == "2016-11-17"
    assert policy["end_date"] == "2026-11-17"
    assert review.resolve_policy(policy, "2027-03-01")["start_date"] == "2016-11-17"
    assert review.resolve_policy({"profile": "circuit_review"}, "2024-02-29")["start_date"] == "2014-02-28"
    old = deepcopy(next(case["paper"] for case in FIXTURE["cases"] if case["case_id"] == "kleine-1994"))
    result = review.apply_review_policy(report([old], years=[1990, 2000]))
    assert result["papers"][0]["doi"] == old["doi"]
    assert result["review_policy"]["start_date"] == "1990-01-01"


def test_default_discovery_keeps_old_and_non_ieee_records_without_implicit_filters():
    rows = [deepcopy(next(case["paper"] for case in FIXTURE["cases"] if case["case_id"] == "kleine-1994")),
            core_paper()]
    result = review.apply_review_policy({"generated_at": FIXTURE["generated_at"], "papers": rows})
    assert result["review_policy"]["profile"] == "broad_discovery"
    assert len(result["papers"]) == 2
    assert review.resolve_policy(result["review_policy"])["start_date"] is None


@pytest.mark.parametrize("profile", ["broad_discovery", "circuit_review"])
def test_explicit_dates_and_sources_apply_to_both_profiles(profile):
    paper = core_paper()
    paper["review_evidence"]["exception_reason"] = "Relevant, measured non-IEEE source."
    result = review.apply_review_policy(report([paper], profile=profile, years=[2025, 2026],
                                               allowed_publishers=["IEEE"]))
    assert not result["papers"]
    assert {"outside_window", "source_priority"} <= set(result["screening_audit"][0]["reasons"])
    result = review.apply_review_policy(report([paper], profile=profile, years=[2024, 2026],
                                               allowed_publishers=["SPIE"]))
    assert len(result["papers"]) == 1


@pytest.mark.parametrize("published,expected", [("2016-11-16", "outside_window"), ("2016-11-17", "core"), ("2026-11-18", "outside_window")])
def test_exact_publication_boundary_and_year_only_precision(published, expected):
    paper = core_paper()
    paper["publication_date"] = published
    result = review.apply_review_policy(report([paper], preferred_publishers=["SPIE"]))
    assert result["screening_audit"][0]["decision"] == expected
    paper.pop("publication_date")
    paper["year"] = "2016"
    result = review.apply_review_policy(report([paper], preferred_publishers=["SPIE"]))
    assert result["papers"] and result["screening_audit"][0]["date_precision"] == "year"


def test_manual_exclusion_survives_hydration_new_version_and_repeated_refresh():
    no_doi = core_paper()
    no_doi.pop("doi")
    saved = review.record_manual_exclusion(report([]), no_doi, "用户已审阅并删除")
    conference = core_paper()
    conference.update(work_id="verified-work", work_id_confirmed=True)
    journal = deepcopy(conference)
    journal.update(doi="10.9999/test-long-version", title="A longer account of the same measured amplifier")
    saved["papers"] = [conference, journal]
    saved["version_relations"] = [relation(conference["doi"], journal["doi"])]
    result = review.apply_review_policy(saved)
    assert not result["papers"]
    assert len(result["manual_exclusions"]) == 1
    assert result["manual_exclusions"][0]["work_id"].startswith("work:")
    result["papers"] = [journal]
    result = review.apply_review_policy(result)
    assert not result["papers"]
    assert all(row["decision"] == "manual_exclusion" for row in result["screening_audit"])


def test_manual_flag_and_existing_audit_persist_without_duplicating_tombstones():
    paper = core_paper()
    paper["manual_excluded"] = True
    result = review.apply_review_policy(report([paper]))
    result["papers"] = [deepcopy(paper)]
    result = review.apply_review_policy(result)
    assert len(result["manual_exclusions"]) == len(result["screening_audit"]) == 1
    assert result["screening_audit"][0]["decision"] == "manual_exclusion"
    result["papers"] = []
    assert review.apply_review_policy(result)["screening_audit"] == result["screening_audit"]


def relation(left, right, **changes):
    row = {"from_doi": left, "to_doi": right, "relation_type": "journal_extension", "status": "confirmed",
           "evidence": [{"kind": "publisher_version_statement", "url": "https://example.org/publisher-paper",
                         "location": "First-page publication footnote", "note": "Journal explicitly identifies the earlier conference paper."}]}
    row.update(changes)
    return row


def test_later_verified_relation_excludes_new_version_without_old_seed_present():
    old = core_paper()
    new = deepcopy(old)
    new.update(doi="10.9999/later-journal", title="Long version with a changed title")
    saved = review.record_manual_exclusion(report([]), old)
    saved["papers"] = [new]
    saved["version_relations"] = [relation(old["doi"], new["doi"])]
    result = review.apply_review_policy(saved)
    assert not result["papers"]
    assert "doi:10.9999/later-journal" in result["manual_exclusions"][0]["publication_ids"]


@pytest.mark.parametrize("changes", [{"status": "pending"}, {"relation_type": "related"}, {"evidence": []}])
def test_similar_or_unsupported_relationship_does_not_spread_a_manual_exclusion(changes):
    old = core_paper()
    new = deepcopy(old)
    new.update(doi="10.9999/independent-amplifier")
    saved = review.record_manual_exclusion(report([], preferred_publishers=["SPIE"]), old)
    saved["papers"] = [new]
    saved["version_relations"] = [relation(old["doi"], new["doi"], **changes)]
    result = review.apply_review_policy(saved)
    assert len(result["papers"]) == 1
    assert result["papers"][0]["doi"] == new["doi"]


def test_output_limit_counts_verified_works_and_keeps_both_publications():
    conference = core_paper()
    conference.update(work_id="verified-work", work_id_confirmed=True)
    journal = deepcopy(conference)
    journal.update(doi="10.9999/long-version", title="Long version")
    independent = deepcopy(conference)
    independent.update(doi="10.9999/another-work", work_id="unconfirmed", work_id_confirmed=False)
    payload = report([journal, conference, independent], preferred_publishers=["SPIE"], max_results=1)
    payload["version_relations"] = [relation(conference["doi"], journal["doi"])]
    result = review.apply_review_policy(payload)
    assert [paper["doi"] for paper in result["papers"]] == [journal["doi"], conference["doi"]]
    assert result["screening_summary"]["displayed_works"] == 1
    assert result["screening_audit"][-1]["decision"] == "output_limit"
    assert result["screening_audit"][-1]["eligibility"] == "core"


@pytest.mark.parametrize("kind", [None, "preprint", "agent_assertion", "author_citation"])
def test_a_source_url_and_prose_do_not_prove_formal_publication(kind):
    paper = core_paper()
    for row in paper["review_evidence"]["publication_evidence"]:
        row["kind"] = kind
    result = review.apply_review_policy(report([paper], preferred_publishers=["SPIE"]))
    assert not result["papers"]
    assert result["screening_audit"][0]["decision"] == "insufficient_evidence"
    assert not result["screening_audit"][0]["publication_evidence"]


def test_caller_supplied_confirmed_work_id_cannot_bypass_result_cap_or_spread_exclusions():
    left = core_paper()
    left.update(work_id="injected-family", work_id_confirmed=True)
    right = deepcopy(left)
    right.update(doi="10.9999/unrelated", title="Independent amplifier")
    result = review.apply_review_policy(report([left, right], preferred_publishers=["SPIE"], max_results=1))
    assert len(result["papers"]) == 1
    assert result["screening_audit"][-1]["decision"] == "output_limit"
    saved = review.record_manual_exclusion(report([left, right], preferred_publishers=["SPIE"]), left)
    result = review.apply_review_policy(saved)
    assert [p["doi"] for p in result["papers"]] == [right["doi"]]


def test_explicit_preprint_status_is_not_overridden_by_stale_nested_formal_claim():
    preprint = core_paper()
    preprint["publication_status"] = "preprint"
    formal = deepcopy(preprint)
    formal.update(doi="10.9999/formal-journal", title="Later formal account", publication_status="journal-article")
    payload = report([preprint, formal], preferred_publishers=["SPIE"], max_results=1)
    payload["version_relations"] = [relation(preprint["doi"], formal["doi"])]
    result = review.apply_review_policy(payload)
    assert [p["doi"] for p in result["papers"]] == [formal["doi"]]
    assert result["screening_audit"][0]["decision"] == "source_priority"
    assert not any(row["work_id"] for row in result["screening_audit"])
    assert not result["papers"][0]["work_id_confirmed"]


def test_explicit_top_level_nonformal_provenance_is_not_filled_from_stale_review_evidence():
    paper = core_paper()
    paper["publication_evidence"] = [{"kind": "preprint", "url": "https://example.org/preprint",
                                     "location": "Repository record", "text": "Unaccepted author manuscript."}]
    result = review.apply_review_policy(report([paper], preferred_publishers=["SPIE"]))
    assert not result["papers"]
    assert result["screening_audit"][0]["decision"] == "insufficient_evidence"


def test_explicit_broad_discovery_keeps_older_sources_but_honors_manual_deletion():
    rows = [case["paper"] for case in FIXTURE["cases"]]
    saved = review.record_manual_exclusion(report(rows, profile="broad_discovery"), rows[0])
    result = review.apply_review_policy(saved)
    assert len(result["papers"]) == len(rows) - 1


def test_gap_plan_partitions_full_queries_and_follows_sparse_source_and_reference_leads():
    queries = [{"query_id": "full", "status": "completed", "result_count": 200, "top_k": 200, "years": [2016, 2026]},
               {"query_id": "not-full", "status": "completed", "result_count": 104, "top_k": 200},
               {"query_id": "failed", "status": "failed", "client_request_id": "persisted-id"},
               {"query_id": "missing-doi", "status": "completed", "result_count": 0, "identity_lookup": True},
               {"query_id": "empty-angle", "status": "completed", "result_count": 0},
               {"query_id": "unavailable", "status": "source_unavailable"}]
    source = {"source_id": "specialist-journal", "strong_topic_match": True, "formal_records_seen": False,
              "preprint_with_formal_doi_seen": True}
    plan = review.plan_gap_checks([{"branch_id": "hts-input-matching", "candidate_count": 1}], queries, [source],
                                 reference_leads=[{"lead_id": "thesis-table", "terms": ["cryogenic", "HTS input matching", "LNA"]}])
    checks = {row["cache_key"]: row for row in plan["checks"]}
    assert checks["split:full"]["partition_by"] == "years"
    assert "split:not-full" not in checks
    assert checks["resume:failed"]["client_request_id"] == "persisted-id"
    assert not checks["formal-source:specialist-journal"]["preprint_doi_is_coverage_evidence"]
    assert checks["branch:hts-input-matching"]["reason"] == "sparse_branch"
    assert checks["lead:thesis-table"]["max_hops"] == 1
    states = {row["query_id"]: row["state"] for row in plan["observations"]}
    assert states["not-full"] == "completed_visible_results"
    assert states["missing-doi"] == "index_no_records"
    assert states["empty-angle"] == "completed_zero_results"
    assert states["unavailable"] == "source_unavailable"
    assert all(row["coverage_scope"] == "visible_index_only" for row in plan["observations"])


def test_gap_receipts_reuse_success_without_caching_a_failure_as_absence():
    sources = [{"source_id": name, "strong_topic_match": True, "formal_records_seen": False}
               for name in ["found", "missing", "unavailable", "failed"]]
    plan = review.plan_gap_checks(source_checks=sources, remaining_budget=3)
    assert len(plan["checks"]) == 3 and len(plan["deferred_checks"]) == 1
    saved = {}
    outcomes = ["completed_found", "index_no_records", "source_unavailable"]
    for check, outcome in zip(plan["checks"], outcomes):
        saved = review.record_gap_check(saved, check, outcome)
    resumed = review.plan_gap_checks(source_checks=sources, completed_checks=saved["gap_checks"])
    assert [row["cache_key"] for row in resumed["checks"]] == ["formal-source:missing", "formal-source:unavailable", "formal-source:failed"]
    assert saved["gap_checks"]["formal-source:missing:index"]["channel"] == "index"
    assert saved["gap_checks"]["formal-source:unavailable"]["status"] == "source_unavailable"
    assert not review.plan_gap_checks(source_checks=sources, allow_external=False)["checks"]


def test_report_pipeline_exposes_targeted_gap_and_reuses_its_saved_receipt():
    payload = report([], max_gap_checks=1)
    payload["searches"] = [{"query_id": "title-cold", "result_count": 104, "top_k": 200, "status": "completed"}]
    payload["source_checks"] = [{"source_id": "TASC", "strong_topic_match": True,
                                 "formal_records_seen": False, "preprint_with_formal_doi_seen": True}]
    result = review.apply_review_policy(payload)
    assert len(result["gap_check_plan"]["checks"]) == 1
    check = result["gap_check_plan"]["checks"][0]
    assert check["action"] == "check_formal_source"
    result = review.record_gap_check(result, check, "completed_found", query_ids=["identity-batch"],
                                    evidence=[{"url": "https://example.org/issue", "location": "Issue table of contents",
                                               "text": "A formal relevant publication was found."}])
    refreshed = review.apply_review_policy(result)
    assert not refreshed["gap_check_plan"]["checks"]
    assert refreshed["gap_checks"][check["cache_key"]]["query_ids"] == ["identity-batch"]


@pytest.mark.parametrize("options", [{"max_results": 0}, {"max_results": True}, {"profile": "unknown"}, {"years": [2026, 2016]}])
def test_invalid_policy_does_not_silently_relax_eligibility(options):
    with pytest.raises(ValueError):
        review.resolve_policy(options, "2026-11-17")

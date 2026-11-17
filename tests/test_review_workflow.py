import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/chipseeker-ultra-search/scripts"
sys.path.insert(0, str(SCRIPTS))
import review_policy as policy
import research_output as output
import chipseeker_remote as remote


def put(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def get(path):
    return json.loads(path.read_text(encoding="utf-8"))


def command(workspace, action, *args):
    result = subprocess.run([sys.executable, str(SCRIPTS / "research_output.py"), action,
                             "--workspace", str(workspace), *args], capture_output=True, check=True)
    return json.loads(result.stdout)


EVIDENCE = {"url": "https://example.test/source", "location": "Fig. 5", "text": "Measurement against the stated condition."}


@pytest.mark.parametrize("metric,subject,basis,unit,value,expected", [
    ("sample_rate", "aggregate_adc", "author_measured", "MS/s", 8100, "core"),
    ("sample_rate", "aggregate_adc", "author_measured", "GS/s", 5, "requirement_not_met"),
    ("sample_rate", "aggregate_adc", "cited_benchmark", "GS/s", 64, "insufficient_evidence"),
    ("input_frequency", "aggregate_adc", "author_measured", "GHz", 10, "insufficient_evidence"),
    ("sample_rate", "aggregate_adc", "author_measured", "Gb/s", 10, "insufficient_evidence"),
    ("sample_rate", "one_channel", "author_measured", "GS/s", 10, "insufficient_evidence"),
])
def test_qualification_keeps_number_role_unit_and_subject_separate(metric, subject, basis, unit, value, expected):
    rule = {"id": "rate", "metric": "sample_rate", "subject": "aggregate_adc", "basis": "author_measured",
            "unit": "GS/s", "min": 8}
    verdict = policy.evaluate_candidate({"review_evidence": {"observations": {"rate": {
        "metric": metric, "subject": subject, "basis": basis, "unit": unit, "value": value, "evidence": [EVIDENCE],
    }}}}, policy.resolve_policy({"requirements": [rule]}))
    assert verdict["decision"] == expected


def test_gap_priority_reserves_external_and_honors_costs():
    queries = [{"query_id": f"q{i}", "status": "completed", "result_count": 45, "top_k": 45} for i in range(6)]
    branches = [{"branch_id": "useful-route", "query_ids": ["q5"], "priority": 9,
                 "qualified_count": 7, "partition_by": "technology"}]
    sources = [{"source_id": "specialist", "strong_topic_match": True}]
    result = policy.plan_gap_checks(branches, queries, sources, remaining_budget=2, remaining_api_budget=1)
    assert [row["cache_key"] for row in result["checks"]] == ["split:q5", "formal-source:specialist"]
    assert all(row["status"] == "budget_exhausted" for row in result["deferred_checks"])
    resumed = policy.plan_gap_checks(branches, queries, sources, remaining_api_budget=0, allow_external=False)
    assert not resumed["checks"] and resumed["skipped_checks"][0]["status"] == "library_only"


def test_external_budget_is_separate_and_exhaustion_is_not_a_miss(tmp_path):
    work = output.init_workspace(tmp_path, "bounded")
    data = work / output.DATA_DIR
    put(data / "queries.json", {"max_api_requests": 0, "max_external_checks": 1})
    put(data / "papers.json", {"papers": [], "source_checks": [{"source_id": "specialist", "strong_topic_match": True}]})
    receipt = data / "receipt.json"
    put(receipt, {"cache_key": "formal-source:specialist", "status": "source_unavailable"})
    result = command(work, "review-next", "--receipt", str(receipt))
    assert result["checks"] == 0 and result["deferred"] == 1
    deferred, = get(data / "review-next.json")["deferred_checks"]
    assert deferred["status"] == "budget_exhausted" and deferred["previous_status"] == "source_unavailable"


def test_amplifier_operating_temperature_is_not_noise_or_rack_temperature():
    req = {"id": "temp", "metric": "operating_temperature", "subject": "target_amplifier",
           "basis": "author_measured", "unit": "K", "max": 16}
    fact = {"metric": "noise_temperature", "subject": "target_amplifier", "basis": "author_measured",
            "unit": "K", "value": 3, "evidence": [EVIDENCE]}
    assert policy.assess_requirements([req], {"temp": fact})[0]["status"] == "unknown"
    assert policy.assess_requirements([req], {"temp": {**fact, "metric": "operating_temperature",
                                                         "subject": "receiver_system"}})[0]["status"] == "unknown"
    assert policy.assess_requirements([req], {"temp": {**fact, "metric": "operating_temperature",
                                                         "unit": "mK", "value": 4200}})[0]["status"] == "met"


def test_report_places_proven_target_before_pretty_venue(tmp_path):
    papers = [
        {"title": "A related module in a prestigious system", "venue_quality": 100,
         "review_evidence": {"contribution": "system", "relevance": "unknown"}},
        {"title": "Measured target design", "venue_quality": 10,
         "review_evidence": {"contribution": "target_circuit", "relevance": "strong",
                             "measured_target": True, "evidence": [EVIDENCE]}}
    ]
    report = output.normalize_report({"review_policy": {"rank_by_evidence": True}, "papers": papers}, tmp_path)
    assert report["papers"][0]["title"] == "Measured target design"


def test_single_seed_related_publication_goes_through_receipt_and_screen(tmp_path):
    fixture = get(SCRIPTS.parent / "examples/circuit-review.json")["replay_catalog"]
    by_doi = {paper["doi"]: paper for paper in fixture["papers"]}
    relation = next(row for row in fixture["version_relations"] if row["relation_type"] == "shared_design")
    seed, discovered = by_doi[relation["to_doi"]], by_doi[relation["from_doi"]]
    from review_workflow import plan_review, record_review_outcome
    payload = {"papers": [seed], "review_policy": {"profile": "broad_discovery"},
               "reference_leads": [{"lead_id": "confirmed-version"}]}
    result = record_review_outcome(payload, {"cache_key": "lead:confirmed-version", "status": "completed_found",
                                            "evidence": relation["evidence"], "discoveries": [discovered],
                                            "version_relations": [relation]})
    queued = plan_review(result)["screening_candidates"]
    assert [paper["doi"] for paper in queued] == [discovered["doi"]]
    result["papers"] += queued
    report = output.normalize_report(result, tmp_path)
    assert len(report["papers"]) == 1
    assert report["papers"][0]["_versions"][0]["doi"] == discovered["doi"]
    assert output.render_ris(report).count("ER  -\n") == 2


def test_normal_cli_collect_receipt_screen_render_and_resume(tmp_path):
    workspace = output.init_workspace(tmp_path, "review")
    data = workspace / output.DATA_DIR
    put(data / "queries.json", {"max_api_requests": 1, "queries": [{"id": "wide"}]})
    put(data / "union.json", {"searches": [{"query_id": "wide", "status": "completed", "result_count": 15, "top_k": 15}],
                             "summary": {"successful_queries": 1}})
    source = data / "papers.json"
    put(source, {"schema": "ultrasearch-screened-papers/v2", "papers": [],
                 "source_checks": [{"source_id": "specialist", "strong_topic_match": True}]})
    key = "formal-source:specialist"
    receipt = data / "receipt.json"
    assert command(workspace, "review-next")["deferred"] == 1
    put(receipt, {"cache_key": key, "status": "index_no_records", "query_ids": ["wide"]})
    assert command(workspace, "review-next", "--receipt", str(receipt))["checks"] == 1
    put(receipt, {"cache_key": key, "status": "source_unavailable"})
    command(workspace, "review-next", "--receipt", str(receipt))
    plan = get(data / "review-next.json")
    assert plan["checks"][0]["previous_status"] == "source_unavailable"
    paper = {"title": "Verified external circuit", "doi": "10.5555/verified", "publication_status": "journal-article",
             "publication_evidence": [{**EVIDENCE, "kind": "publisher_record"}]}
    put(receipt, {"cache_key": key, "status": "completed_found", "evidence": [EVIDENCE], "discoveries": [paper]})
    assert command(workspace, "review-next", "--receipt", str(receipt))["screening_candidates"] == 1
    assert command(workspace, "render")["partial"]
    assert get(data / "report-data.json")["papers"] == []
    payload = get(source)
    payload["papers"] = get(data / "review-next.json")["screening_candidates"]
    put(source, payload)
    assert command(workspace, "review-next")["checks"] == 0
    assert command(workspace, "render", "--overwrite")["retained_papers"] == 1
    details = get(data / "report-data.json")
    assert details["gap_checks"][key]["status"] == "completed_found"
    assert details["gap_checks"][key + ":index"]["query_ids"] == ["wide"]
    put(data / "queries.json", {"library_only": True})
    assert command(workspace, "render", "--overwrite")["retained_papers"] == 0


def test_over_budget_collection_rejected_before_any_request(tmp_path, monkeypatch):
    spec = tmp_path / "plan.json"
    put(spec, {"max_api_requests": 0, "queries": [{"id": "test", "query": "ADC"}]})
    monkeypatch.setattr(remote, "remote_search", lambda *a, **k: pytest.fail("should not make a request"))
    from types import SimpleNamespace
    with pytest.raises(ValueError, match="max_api_requests"):
        remote.command_collect(SimpleNamespace(query_spec=spec))


def test_budgeted_plan_cannot_reuse_checkpoint_id_for_changed_query(tmp_path, monkeypatch):
    from types import SimpleNamespace
    config = {"base_url": "https://example.test", "email": "reader@example.test"}
    monkeypatch.setattr(remote, "load_config", lambda: config)
    old = {"id": "same", "query": "original"}
    spec = tmp_path / "plan.json"
    put(spec, {"max_api_requests": 1, "queries": [{**old, "query": "changed"}]})
    checkpoint = remote._checkpoint_path(tmp_path, "same")
    put(checkpoint, {"status": "completed",
                     "_request_fingerprint": remote._request_fingerprint(remote.normalize_request(old), config)})
    with pytest.raises(ValueError, match="changed"):
        remote.command_collect(SimpleNamespace(query_spec=spec, checkpoint_dir=tmp_path))

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/chipseeker-ultra-search/scripts/chipseeker_remote.py"
SPEC = importlib.util.spec_from_file_location("remote_client_test", SCRIPT)
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)


def completed():
    return {"job_id": "job-1", "status": "done", "result": {"results": [], "result_count": 0}}


def test_poll_502_recovers_same_job_without_resubmitting(monkeypatch):
    calls, sleeps = [], []

    def request(method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            return {"job_id": "job-1", "status": "queued"}
        if len(calls) == 2:
            raise remote.RemoteError("gateway unavailable", 502)
        return completed()

    monkeypatch.setattr(remote, "api_request", request)
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    assert remote.remote_search({"query": "LNA"})["result_count"] == 0
    assert sum(method == "POST" for method, _ in calls) == 1
    assert len(sleeps) == 1


def test_busy_submission_retries_after_server_delay_with_same_identity(monkeypatch):
    keys, sleeps = [], []

    def request(method, path, **kwargs):
        if method == "POST":
            keys.append(kwargs["payload"]["client_request_id"])
            if len(keys) == 1:
                raise remote.RemoteError("busy", 429, retry_after=7)
            return {"job_id": "job-1", "status": "queued"}
        return completed()

    monkeypatch.setattr(remote, "api_request", request)
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    remote.remote_search({"query": "LNA"})
    assert len(keys) == 2 and keys[0] == keys[1]
    assert sleeps == [7]


def test_long_server_retry_after_is_deferred_without_early_retry(monkeypatch):
    calls, sleeps = [], []

    def request(*args, **kwargs):
        calls.append(args)
        raise remote.RemoteError("busy", 429, retry_after=120)

    monkeypatch.setattr(remote, "api_request", request)
    monkeypatch.setattr(remote.time, "sleep", sleeps.append)
    with pytest.raises(remote.RemoteError) as error:
        remote.remote_search({"query": "LNA"})
    assert error.value.retry_after == 120
    assert len(calls) == 1 and sleeps == []


def test_retry_after_http_date_and_invalid_values():
    value = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=180), usegmt=True)
    assert 178 <= remote._retry_after_seconds(value) <= 180
    for value in ("invalid", "NaN", "inf", -2):
        assert remote._retry_after_seconds(value) == 0


def test_saved_cooldown_blocks_reinvocation_until_server_wait_elapsed(monkeypatch, tmp_path):
    calls = []
    clock = [1000.0]

    def search(*args, **kwargs):
        calls.append(args)
        raise remote.RemoteError("busy", 429, retry_after=120)

    monkeypatch.setattr(remote, "load_config", lambda: {"base_url": "https://example.com", "email": "buyer@example.com"})
    monkeypatch.setattr(remote, "remote_search", search)
    monkeypatch.setattr(remote.time, "time", lambda: clock[0])
    entry = {"id": "cooldown", "query": "LNA"}
    first = remote._run_entry(entry, {}, tmp_path, 1)
    assert first["_retry_not_before"] == 1120
    second = remote._run_entry(entry, {}, tmp_path, 1)
    assert second["_client_request_id"] == first["_client_request_id"]
    assert len(calls) == 1
    clock[0] = 1121
    remote._run_entry(entry, {}, tmp_path, 0)
    assert len(calls) == 2


def test_collect_during_saved_cooldown_makes_no_service_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(remote, "load_config", lambda: {"base_url": "https://example.com", "email": "buyer@example.com"})
    monkeypatch.setattr(remote.time, "time", lambda: 1000.0)

    def busy(*args, **kwargs):
        raise remote.RemoteError("busy", 429, retry_after=120)

    entry = {"id": "cooldown", "query": "LNA"}
    checkpoints = tmp_path / "checkpoints"
    monkeypatch.setattr(remote, "remote_search", busy)
    remote._run_entry(entry, {}, checkpoints, 0)

    def forbidden(*args, **kwargs):
        raise AssertionError("No service contact is allowed during the saved cooldown")

    monkeypatch.setattr(remote, "remote_search", forbidden)
    monkeypatch.setattr(remote, "api_request", forbidden)
    spec = tmp_path / "queries.json"
    spec.write_text(json.dumps({"queries": [entry]}), encoding="utf-8")
    args = SimpleNamespace(query_spec=spec, workers=1, checkpoint_dir=checkpoints, retries=0, output=tmp_path / "union.json")
    assert remote.command_collect(args) == 2
    merged = json.loads(args.output.read_text(encoding="utf-8"))
    assert merged["searches"][0]["retry_not_before"] == 1120


def test_transport_interruption_resumes_saved_job(monkeypatch, tmp_path):
    calls = []
    broken = [True]

    def request(method, path, **kwargs):
        calls.append((method, path))
        if method == "POST":
            return {"job_id": "job-1", "status": "running"}
        if broken[0]:
            raise remote.RemoteError("gateway", 502)
        return completed()

    monkeypatch.setattr(remote, "api_request", request)
    monkeypatch.setattr(remote, "load_config", lambda: {"base_url": "https://example.com", "email": "buyer@example.com"})
    monkeypatch.setattr(remote.time, "sleep", lambda _: None)
    entry = {"id": "q1", "query": "LNA"}
    failed = remote._run_entry(entry, {}, tmp_path, 0)
    assert failed["status"] == "failed"
    assert failed["_client_job_id"] == "job-1"
    broken[0] = False
    resumed = remote._run_entry(entry, {}, tmp_path, 0)
    assert resumed["status"] == "completed"
    assert sum(method == "POST" for method, _ in calls) == 1


def test_metadata_enriches_paper_without_inflating_discovery_evidence():
    entries = [
        {"id": "search", "mode": "lite", "purpose": "discovery", "query_family": "direct"},
        {"id": "hydrate", "mode": "keyword", "purpose": "metadata", "query_family": "lookup"},
    ]
    responses = [
        {"status": "completed", "_entry": entries[0], "result_count": 1,
         "results": [{"title": "A LNA", "year": "2025", "doi": "", "similarity": .9}]},
        {"status": "completed", "_entry": entries[1], "result_count": 1,
         "results": [{"title": "A LNA", "year": "2025", "doi": "doi:10.1/a", "similarity": 1,
                      "abstract": "Original source abstract.", "abstract_kind": "source_abstract", "abstract_truncated": False}]},
    ]
    merged = remote.merge_responses(responses, entries)
    paper = merged["papers"][0]
    assert len(merged["papers"]) == 1
    assert paper["abstract"] == "Original source abstract."
    assert paper["abstract_truncated"] is False
    assert paper["retrieval_family_count"] == 1
    assert paper["ranking"]["best_lite_similarity"] == .9
    assert merged["summary"]["successful_metadata_queries"] == 1
    assert merged["summary"]["discovery_raw_results"] == 1


def test_literal_only_results_do_not_get_fabricated_semantic_relevance():
    response = {"status": "completed", "_entry": {"id": "kw", "mode": "keyword"},
                "result_count": 1, "results": [{"title": "Uncertain", "doi": "10.1/b", "similarity": 1}]}
    paper = remote.merge_responses([response], [{}])["papers"][0]
    assert paper["ranking"]["relevance"] is None
    assert paper["ranking"]["relevance_basis"] == "literal_only_unrated"


def test_different_dois_are_not_merged_even_if_titles_equal():
    response = {"status": "completed", "_entry": {"id": "kw", "mode": "keyword"}, "result_count": 2,
                "results": [{"title": "Common title", "year": "2025", "doi": doi} for doi in ("10.1/a", "10.1/b")]}
    assert len(remote.merge_responses([response], [{}])["papers"]) == 2


def test_partial_collect_saves_results_and_returns_nonzero(monkeypatch, tmp_path):
    spec = tmp_path / "queries.json"
    spec.write_text(json.dumps({"queries": [{"id": "ok"}, {"id": "broken"}]}), encoding="utf8")

    def run(entry, *args):
        return {"_entry": entry, "status": "completed" if entry["id"] == "ok" else "failed",
                "result_count": 0, "results": []}

    monkeypatch.setattr(remote, "_run_entry", run)
    monkeypatch.setattr(remote, "api_request", lambda *args: {"quota": {"credits_left": 1}})
    args = SimpleNamespace(query_spec=spec, workers=2, checkpoint_dir=tmp_path / "checkpoints", retries=0, output=tmp_path / "union.json")
    assert remote.command_collect(args) == 2
    merged = json.loads(args.output.read_text(encoding="utf8"))
    assert merged["status"] == "partial"
    assert merged["failed_queries"] == ["broken"]


def test_query_id_collisions_rejected_before_any_search(tmp_path):
    spec = tmp_path / "queries.json"
    spec.write_text(json.dumps({"queries": [{"id": "duplicate"}, {"id": "duplicate"}]}))
    with pytest.raises(ValueError, match="unique ID"):
        remote.command_collect(SimpleNamespace(query_spec=spec))


def test_non_ascii_titles_do_not_collapse_to_empty_identity():
    assert remote._identity({"title": "甲", "year": "2025"}) != remote._identity({"title": "乙", "year": "2025"})


def test_intermediate_union_does_not_misreport_pending_queries_as_success():
    response = {"status": "pending", "_entry": {"id": "later"}, "result_count": 0, "results": []}
    result = remote.merge_responses([response], [{}])
    assert result["status"] == "running"
    assert result["summary"]["successful_queries"] == 0
    assert result["pending_queries"] == ["later"]
    assert result["failed_queries"] == []


def test_structured_selector_string_is_rejected_instead_of_split_into_letters():
    with pytest.raises(ValueError, match="must be a list"):
        remote.normalize_request({"mode": "keyword", "dois": "10.1/a"})

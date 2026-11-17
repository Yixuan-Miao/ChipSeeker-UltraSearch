"""A formal DOI on an arXiv record is not formal-source coverage."""

import copy
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/chipseeker-ultra-search/scripts/chipseeker_remote.py"
SPEC = importlib.util.spec_from_file_location("provenance_remote", SCRIPT)
remote = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote)


def test_same_doi_prefers_formal_metadata_without_losing_preprint_origin():
    preprint = {"doi": "10.1109/example", "title": "Author manuscript", "year": 2024,
                "venue": "arXiv", "source_links": {"arxiv": "https://arxiv.org/abs/2401.00001"},
                "origin": "chipseeker_remote_api", "source_in_current_corpus": True,
                "primary_source": "https://arxiv.org/abs/2401.00001", "pdf_link": "https://arxiv.org/pdf/2401.00001",
                "abstract": "A longer preprint abstract that must not replace the formal abstract."}
    formal = {"doi": "10.1109/example", "title": "Published title", "year": 2025,
              "venue": "IEEE Transactions on Applied Superconductivity", "volume": "35", "issue": "2",
              "origin": "external_primary_source", "source_in_current_corpus": False,
              "primary_source": "https://doi.org/10.1109/example",
              "abstract": "Formal abstract."}
    for items in ([preprint, formal], [formal, preprint]):
        responses = [{"status": "completed", "results": [p], "result_count": 1,
                      "_entry": {"id": str(i), "mode": "keyword"}} for i, p in enumerate(items)]
        original = copy.deepcopy(responses)
        merged = remote.merge_responses(responses, [])
        assert responses == original
        assert len(merged["papers"]) == 1
        paper = merged["papers"][0]
        assert paper["title"] == "Published title"
        assert paper["year"] == 2025
        assert {s["venue"] for s in paper["publication_sources"]} == {preprint["venue"], formal["venue"]}
        assert paper["origin"] == formal["origin"]
        assert paper["source_in_current_corpus"] is False
        assert paper["primary_source"] == formal["primary_source"]
        assert paper["abstract"] == formal["abstract"]
        assert not paper.get("pdf_link") and not paper.get("source_links")
        arxiv = next(source for source in paper["publication_sources"] if source["venue"] == "arXiv")
        assert arxiv["source_links"]["arxiv"].endswith("2401.00001")
        assert arxiv["pdf_link"] == preprint["pdf_link"]


def test_formal_doi_alone_keeps_arxiv_provenance():
    p = {"doi": "10.1109/example", "title": "Manuscript", "venue": "arXiv"}
    result = remote.merge_responses([{"results": [p], "result_count": 1, "_entry": {"id": "one"}}], [])
    assert result["papers"][0]["publication_sources"] == [p]


def test_collector_receipts_drive_partition_and_pending_recovery():
    import sys
    sys.path.insert(0, str(SCRIPT.parent))
    from review_policy import plan_gap_checks
    capped = {"status": "completed", "result_count": 150, "results": [],
              "_entry": {"id": "cap", "top_k": 150, "years": [2016, 2026]}}
    running = {"status": "running", "_client_request_id": "same-billable-request", "_client_job_id": "accepted",
               "_entry": {"id": "pending", "top_k": 150}}
    union = remote.merge_responses([capped, running], [])
    plan = plan_gap_checks(queries=union["searches"])
    partition, resume = plan["checks"]
    assert partition["partition_by"] == "years"
    assert resume["action"] == "resume_query"
    assert resume["client_request_id"] == "same-billable-request"


def test_run_entry_preserves_effective_keyword_identity_and_bounds(monkeypatch):
    import sys
    sys.path.insert(0, str(SCRIPT.parent))
    from review_policy import query_observation

    monkeypatch.setattr(remote, "load_config", lambda: {"base_url": "https://example.test"})
    monkeypatch.setattr(remote, "remote_search", lambda request, **kwargs: {"results": [], "result_count": 0})
    defaults = {"mode": "keyword", "years": "1800:2100", "venues": ["JSSC"], "top_k": 150}
    entries = [{"id": "doi", "dois": ["10.1109/example"]}, {"id": "title", "exact_titles": ["Paper"]},
               {"id": "terms", "query": "LNA"}, {"id": "filtered", "mode": "filtered_lite", "exact_titles": ["Paper"]}]
    responses = [remote._run_entry(entry, defaults, None, 0) for entry in entries]
    searches = remote.merge_responses(responses, entries)["searches"]
    assert [query_observation(query) for query in searches] == [
        "index_no_records", "index_no_records", "completed_zero_results", "completed_zero_results",
    ]
    assert all(query["years"] == [1800, 2100] and query["venues"] == ["JSSC"] and query["top_k"] == 150
               for query in searches)

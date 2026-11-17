"""Connect remote collection, agent gap checks and evidence-screened reports."""

import json
from pathlib import Path
import re

from chipseeker_remote import _write_json
from review_policy import _evidence, plan_gap_checks, record_gap_check, resolve_policy
from work_versions import confirmed_relations, is_formal_publication, normalize_doi, publication_key


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def prepare_review(payload, data_dir):
    """Refresh observations from the collector without accepting unscreened candidates."""
    payload = dict(payload)
    plan = load_json(data_dir / "queries.json")
    union = load_json(data_dir / "union.json")
    options = dict(payload.get("review_policy") or {})
    if "max_api_requests" in plan:
        limit = plan["max_api_requests"]
        if type(limit) is not int or limit < 0:
            raise ValueError("max_api_requests must be a nonnegative integer")
        # Registered IDs include pending requests. Replaying the same plan reuses checkpoints.
        options["remaining_api_budget"] = max(0, limit - len(plan.get("queries") or []))
    if "max_external_checks" in plan:
        limit = plan["max_external_checks"]
        if type(limit) is not int or limit < 0:
            raise ValueError("max_external_checks must be a nonnegative integer")
        attempted = sum(receipt.get("attempts", 1) for receipt in (payload.get("gap_checks") or {}).values()
                        if receipt.get("channel") == "external")
        options["remaining_external_budget"] = max(0, limit - attempted)
    if plan.get("library_only") is True:
        options["external_check"] = False
        options["library_only"] = True
    payload["review_policy"] = options
    if "searches" in union:
        payload["searches"] = union["searches"]
        summary = dict(payload.get("summary") or {})
        for key in ("successful_queries", "failed_queries", "pending_queries", "unique_papers"):
            if key in union.get("summary", {}):
                summary[key] = union["summary"][key]
        payload["summary"] = summary
    return payload


def plan_review(payload):
    policy = resolve_policy(payload.get("review_policy"), payload.get("generated_at"))
    result = plan_gap_checks(
        payload.get("research_branches") or [], payload.get("searches") or [], payload.get("source_checks") or [],
        reference_leads=payload.get("reference_leads") or [], max_checks=policy["max_gap_checks"],
        remaining_budget=policy.get("remaining_budget"), remaining_api_budget=policy.get("remaining_api_budget"),
        completed_checks=payload.get("gap_checks") or {}, allow_external=policy["external_check"],
        remaining_external_budget=policy.get("remaining_external_budget"),
    )
    reviewed = {publication_key(paper) for paper in payload.get("papers") or []}
    reviewed.update("doi:" + normalize_doi(doi) for doi in payload.get("gap_screened_dois") or [])
    queue = {}
    if policy["external_check"]:
        for receipt in (payload.get("gap_checks") or {}).values():
            for paper in receipt.get("discoveries") or []:
                identity = publication_key(paper)
                if identity not in reviewed:
                    item = queue.setdefault(identity, {**paper, "gap_receipt_keys": []})
                    item["gap_receipt_keys"].append(receipt["cache_key"])
    result["screening_candidates"] = list(queue.values())
    return result


def record_review_outcome(payload, receipt):
    plan = plan_review(payload)
    checks = plan["checks"] + [item for item in plan["deferred_checks"] if item["channel"] == "index"]
    check = next((item for item in checks if item["cache_key"] == receipt["cache_key"]), None)
    if check is None:
        raise ValueError("Unknown, already completed, or disabled gap; inspect the current review plan")
    outcome = receipt["status"]
    evidence = receipt.get("evidence") or []
    papers = receipt.get("discoveries") or []
    if papers and (check["channel"] != "external" or outcome != "completed_found"):
        raise ValueError("External discoveries belong to a completed_found external receipt")
    if outcome in {"completed_found", "completed_no_match"} and check["channel"] == "external" and not _evidence(evidence):
        raise ValueError("A completed external check requires source evidence and a location")
    if papers and any(not paper.get("title") or not re.fullmatch(r"10\.\d{4,9}/\S+", normalize_doi(paper.get("doi")))
                      or not is_formal_publication(paper)
                      or not _evidence(paper.get("publication_evidence")) for paper in papers):
        raise ValueError("A discovered candidate requires verified formal metadata and source location")
    candidates = [{**paper, "origin": "external_primary_source"} for paper in papers]
    result = record_gap_check(payload, check, outcome, evidence=evidence, query_ids=receipt.get("query_ids") or [],
                              discoveries=candidates)
    if receipt.get("version_relations"):
        catalog = (payload.get("papers") or []) + candidates
        relations = confirmed_relations(receipt["version_relations"], catalog)
        if len(relations) != len(receipt["version_relations"]):
            raise ValueError("Version relations require explicit evidence and both verified formal records")
        saved = result.setdefault("version_relations", [])
        saved.extend(item for item in relations if item not in saved)
    return result


def review_workspace(data_dir, receipt_path=None):
    source = data_dir / "papers.json"
    payload = load_json(source)
    if not isinstance(payload.get("papers"), list):
        raise ValueError("Write initial screening decisions in papers.json before planning follow-ups")
    prepared = prepare_review(payload, data_dir)
    if receipt_path:
        receipt = load_json(Path(receipt_path))
        if not receipt.get("cache_key") or not receipt.get("status"):
            raise ValueError("Gap receipt needs a cache_key and status")
        updated = record_review_outcome(prepared, receipt)
        payload["gap_checks"] = updated["gap_checks"]
        if "version_relations" in updated:
            payload["version_relations"] = updated["version_relations"]
        _write_json(source, payload)
        prepared = prepare_review(payload, data_dir)
    result = plan_review(prepared)
    path = data_dir / "review-next.json"
    _write_json(path, result)
    return {"plan": str(path), "checks": len(result["checks"]), "deferred": len(result["deferred_checks"]),
            "screening_candidates": len(result["screening_candidates"])}

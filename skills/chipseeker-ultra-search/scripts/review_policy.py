#!/usr/bin/env python3
"""Evidence-based circuit-review screening and bounded gap-check planning.

The customer's agent supplies source-backed contribution decisions. This module
applies the saved policy reproducibly. A title, retrieval score, prestigious
venue or a DOI on an arXiv record never establishes the main contribution or
formal-publication provenance on its own.
"""

from copy import deepcopy
from datetime import date
import importlib.util
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit


DECISION_LABELS = {
    "core": "核心收录",
    "manual_exclusion": "用户手动排除",
    "system_contribution": "主贡献为系统或其他电路",
    "outside_window": "超出时间窗",
    "source_priority": "来源优先级不符",
    "off_topic": "与目标电路相关性不足",
    "insufficient_evidence": "证据不足待核验",
    "output_limit": "超过本次展示上限",
}
FORMAL_PROVENANCE = {"publisher_record", "doi_registration", "official_proceedings"}


def _date(value):
    return date.fromisoformat(str(value)[:10])


def resolve_policy(policy=None, generated_at=None):
    """Resolve relative defaults once, using the saved search date on refresh."""
    options = dict(policy or {})
    profile = options.get("profile", "broad_discovery")
    if profile not in {"circuit_review", "broad_discovery"}:
        raise ValueError("profile must be circuit_review or broad_discovery")
    search_date = _date(options.get("search_date") or generated_at or date.today())
    strict = profile == "circuit_review"
    start = None
    if strict:
        try:
            start = search_date.replace(year=search_date.year - 10)
        except ValueError:
            start = search_date.replace(year=search_date.year - 10, day=28)
    resolved = {
        "profile": profile,
        "search_date": search_date.isoformat(),
        "start_date": start.isoformat() if start else None,
        "end_date": search_date.isoformat() if strict else None,
        "preferred_publishers": ["IEEE"] if strict else [],
        "allowed_publishers": [],
        "allowed_publication_types": ["journal-article", "proceedings-article"] if strict else [],
        "external_check": True,
        "attach_out_of_scope_versions": False,
        "max_results": 200,
        "max_gap_checks": 12,
    }
    resolved.update(options)
    if "years" in options:
        first, last = options["years"]
        resolved["start_date"] = date(int(first), 1, 1).isoformat()
        resolved["end_date"] = date(int(last), 12, 31).isoformat()
    lower = _date(resolved["start_date"]) if resolved["start_date"] is not None else date.min
    upper = _date(resolved["end_date"]) if resolved["end_date"] is not None else date.max
    if lower > upper:
        raise ValueError("start_date must not follow end_date")
    limit = resolved["max_results"]
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("max_results must be a positive integer")
    for key in ("preferred_publishers", "allowed_publishers", "allowed_publication_types"):
        if not isinstance(resolved[key], list) or any(not isinstance(value, str) or not value.strip() for value in resolved[key]):
            raise ValueError(key + " must be a list of non-empty strings")
    return resolved


def _doi(value):
    return re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(value or ""), flags=re.I).strip().lower()


def _title(value):
    return "".join(c for c in unicodedata.normalize("NFKC", str(value or "")).casefold() if c.isalnum())


def publication_ids(paper):
    """Use publication identity, preserving the no-DOI hydration fallback."""
    identifiers = []
    if paper.get("doi"):
        identifiers.append("doi:" + _doi(paper["doi"]))
    if paper.get("id"):
        identifiers.append("id:" + str(paper["id"]))
    if paper.get("title"):
        identifiers.append("title:" + _title(paper["title"]) + ":" + str(paper.get("year", "")))
    return identifiers


def _work_id(paper):
    return str(paper.get("work_id") or "") if paper.get("work_id_confirmed") is True else ""


def _matches_exclusion(paper, entry):
    work = _work_id(paper)
    if work and work == entry.get("work_id"):
        return True
    saved = set(entry.get("publication_ids") or [])
    current = set(publication_ids(paper))
    saved_dois = {value for value in saved if value.startswith("doi:")}
    current_dois = {value for value in current if value.startswith("doi:")}
    if saved_dois and current_dois:
        return bool(saved_dois & current_dois)
    return bool(saved & current)


def record_manual_exclusion(payload, paper, reason="用户手动排除"):
    """Return a payload with a durable publication/confirmed-work tombstone."""
    result = deepcopy(payload)
    ledger = result.setdefault("manual_exclusions", [])
    entry = next((row for row in ledger if _matches_exclusion(paper, row)), None)
    if entry is None:
        entry = {"publication_ids": [], "reason": reason}
        ledger.append(entry)
    entry["publication_ids"] = sorted(set(entry["publication_ids"]) | set(publication_ids(paper)))
    if _work_id(paper):
        entry["work_id"] = _work_id(paper)
    return result


def _version_module():
    spec = importlib.util.spec_from_file_location("review_work_versions", Path(__file__).with_name("work_versions.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expand_version_exclusions(ledger, relations):
    if not relations:
        return
    # The old seed may be absent from refreshed candidates. Its saved exclusion
    # still propagates through independently checked version statements.
    accepted = _version_module().confirmed_relations(relations)
    changed = True
    while changed:
        changed = False
        for relation in accepted:
            endpoints = {"doi:" + _doi(relation[key]) for key in ("from_doi", "to_doi")}
            for entry in ledger:
                saved = set(entry["publication_ids"])
                if saved & endpoints and not endpoints <= saved:
                    entry["publication_ids"] = sorted(saved | endpoints)
                    changed = True


def _evidence(rows):
    """Require a source URL and an inspectable location and observation."""
    valid = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            url = urlsplit(str(row.get("url") or ""))
        except ValueError:
            continue
        observation = row.get("text") or row.get("note") or row.get("excerpt")
        if url.scheme in {"http", "https"} and url.hostname and row.get("location") and observation:
            valid.append({**deepcopy(row), "text": observation})
    return valid


def _publication_window(paper, policy):
    # The formal issue year and online-publication year remain separate fields.
    # Exact dates are used when available. Year-only boundary records remain
    # visible with their limited precision instead of inventing a month/day.
    lower = _date(policy["start_date"]) if policy["start_date"] is not None else date.min
    upper = _date(policy["end_date"]) if policy["end_date"] is not None else date.max
    value = paper.get("publication_date")
    if value:
        try:
            published = _date(value)
            return lower <= published <= upper, "day"
        except ValueError:
            return None, "invalid"
    value = paper.get("issue_year") or paper.get("year")
    if not re.fullmatch(r"\d{4}", str(value or "")):
        return None, "unknown"
    return lower.year <= int(value) <= upper.year, "year"


def _publisher_name(value):
    name = str(value or "").strip().casefold()
    return "ieee" if name == "institute of electrical and electronics engineers (ieee)" else name


def evaluate_candidate(paper, policy):
    """Return the auditable decision; publication origin and readability stay separate."""
    evidence = paper.get("review_evidence") or {}
    citations = _evidence(evidence.get("evidence"))
    publication = [row for row in _evidence(paper.get("publication_evidence") or evidence.get("publication_evidence"))
                   if row.get("kind") in FORMAL_PROVENANCE]
    in_window, precision = _publication_window(paper, policy)
    strict = policy["profile"] == "circuit_review"
    limited_dates = policy["start_date"] is not None or policy["end_date"] is not None
    kind = paper.get("publication_status") or paper.get("publication_type") or evidence.get("publication_type")
    kind = {"conference-paper": "proceedings-article"}.get(kind, kind)
    publisher = _publisher_name(paper.get("publisher") or evidence.get("publisher"))
    allowed_publishers = {_publisher_name(name) for name in policy["allowed_publishers"]}
    publishers = set() if allowed_publishers else {_publisher_name(name) for name in policy["preferred_publishers"]}
    types = policy["allowed_publication_types"]
    exception = (strict and evidence.get("exception_reason") and evidence.get("relevance") == "strong"
                 and evidence.get("measured_target") is True and citations)
    reasons = []
    if strict and evidence.get("contribution") in {"system", "supporting_module", "other_circuit"} and citations:
        reasons.append("system_contribution")
    if limited_dates and in_window is False:
        reasons.append("outside_window")
    if ((types and kind and kind not in types)
            or (allowed_publishers and publisher and publisher not in allowed_publishers)
            or (publishers and publisher and publisher not in publishers and not exception)):
        reasons.append("source_priority")
    if evidence.get("relevance") == "weak" and citations:
        reasons.append("off_topic")
    if ((limited_dates and in_window is None) or (types and not kind) or ((publishers or allowed_publishers) and not publisher)
            or (strict and (evidence.get("contribution") not in {"target_circuit", "system", "supporting_module", "other_circuit"}
                            or evidence.get("relevance") not in {"strong", "weak"}
                            or not citations or not publication or not kind or not publisher))):
        reasons.append("insufficient_evidence")
    code = reasons[0] if reasons else "core"
    return {
        "decision": code,
        "label": DECISION_LABELS[code],
        "reason_zh": "；".join(DECISION_LABELS[reason] for reason in dict.fromkeys(reasons)) or DECISION_LABELS[code],
        "reasons": list(dict.fromkeys(reasons)),
        "source_origin": paper.get("origin") or "unspecified",
        "readability": evidence.get("readability") or ("full_text" if paper.get("full_text_read") else "unknown"),
        "date_precision": precision,
        "contribution": evidence.get("contribution") or "unknown",
        "measured_target": evidence.get("measured_target"),
        "evidence": citations,
        "publication_evidence": publication,
    }


def apply_review_policy(payload, *, exclusions=None):
    """Screen a report, retaining decisions and manual exclusions across refreshes.

    Input order is the agent's evidence-based reading order. The output limit is
    applied after eligibility, per confirmed work family, without changing the
    recorded eligibility decision or dropping a family's second publication.
    """
    result = deepcopy(payload)
    policy = resolve_policy(result.get("review_policy"), result.get("generated_at"))
    result["review_policy"] = policy
    result.setdefault("manual_exclusions", []).extend(deepcopy(exclusions or []))
    papers = result.get("papers") or []
    versions = _version_module()
    catalog = [versions.canonicalize_publication(paper) for paper in papers]
    relations = versions.confirmed_relations(result.get("version_relations") or [], catalog)
    # Recompute rather than trusting imported work_id/work_id_confirmed fields.
    papers = versions.assign_work_ids(papers, relations)
    for paper in papers:
        if paper.get("manual_excluded") and not any(_matches_exclusion(paper, row) for row in result["manual_exclusions"]):
            result["manual_exclusions"].append({"publication_ids": publication_ids(paper), "work_id": _work_id(paper),
                                                 "reason": paper.get("exclusion_reason") or "用户手动排除"})
        # A saved no-DOI exclusion can acquire its DOI after hydration. A later
        # confirmed version relation similarly extends it to the verified work.
        for entry in result["manual_exclusions"]:
            if _matches_exclusion(paper, entry):
                entry["publication_ids"] = sorted(set(entry["publication_ids"]) | set(publication_ids(paper)))
                if _work_id(paper):
                    entry["work_id"] = _work_id(paper)
    ledger = result["manual_exclusions"]
    _expand_version_exclusions(ledger, result.get("version_relations") or [])
    audit = {tuple(row["publication_ids"]): row for row in result.get("screening_audit", [])}
    retained = []
    displayed_works = set()
    for paper in papers:
        decision = evaluate_candidate(paper, policy)
        excluded = next((entry for entry in ledger if _matches_exclusion(paper, entry)), None)
        if excluded:
            decision.update(decision="manual_exclusion", label=DECISION_LABELS["manual_exclusion"],
                            reasons=["manual_exclusion"], manual_reason=excluded.get("reason", ""),
                            reason_zh=excluded.get("reason") or DECISION_LABELS["manual_exclusion"])
        if decision["decision"] == "core":
            work = _work_id(paper) or tuple(publication_ids(paper))
            if work in displayed_works or len(displayed_works) < policy["max_results"]:
                displayed_works.add(work)
                retained.append(paper)
            else:
                decision.update(decision="output_limit", label=DECISION_LABELS["output_limit"],
                                reasons=["output_limit"], eligibility="core", reason_zh=DECISION_LABELS["output_limit"])
        paper["review_decision"] = decision
        identifiers = publication_ids(paper)
        # Remove the previous audit identity after metadata hydration gains DOI.
        for old_key in list(audit):
            if _matches_exclusion(paper, {"publication_ids": list(old_key)}):
                del audit[old_key]
        audit[tuple(identifiers)] = {"publication_ids": identifiers, "title": paper.get("title"),
                                     "doi": paper.get("doi"), "work_id": _work_id(paper), **decision}
    result["papers"] = retained
    result["screening_audit"] = list(audit.values())
    result["screening_summary"] = {"retained_publications": len(retained), "displayed_works": len(displayed_works),
                                   "audited_publications": len(audit)}
    result["gap_check_plan"] = plan_gap_checks(
        result.get("research_branches") or [], result.get("searches") or [], result.get("source_checks") or [],
        reference_leads=result.get("reference_leads") or [], max_checks=policy["max_gap_checks"],
        remaining_budget=policy.get("remaining_budget"), completed_checks=result.get("gap_checks") or {},
        allow_external=policy["external_check"],
    )
    return result


def query_observation(query):
    """Describe the observed request, without extrapolating to literature coverage."""
    state = query.get("status")
    if state in {"queued", "running"}:
        return "pending"
    if state in {"failed", "pending", "source_unavailable"}:
        return state
    if state != "completed":
        return "not_run"
    count = int(query.get("result_count") or 0)
    if query.get("possibly_truncated") or count >= int(query.get("top_k") or 200):
        return "truncated"
    if count == 0:
        return "index_no_records" if query.get("identity_lookup") else "completed_zero_results"
    return "completed_visible_results"


def record_gap_check(payload, check, outcome, *, evidence=(), query_ids=()):
    """Save a repeatable check receipt, including failures and index visibility."""
    allowed = {"completed_found", "completed_no_match", "index_no_records", "source_unavailable", "failed", "pending", "truncated"}
    if outcome not in allowed:
        raise ValueError("Unknown gap-check outcome: " + str(outcome))
    result = deepcopy(payload)
    receipts = result.setdefault("gap_checks", {})
    receipts[check["cache_key"]] = {"cache_key": check["cache_key"], "action": check["action"],
                                    "status": outcome, "evidence": _evidence(evidence),
                                    "query_ids": list(query_ids), "trigger": check.get("reason", "")}
    return result


def plan_gap_checks(branches=(), queries=(), source_checks=(), *, reference_leads=(),
                    max_checks=12, remaining_budget=None, completed_checks=(), allow_external=True):
    """Plan bounded agent-side checks, reusing completed cache keys.

    Source checks must distinguish a formal publisher import from a preprint
    carrying a final DOI. Unsaturated searches describe only the visible index.
    Every plan item includes its trigger and evidence link supplied by the agent.
    The planner performs no network requests and no server-side model work.
    """
    if max_checks < 0 or remaining_budget is not None and remaining_budget < 0:
        raise ValueError("gap-check budgets cannot be negative")
    budget = min(max_checks, remaining_budget) if remaining_budget is not None else max_checks
    plans = []
    observations = []
    for query in queries:
        state = query_observation(query)
        observations.append({"query_id": query.get("query_id"), "state": state,
                             "coverage_scope": "visible_index_only", "result_count": query.get("result_count")})
        if state in {"failed", "pending"}:
            plans.append({"cache_key": "resume:" + str(query.get("query_id")), "action": "resume_query",
                          "reason": state, "query_id": query.get("query_id"),
                          "client_request_id": query.get("client_request_id")})
        if state == "truncated":
            years = query.get("years") or []
            split = "years" if len(years) == 2 and years[0] < years[1] else "venue_or_technical_branch"
            plans.append({"cache_key": "split:" + str(query.get("query_id")), "action": "partition_query",
                          "reason": "truncated", "query_id": query.get("query_id"), "partition_by": split})
    if allow_external:
        for source in source_checks:
            if source.get("strong_topic_match") and not source.get("formal_records_seen"):
                plans.append({"cache_key": "formal-source:" + str(source["source_id"]),
                              "action": "check_formal_source", "reason": "specialist_formal_source_missing",
                              "source_id": source["source_id"], "evidence": source.get("evidence", []),
                              "preprint_doi_is_coverage_evidence": False})
        for branch in branches:
            if branch.get("candidate_count", 0) <= branch.get("sparse_threshold", 2):
                plans.append({"cache_key": "branch:" + str(branch["branch_id"]), "action": "check_branch",
                              "reason": "sparse_branch", "branch_id": branch["branch_id"],
                              "terms": branch.get("terms", []), "sources": branch.get("sources", [])})
        for lead in reference_leads:
            plans.append({"cache_key": "lead:" + str(lead["lead_id"]), "action": "follow_reference_lead",
                          "reason": "benchmark_or_citation_or_author_lead", "lead_id": lead["lead_id"],
                          "evidence": lead.get("evidence", []), "terms": lead.get("terms", []),
                          "max_hops": 1})
    completed = set()
    receipts = completed_checks.values() if isinstance(completed_checks, dict) else completed_checks
    for receipt in receipts:
        if isinstance(receipt, str):
            completed.add(receipt)
        elif receipt.get("status") in {"completed_found", "completed_no_match", "index_no_records"}:
            completed.add(receipt["cache_key"])
    unique = {}
    for plan in plans:
        if plan["cache_key"] not in completed:
            unique.setdefault(plan["cache_key"], plan)
    pending = list(unique.values())
    return {"checks": pending[:budget], "deferred_checks": pending[budget:], "observations": observations,
            "budget": budget, "external_check": allow_external}

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
import math
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
    "requirement_not_met": "不满足明确要求",
    "insufficient_evidence": "证据不足待核验",
    "output_limit": "超过本次展示上限",
}
FORMAL_PROVENANCE = {"publisher_record", "doi_registration", "official_proceedings", "institutional_publication_record"}
# Unit families are deliberately distinct: bits, symbols and samples are not interchangeable.
UNITS = {
    "S/s": ("sample_rate", 1), "kS/s": ("sample_rate", 1e3), "MS/s": ("sample_rate", 1e6), "GS/s": ("sample_rate", 1e9),
    "b/s": ("bit_rate", 1), "Mb/s": ("bit_rate", 1e6), "Gb/s": ("bit_rate", 1e9), "Tb/s": ("bit_rate", 1e12),
    "Bd": ("symbol_rate", 1), "MBd": ("symbol_rate", 1e6), "GBd": ("symbol_rate", 1e9),
    "Hz": ("frequency", 1), "kHz": ("frequency", 1e3), "MHz": ("frequency", 1e6), "GHz": ("frequency", 1e9),
    "K": ("temperature", 1), "mK": ("temperature", 1e-3),
}


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
        "requirements": [],
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
    requirements = resolved["requirements"]
    if (not isinstance(requirements, list) or any(
        not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip()
        or item.get("unit") not in {None, *UNITS}
        or any(key in item and (not _numeric(item[key]) or not item.get("unit")) for key in ("min", "max"))
        or ("min" in item and "max" in item and item["min"] > item["max"])
        for item in requirements
    ) or len({item["id"] for item in requirements}) != len(requirements)):
        raise ValueError("requirements need unique IDs and valid typed bounds")
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


def _numeric(value):
    return type(value) in {int, float} and math.isfinite(value)


def assess_requirements(requirements, observations):
    results = []
    for req in requirements:
        facts = observations.get(req["id"]) or []
        if isinstance(facts, dict):
            facts = [facts]
        applicable = []
        verdicts = []
        for fact in facts:
            if not isinstance(fact, dict):
                continue
            sources = _evidence(fact.get("evidence"))
            if not sources or any(req.get(key) and fact.get(key) != req[key]
                                  for key in ("metric", "subject", "basis")):
                continue
            value = fact.get("value")
            if req.get("unit"):
                expected = UNITS[req["unit"]]
                observed = UNITS.get(fact.get("unit"))
                if not observed or observed[0] != expected[0] or not _numeric(value):
                    continue
                value = value * observed[1] / expected[1]
            elif value is None:
                continue
            applicable.append({**fact, "evidence": sources})
            verdicts.append(("min" not in req or value >= req["min"])
                            and ("max" not in req or value <= req["max"])
                            and ("equals" not in req or value == req["equals"]))
        results.append({"id": req["id"], "status": "met" if any(verdicts) else "not_met" if verdicts else "unknown",
                        "observations": applicable})
    return results


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
    requirements = assess_requirements(policy.get("requirements") or [], evidence.get("observations") or {})
    if policy.get("library_only") and paper.get("origin") == "external_primary_source" and paper.get("source_in_current_corpus") is not True:
        reasons.append("source_priority")
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
    if any(item["status"] == "not_met" for item in requirements):
        reasons.append("requirement_not_met")
    if any(item["status"] == "unknown" for item in requirements):
        reasons.append("insufficient_evidence")
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
        "relevance": evidence.get("relevance") or "unknown",
        "requirements": requirements,
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
    evaluated = [(paper, evaluate_candidate(paper, policy)) for paper in papers]
    if policy.get("rank_by_evidence") or policy["requirements"]:
        def evidence_rank(item):
            paper, decision = item
            supported = bool(decision["evidence"] or any(row["observations"] for row in decision["requirements"]))
            venue = paper.get("venue_quality")
            return (supported and decision["relevance"] == "strong",
                    supported and decision["contribution"] == "target_circuit",
                    supported and decision["measured_target"] is True,
                    venue if _numeric(venue) else 0)

        evaluated.sort(key=evidence_rank, reverse=True)
    for paper, decision in evaluated:
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
        allow_external=policy["external_check"], remaining_api_budget=policy.get("remaining_api_budget"),
        remaining_external_budget=policy.get("remaining_external_budget"),
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


EXTERNAL_ACTIONS = {"check_formal_source", "check_branch", "follow_reference_lead"}


def record_gap_check(payload, check, outcome, *, evidence=(), query_ids=(), discoveries=()):
    """Save a repeatable check receipt, including failures and index visibility."""
    allowed = {"completed_found", "completed_no_match", "index_no_records", "source_unavailable",
               "failed", "pending", "truncated", "budget_exhausted", "not_run"}
    if outcome not in allowed:
        raise ValueError("Unknown gap-check outcome: " + str(outcome))
    result = deepcopy(payload)
    receipts = result.setdefault("gap_checks", {})
    channel = check.get("channel") or ("external" if check["action"] in EXTERNAL_ACTIONS else "index")
    # Accept older saved index observations without overwriting a completed external check.
    key = check["cache_key"] + ":index" if outcome == "index_no_records" and channel == "external" else check["cache_key"]
    if key != check["cache_key"]:
        channel = "index"
    attempts = receipts.get(key, {}).get("attempts", 0)
    if outcome not in {"budget_exhausted", "pending", "not_run"}:
        attempts += 1
    receipts[key] = {"cache_key": key, "action": check["action"], "channel": channel,
                                    "status": outcome, "evidence": _evidence(evidence),
                                    "query_ids": list(query_ids), "trigger": check.get("reason", ""),
                                    "discoveries": deepcopy(list(discoveries)), "attempts": attempts}
    return result


def plan_gap_checks(branches=(), queries=(), source_checks=(), *, reference_leads=(),
                    max_checks=12, remaining_budget=None, completed_checks=(), allow_external=True,
                    remaining_api_budget=None, remaining_external_budget=None):
    """Plan bounded agent-side checks, reusing completed cache keys.

    Source checks must distinguish a formal publisher import from a preprint
    carrying a final DOI. Unsaturated searches describe only the visible index.
    Every plan item includes its trigger and evidence link supplied by the agent.
    The planner performs no network requests and no server-side model work.
    """
    if any(not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
           for limit in (max_checks, remaining_budget, remaining_api_budget, remaining_external_budget) if limit is not None):
        raise ValueError("gap-check budgets cannot be negative")
    budget = min(max_checks, remaining_budget) if remaining_budget is not None else max_checks
    plans = []
    observations = []
    by_query = {qid: branch for branch in branches for qid in branch.get("query_ids", [])}
    for query in queries:
        state = query_observation(query)
        observations.append({"query_id": query.get("query_id"), "state": state,
                             "coverage_scope": "visible_index_only", "result_count": query.get("result_count")})
        if state in {"failed", "pending"}:
            plans.append({"cache_key": "resume:" + str(query.get("query_id")), "action": "resume_query",
                          "reason": state, "query_id": query.get("query_id"), "api_cost": 0,
                          "client_request_id": query.get("client_request_id")})
        if state == "truncated":
            branch = by_query.get(query.get("query_id"), {})
            if branch.get("qualified_gain") == 0 or branch.get("resolved") is True:
                continue
            years = query.get("years") or []
            split = branch.get("partition_by") or ("years" if len(years) == 2 and years[0] < years[1] else "venue_or_technical_branch")
            plans.append({"cache_key": "split:" + str(query.get("query_id")), "action": "partition_query",
                          "reason": "truncated", "query_id": query.get("query_id"), "partition_by": split,
                          "priority": branch.get("priority", 1), "api_cost": branch.get("partition_cost", 1)})
    observed_query_ids = {entry["query_id"] for entry in observations if entry["state"] != "not_run"}
    branch_observations = []
    for branch in branches:
        branch_observations.append({
            "branch_id": branch["branch_id"], "priority": branch.get("priority", 1),
            "status": ("resolved" if branch.get("resolved") else
                       "tested" if observed_query_ids.intersection(branch.get("query_ids") or []) else "not_run"),
            "qualified_count": branch.get("qualified_count"), "qualified_gain": branch.get("qualified_gain"),
        })
    for source in source_checks:
        if source.get("strong_topic_match") and not source.get("formal_records_seen"):
            plans.append({"cache_key": "formal-source:" + str(source["source_id"]),
                          "action": "check_formal_source", "reason": "specialist_formal_source_missing",
                          "source_id": source["source_id"], "evidence": source.get("evidence", []),
                          "priority": source.get("priority", 4), "preprint_doi_is_coverage_evidence": False})
    for branch in branches:
        if not branch.get("resolved") and branch.get("qualified_count", branch.get("candidate_count", 0)) <= branch.get("sparse_threshold", 2):
            plans.append({"cache_key": "branch:" + str(branch["branch_id"]), "action": "check_branch",
                          "reason": "sparse_branch", "branch_id": branch["branch_id"], "priority": branch.get("priority", 2),
                          "terms": branch.get("terms", []), "sources": branch.get("sources", [])})
    for lead in reference_leads:
        plans.append({"cache_key": "lead:" + str(lead["lead_id"]), "action": "follow_reference_lead",
                      "reason": "benchmark_or_citation_or_author_lead", "lead_id": lead["lead_id"],
                      "evidence": lead.get("evidence", []), "terms": lead.get("terms", []),
                      "priority": lead.get("priority", 3), "max_hops": 1})
    history = {}
    receipts = completed_checks.values() if isinstance(completed_checks, dict) else completed_checks
    for receipt in receipts:
        if isinstance(receipt, dict):
            history[receipt["cache_key"]] = receipt
        else:
            history[receipt] = {"status": "completed_found"}
    unique = {}
    for plan in plans:
        external = plan["action"] in EXTERNAL_ACTIONS
        previous = history.get(plan["cache_key"], {})
        status = previous.get("status", "not_run")
        if status in {"completed_found", "completed_no_match"} or status == "index_no_records" and not external:
            continue
        cost = plan.get("api_cost", 0 if external else 1)
        if type(cost) is not int or cost < 0:
            raise ValueError("gap-check api_cost must be a nonnegative integer")
        unique.setdefault(plan["cache_key"], {**plan, "channel": "external" if external else "index",
                                               "api_cost": cost, "previous_status": status})
    pending = sorted(unique.values(), key=lambda plan: -plan.get("priority", 1))
    # Keep a slot for a good external lead, even if many capped queries score highly.
    if allow_external and budget and remaining_external_budget != 0:
        first_external = next((plan for plan in pending if plan["channel"] == "external"), None)
        if first_external and first_external not in pending[:budget]:
            pending.remove(first_external)
            pending.insert(budget - 1, first_external)
    selected, deferred, skipped = [], [], []
    api_left = remaining_api_budget
    external_left = remaining_external_budget
    for plan in pending:
        if not allow_external and plan["channel"] == "external":
            skipped.append({**plan, "status": "library_only"})
        elif (len(selected) >= budget or api_left is not None and plan["api_cost"] > api_left
              or plan["channel"] == "external" and external_left == 0):
            deferred.append({**plan, "status": "budget_exhausted"})
        else:
            selected.append(plan)
            if api_left is not None:
                api_left -= plan["api_cost"]
            if plan["channel"] == "external" and external_left is not None:
                external_left -= 1
    return {"checks": selected, "deferred_checks": deferred, "skipped_checks": skipped, "observations": observations,
            "branch_observations": branch_observations,
            "budget": budget, "remaining_api_budget": remaining_api_budget,
            "remaining_external_budget": remaining_external_budget, "external_check": allow_external}

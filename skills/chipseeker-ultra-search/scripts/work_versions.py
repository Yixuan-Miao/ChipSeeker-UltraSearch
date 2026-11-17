"""Evidence-gated publication relationships, independent of retrieval query groups.

The calling researcher supplies reviewed source evidence. Similar titles, authors,
technologies and citations can propose leads, but never confirm a version group.
Publication records stay intact, including per-version measurements and venues.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from copy import deepcopy
from urllib.parse import unquote, urlsplit


VERSION_RELATIONS = {"journal_extension", "conference_version", "shared_design"}
VERSION_EVIDENCE = {
    "publisher_version_statement", "author_version_statement", "explicit_design_reuse"
}
FORMAL_TYPES = {"journal-article", "proceedings-article", "conference-paper"}
PUBLICATION_EVIDENCE = {"publisher_record", "doi_registration", "official_proceedings"}


def normalize_doi(value):
    value = unquote(str(value or "").strip()).lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip(" .")


def publication_key(paper):
    doi = normalize_doi(paper.get("doi"))
    if doi:
        return "doi:" + doi
    title = re.sub(r"\W+", " ", str(paper.get("title", "")).casefold()).strip()
    return "title_year:" + title + "|" + str(paper.get("year", ""))


def _web_url(value):
    try:
        parts = urlsplit(str(value or ""))
        return parts.scheme in {"https", "http"} and bool(parts.hostname)
    except ValueError:
        return False


def _publication_metadata(paper):
    reviewed = paper.get("review_evidence") or {}
    kind = paper.get("publication_status") or paper.get("publication_type") or reviewed.get("publication_type")
    evidence = paper.get("publication_evidence") or reviewed.get("publication_evidence") or []
    return kind, evidence


def canonicalize_publication(paper):
    """Fill missing formal-metadata fields from saved reviewed provenance.

    The explicit publication record takes precedence. Evidence validation is
    still required before metadata can establish a formal version relationship.
    """
    item = deepcopy(paper)
    kind, evidence = _publication_metadata(item)
    if kind and not item.get("publication_status"):
        item["publication_status"] = kind
    if evidence and not item.get("publication_evidence"):
        item["publication_evidence"] = deepcopy(evidence)
    return item


def is_formal_publication(paper):
    kind, evidence_rows = _publication_metadata(paper)
    return kind in FORMAL_TYPES and any(
        evidence.get("kind") in PUBLICATION_EVIDENCE and _web_url(evidence.get("url"))
        for evidence in evidence_rows
        if isinstance(evidence, dict)
    )


def _relation_problem(relation, papers=None):
    left = normalize_doi(relation.get("from_doi"))
    right = normalize_doi(relation.get("to_doi"))
    if not left or not right or left == right:
        return "distinct_publication_identifiers_required"
    if relation.get("relation_type") not in VERSION_RELATIONS:
        return "related_only"
    if relation.get("status") != "confirmed":
        return "unconfirmed"
    evidence = relation.get("evidence", [])
    if not any(
        isinstance(item, dict)
        and item.get("kind") in VERSION_EVIDENCE
        and _web_url(item.get("url"))
        and str(item.get("location", "")).strip()
        and str(item.get("note") or item.get("excerpt") or "").strip()
        for item in evidence
    ):
        return "explicit_version_evidence_required"
    if papers is not None:
        by_doi = {normalize_doi(paper.get("doi")): paper for paper in papers}
        if left not in by_doi or right not in by_doi:
            return "publication_record_unavailable"
        if not all(is_formal_publication(by_doi[doi]) for doi in (left, right)):
            return "formal_publication_evidence_required"
    return ""


def confirmed_relations(relations, papers=None):
    """Return reviewed relations with explicit source locations and strong evidence.

    With a catalog, also require both formal records. Without a catalog the graph
    can resolve a saved manual exclusion even when one old version is no longer in
    the current candidates. The result alone does not prove corpus coverage.
    """
    accepted = []
    seen = set()
    for relation in relations or []:
        if _relation_problem(relation, papers):
            continue
        item = deepcopy(relation)
        item["from_doi"] = normalize_doi(item["from_doi"])
        item["to_doi"] = normalize_doi(item["to_doi"])
        key = (frozenset((item["from_doi"], item["to_doi"])), item["relation_type"])
        if key not in seen:
            accepted.append(item)
            seen.add(key)
    return accepted


def _components(relations):
    adjacent = {}
    for relation in relations:
        left, right = relation["from_doi"], relation["to_doi"]
        adjacent.setdefault(left, set()).add(right)
        adjacent.setdefault(right, set()).add(left)
    components = {}
    for doi in sorted(adjacent):
        if doi in components:
            continue
        members, pending = set(), [doi]
        while pending:
            member = pending.pop()
            if member in members:
                continue
            members.add(member)
            pending.extend(adjacent[member] - members)
        identity = "doi:" + min(members)
        work_id = "work:" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        components.update({member: work_id for member in members})
    return components


def assign_work_ids(papers, relations):
    """Annotate copied records for policy/exclusion persistence before grouping."""
    components = _components(confirmed_relations(relations))
    annotated = []
    for paper in papers or []:
        item = deepcopy(paper)
        doi = normalize_doi(item.get("doi"))
        identity = publication_key(item)
        item["work_id"] = components.get(
            doi, "work:" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        )
        item["work_id_confirmed"] = doi in components
        annotated.append(item)
    return annotated


def deduplicate_publications(papers):
    """Unify a DOI's provenance, preferring formal metadata over preprint metadata.

    Different DOIs are always independent publications. An unidentified record
    stays independent because title similarity alone cannot establish identity.
    """
    result, positions = [], {}
    for paper in papers or []:
        item = canonicalize_publication(paper)
        doi = normalize_doi(item.get("doi"))
        if not doi:
            result.append(item)
            continue
        source_fields = (
            "doi", "title", "year", "online_year", "venue", "venue_name", "origin",
            "publication_status", "primary_source", "source_url", "pdf_link", "open_pdf_link",
            "source_links", "publication_evidence",
            "source_in_current_corpus",
        )
        source = {key: deepcopy(item[key]) for key in source_fields if key in item}
        sources = list(item.get("publication_sources") or [])
        sources.append(source)
        if doi in positions:
            position = positions[doi]
            old = result[position]
            sources = list(old.get("publication_sources") or []) + sources
            item_formal, old_formal = is_formal_publication(item), is_formal_publication(old)
            primary, secondary = (item, old) if item_formal and not old_formal else (old, item)
            item = deepcopy(primary)
            if item_formal == old_formal:
                for key, value in secondary.items():
                    if key not in item or item[key] in (None, "", []):
                        item[key] = deepcopy(value)
                if secondary.get("source_in_current_corpus") is True:
                    item["source_in_current_corpus"] = True
            if secondary.get("manual_excluded"):
                item["manual_excluded"] = True
                if not item.get("exclusion_reason") and secondary.get("exclusion_reason"):
                    item["exclusion_reason"] = secondary["exclusion_reason"]
            result[position] = item
        else:
            positions[doi] = len(result)
            result.append(item)
        unique = {}
        for source in sources:
            unique[json.dumps(source, sort_keys=True, ensure_ascii=False)] = source
        item["publication_sources"] = list(unique.values())
    return result


def expand_verified_versions(seeds, catalog, relations, max_publications=20):
    """Follow a finite reviewed evidence graph from either publication direction.

    This is the replay/merge stage after external evidence discovery. It performs
    no networking and makes no inference about an unavailable paper or source.
    """
    if max_publications < len(seeds):
        raise ValueError("max_publications must accommodate all seed publications")
    records = {publication_key(paper): paper for paper in deduplicate_publications(list(catalog or []) + list(seeds or []))}
    valid = confirmed_relations(relations, list(records.values()))
    selected = {publication_key(paper) for paper in seeds or []}
    unresolved = []
    for relation in relations or []:
        reason = _relation_problem(relation, list(records.values()))
        if reason:
            unresolved.append({**deepcopy(relation), "reason": reason})
    changed = True
    while changed:
        changed = False
        for relation in valid:
            left, right = "doi:" + relation["from_doi"], "doi:" + relation["to_doi"]
            if (left in selected) == (right in selected):
                continue
            other = right if left in selected else left
            if len(selected) >= max_publications:
                continue
            selected.add(other)
            changed = True
    limited = [
        relation for relation in valid
        if ("doi:" + relation["from_doi"] in selected)
        != ("doi:" + relation["to_doi"] in selected)
    ]
    return {
        "papers": assign_work_ids([records[key] for key in sorted(selected)], valid),
        "relations": valid,
        "unresolved": unresolved,
        "budget_limited_relations": limited,
    }


def group_work_versions(papers, relations):
    """Show one journal-first row per confirmed work, retaining each publication."""
    papers = deduplicate_publications(papers)
    valid = confirmed_relations(relations, papers)
    groups = {}
    for index, paper in enumerate(assign_work_ids(papers, valid)):
        group_key = paper["work_id"] if paper["work_id_confirmed"] else "single:" + str(index)
        groups.setdefault(group_key, []).append(paper)
    rows = []
    for members in groups.values():
        members.sort(key=lambda paper: (
            paper.get("publication_status") != "journal-article",
            str(paper.get("year", "")), publication_key(paper)
        ))
        primary, *versions = members
        primary["versions"] = versions
        member_dois = {normalize_doi(paper.get("doi")) for paper in members}
        primary["version_relations"] = [
            relation for relation in valid
            if {relation["from_doi"], relation["to_doi"]} <= member_dois
        ]
        rows.append(primary)
    return {
        "papers": rows,
        "relations": valid,
        "relation_audit": [
            {**deepcopy(relation), "reason": _relation_problem(relation, papers) or "confirmed"}
            for relation in relations or []
        ],
    }


def reference_title_key(title):
    """Normalize bibliographic typography and a common US/UK spelling variant."""
    title = html.unescape(re.sub(r"<[^>]+>", " ", str(title or "")))
    words = re.sub(r"\W+", " ", title.casefold()).split()
    return " ".join("gray" if word == "grey" else word for word in words)


def discover_reference_gaps(seeds, sources, catalog, max_references=20):
    """Replay bounded citation/benchmark discovery without a supplied target DOI.

    Source extraction is performed by the user's AI from a read public document.
    Each reference retains its location. Unknown titles become exact-title lookup
    leads, never invented records or automatic evidence of a missing library item.
    """
    seed_dois = {normalize_doi(paper.get("doi")) for paper in seeds or []}
    by_title = {}
    for paper in catalog or []:
        by_title.setdefault(reference_title_key(paper.get("title")), []).append(paper)
    papers, leads, seen = {}, [], set()
    inspected = 0
    for source in sources or []:
        linked = {normalize_doi(doi) for doi in source.get("seed_publication_dois", [])}
        if not seed_dois.intersection(linked) or not _web_url(source.get("url")):
            continue
        for reference in source.get("references", []):
            title = str(reference.get("title", "")).strip()
            key = reference_title_key(title)
            if not key or key in seen:
                continue
            seen.add(key)
            if inspected >= max_references:
                leads.append({"title": title, "status": "budget_deferred", "source": source["url"]})
                continue
            inspected += 1
            evidence = {
                "url": reference.get("url") or source["url"],
                "location": reference.get("location", ""),
                "note": reference.get("note", ""),
            }
            matches = [paper for paper in by_title.get(key, []) if is_formal_publication(paper)]
            status = "formal_record_found" if len(matches) == 1 else "title_lookup_required"
            leads.append({"title": title, "status": status, "evidence": evidence})
            if len(matches) == 1:
                item = deepcopy(matches[0])
                item.setdefault("gap_discovery_evidence", []).append(evidence)
                papers[publication_key(item)] = item
    return {"papers": list(papers.values()), "leads": leads, "inspected_references": inspected}

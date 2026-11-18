#!/usr/bin/env python3
"""Create tidy research workspaces and render curated paper data offline."""

import argparse
import base64
import html
import json
import re
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit


DATA_DIR = "检索数据"
MD_NAME = "结果报告.md"
HTML_NAME = "结果报告.html"
ASSETS = Path(__file__).resolve().parents[1] / "assets"
SEARCH_GOAL_LABELS = {
    "coverage": "尽可能找全",
    "precision": "精准精选",
    "balanced": "兼顾覆盖与精准",
    "representative": "代表性速查",
}
# Support loading the standalone renderer through importlib as well as the CLI.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def topic_name(value):
    value = unicodedata.normalize("NFKC", str(value or "")).strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    value = value.removeprefix("芯寻_").strip(" ._")[:64].rstrip(" ._")
    if not value:
        raise ValueError("A non-empty, concise topic name is required.")
    return "芯寻_" + value


def init_workspace(parent, name, *, resume=False):
    parent = Path(parent).expanduser().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    stem = topic_name(name)
    workspace = parent / stem
    if workspace.exists() and not resume:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace = parent / f"{stem}_{stamp}"
        if workspace.exists():
            workspace = parent / f"{stem}_{stamp}_{uuid.uuid4().hex[:6]}"
    if workspace.is_symlink() or workspace.resolve().parent != parent:
        raise ValueError("The topic folder must be a direct child of the requested parent.")
    workspace.mkdir(exist_ok=resume)
    (workspace / DATA_DIR).mkdir(exist_ok=True)
    return workspace


def safe_url(value):
    value = str(value or "").strip()
    if not value or any(ord(char) < 32 for char in value) or any(char.isspace() for char in value):
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    return quote(value, safe=":/?#[]@!$&'()*+,;=%~_-")


def external_links(paper):
    links = []
    source = safe_url(paper.get("primary_source") or paper.get("source"))
    doi = safe_url(paper.get("doi_link"))
    if not doi:
        identity = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", str(paper.get("doi", "")), flags=re.I).strip()
        if re.fullmatch(r"10\.\d{4,9}/\S+", identity):
            doi = "https://doi.org/" + quote(identity, safe="/():._-")
    pdf = safe_url(paper.get("open_pdf_link") or paper.get("pdf_link") or paper.get("pdf"))
    doi_label = "DOI " + str(paper.get("doi") or "").removeprefix("https://doi.org/")
    if source:
        links.append((doi_label.strip() if source == doi else "来源", source))
    if doi and doi != source:
        links.append((doi_label.strip(), doi))
    if pdf:
        links.append(("PDF", pdf))
    return links


def local_pdf(paper, workspace):
    raw = paper.get("local_pdf_path")
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = workspace / path
    path = path.resolve()
    return path if path.suffix.lower() == ".pdf" and path.is_file() else None


def text(value):
    return str(value if value is not None else "")


def esc(value):
    return html.escape(text(value), quote=True)


def md(value):
    value = re.sub(r"\s+", " ", text(value)).strip()
    return re.sub(r"([\\`*_{}\[\]<>|])", r"\\\1", value)


def authors_text(value):
    if isinstance(value, list):
        return "; ".join(str(author) for author in value if author)
    return text(value)


def stars(value):
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return 0


def source_label(paper):
    if paper.get("origin") == "external_primary_source" or paper.get("source_in_current_corpus") is False:
        return "库外核实"
    if paper.get("source_in_current_corpus") is True or paper.get("origin") == "chipseeker_remote_api":
        return "芯寻论文库"
    return "来源未标注"


def knowledge_label(paper):
    if paper.get("full_text_read") is True:
        return "已核对全文"
    if paper.get("abstract") and paper.get("abstract_kind") == "source_abstract":
        return "题目与原始摘要"
    if paper.get("abstract"):
        return "题目与摘要；摘要来源未确认"
    evidence = paper.get("review_evidence") or {}
    if evidence.get("evidence"):
        if evidence.get("readability") == "full_text":
            return "已核对全文指定位置"
        if evidence.get("readability") == "source_abstract":
            return "已核对来源摘要"
    return "仅题目/元数据；摘要缺失"


def discovery_trace(paper):
    ids = list(dict.fromkeys(str(hit["query_id"]) for hit in paper.get("retrievals") or []
                             if hit.get("query_id") and hit.get("purpose") != "metadata"))
    ids.extend(str(key) for key in paper.get("gap_receipt_keys") or [])
    return " · ".join(ids[:4]) + (f" · 另 {len(ids) - 4} 项见检索数据" if len(ids) > 4 else "")


def normalize_report(payload, workspace):
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        raise ValueError("papers.json must be an object containing a curated papers list.")
    from work_versions import (assign_work_ids, confirmed_relations, deduplicate_publications,
                               group_work_versions, normalize_doi, publication_key)
    from review_policy import apply_review_policy

    payload = dict(payload)
    relations = payload.get("version_relations") or []
    payload["papers"] = assign_work_ids(payload["papers"], confirmed_relations(relations, payload["papers"]))
    all_publications = payload["papers"]
    if ("review_policy" not in payload and payload.get("schema") != "ultrasearch-screened-papers/v2"
            and (payload.get("manual_exclusions") or any(p.get("manual_excluded") for p in all_publications))):
        payload["review_policy"] = {"profile": "broad_discovery"}
    if "review_policy" in payload or payload.get("schema") == "ultrasearch-screened-papers/v2":
        all_publications = deduplicate_publications(all_publications)
        payload["papers"] = all_publications
        payload = apply_review_policy(payload)
    eligible_records = {publication_key(p): p for p in payload["papers"]}
    eligible = set(eligible_records)
    positions = {key: index for index, key in enumerate(eligible_records)}
    decisions = {tuple(d.get("publication_ids") or []): d for d in payload.get("screening_audit") or []}
    grouped = group_work_versions(all_publications, relations)
    ranked = []
    for work in grouped["papers"]:
        members = [work, *(work.get("versions") or [])]
        main = [eligible_records[publication_key(p)] for p in members if publication_key(p) in eligible]
        if not main:
            continue
        primary = dict(main[0])
        primary["versions"] = []
        for member in members:
            if publication_key(member) == publication_key(primary):
                continue
            decision = next((d for ids, d in decisions.items() if publication_key(member) in ids), {})
            if decision.get("decision") == "manual_exclusion":
                continue
            version = {k: v for k, v in member.items() if k != "versions"}
            if publication_key(member) not in eligible:
                reasons = set(decision.get("reasons") or [])
                if (not (payload.get("review_policy") or {}).get("attach_out_of_scope_versions")
                        or not reasons or not reasons <= {"outside_window", "source_priority"}):
                    continue
                version["scope_note_zh"] = "附属版本，主表准入决定为" + str(decision.get("reason_zh") or "未纳入本次主表")
            primary["versions"].append(version)
        visible_dois = {normalize_doi(p.get("doi")) for p in [primary, *primary["versions"]]}
        primary["version_relations"] = [relation for relation in work.get("version_relations") or []
                                        if {relation["from_doi"], relation["to_doi"]} <= visible_dois]
        ranked.append((min(positions[publication_key(p)] for p in main), primary))
    retained = [row for _, row in sorted(ranked, key=lambda item: item[0])]
    if "review_policy" not in payload and not relations:
        retained = all_publications
    grouped["papers"] = retained
    payload["papers"] = grouped["papers"]
    summary = payload.get("summary") or {}
    if not isinstance(summary, dict):
        raise ValueError("summary must be an object.")
    rows = []
    for index, item in enumerate(payload["papers"], 1):
        if not isinstance(item, dict) or not text(item.get("title")).strip():
            raise ValueError(f"Paper {index} needs a non-empty title.")
        row = dict(item)
        row["_id"] = f"P{index:02}"
        row["_stars"] = stars(item.get("importance_stars"))
        row["_authors"] = authors_text(item.get("authors"))
        row["_venue"] = text(item.get("venue_name") or item.get("venue") or "未标注")
        row["_origin"] = source_label(item)
        row["_evidence"] = knowledge_label(item)
        row["_links"] = external_links(item)
        row["_local_pdf"] = local_pdf(item, workspace)
        row["_versions"] = []
        for version in item.get("versions") or []:
            version = dict(version)
            version["_venue"] = text(version.get("venue_name") or version.get("venue") or "未标注")
            version["_links"] = external_links(version)
            version["_local_pdf"] = local_pdf(version, workspace)
            row["_versions"].append(version)
        rows.append(row)
    def count(value):
        return len(value) if isinstance(value, list) else int(value or 0)

    unresolved = count(summary.get("unresolved_query_ids"))
    pending = count(summary.get("pending_queries"))
    failures = count(summary["failed_queries"]) + pending if "failed_queries" in summary else max(unresolved, pending)
    gap_plan = payload.get("gap_check_plan") or {}
    remaining_gaps = (gap_plan.get("checks") or []) + (gap_plan.get("deferred_checks") or [])
    status = str(summary.get("status", ""))
    partial = bool(failures or unresolved or remaining_gaps
                   or status in {"running", "queued", "pending"} or status.startswith("partial"))
    start, finish = summary.get("quota_at_start") or {}, summary.get("quota_at_finish") or {}
    cost = None
    if "credits_used" in start and "credits_used" in finish:
        cost = int(finish["credits_used"]) - int(start["credits_used"])
        if cost < 0:
            cost = None
    return {
        "title": text(payload.get("title") or summary.get("research_direction") or workspace.name.removeprefix("芯寻_")),
        "summary": summary,
        "date": text(payload.get("generated_at", ""))[:10] or "日期未标注",
        "papers": rows,
        "supplements": payload.get("supplementary_reading") or [],
        "failures": failures,
        "partial": partial,
        "cost": cost,
        "balance": finish.get("credits_left"),
        "report_summary": text(payload.get("report_summary_zh") or summary.get("report_summary_zh")),
        "report_notes": [*(payload.get("report_notes_zh") or []), *([f"仍有 {len(remaining_gaps)} 项有目标的缺口检查待完成，具体任务与回执见结构化数据。"] if remaining_gaps else [])],
        "audit": payload.get("screening_audit") or [],
        "review_policy": payload.get("review_policy"),
        "manual_exclusions": payload.get("manual_exclusions") or [],
        "relations": grouped["relations"],
        "relation_audit": grouped.get("relation_audit") or [],
        "gap_checks": payload.get("gap_checks") or {},
        "gap_check_plan": gap_plan,
        "research_branches": payload.get("research_branches") or [],
        "source_checks": payload.get("source_checks") or [],
        "reference_leads": payload.get("reference_leads") or [],
        "searches": payload.get("searches") or [],
    }


def version_markdown(paper):
    lines = []
    for version in paper["_versions"]:
        scope = version.get("scope_note_zh") or version.get("evidence_limit_zh") or ""
        lines.append(f"**关联正式版本** · {md(version.get('year'))} · {md(version['_venue'])} · {md(version['title'])} {markdown_links(version)} {md(scope)}")
        if version.get("measurement_note_zh"):
            lines.append(md(version["measurement_note_zh"]))
    for relation in paper.get("version_relations") or []:
        for evidence in relation.get("evidence") or []:
            url = safe_url(evidence.get("url"))
            if url:
                lines.append(f"已核实版本关联 · {md(relation.get('relation_type'))} · [{md(evidence.get('location'))}](<{url}>) · {md(evidence.get('note'))}")
    return lines


def version_html(paper):
    parts = []
    for version in paper["_versions"]:
        parts.append(f'<div class="version"><strong>关联正式版本</strong><p>{esc(version.get("year"))} · {esc(version["_venue"])}</p><p>{esc(version["title"])}</p><nav class="paper-links">{html_links(version)}</nav><p class="muted">{esc(version.get("scope_note_zh") or version.get("evidence_limit_zh"))}</p></div>')
        if version.get("measurement_note_zh"):
            parts.append(f'<p class="muted">{esc(version["measurement_note_zh"])}</p>')
    for relation in paper.get("version_relations") or []:
        for evidence in relation.get("evidence") or []:
            url = safe_url(evidence.get("url"))
            if url:
                parts.append(f'<p class="muted">已核实版本关联 · {esc(relation.get("relation_type"))} · <a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{esc(evidence.get("location"))}</a> · {esc(evidence.get("note"))}</p>')
    return ''.join(parts)


def stat_items(model):
    summary = model["summary"]
    return [
        (len(model["papers"]), "研究工作" if model["review_policy"] else "保留论文"),
        (summary.get("unique_papers", "未记录"), "去重候选"),
        (summary.get("successful_queries", "未记录"), "成功查询"),
        (model["failures"], "未完成查询"),
    ]


def status_text(model):
    if model["partial"]:
        return f"本轮仍有{model['failures']}项查询未完成，结果为部分检索记录，不代表已搜全。" if model["failures"] else "本轮检索尚未完成，不代表已搜全。"
    return "本轮检索已完成；不承诺穷尽所有文献。"


def search_goal_label(model):
    goal = model["summary"].get("search_goal")
    return SEARCH_GOAL_LABELS.get(goal, "") if isinstance(goal, str) else ""


def markdown_links(paper):
    links = [f"[{label}](<{url}>)" for label, url in paper["_links"]]
    if paper["_local_pdf"]:
        links.append(f"[本地PDF](<{paper['_local_pdf'].as_posix()}>)")
    return " · ".join(links) or "未确认有效链接"


def render_markdown(model):
    lines = [f"# 结果报告：{md(model['title'])}", "", f"检索日期：{md(model['date'])} · 芯寻 UltraSearch", "", f"> {status_text(model)}", ""]
    if goal := search_goal_label(model):
        lines.extend([f"检索目标：{goal}", ""])
    lines.append("；".join(f"{label}：{value}" for value, label in stat_items(model)) + "。")
    if model["cost"] is not None:
        lines.extend(["", f"检索期间账户已用增加：{model['cost']}次" + (f"；结束时余额：{model['balance']}次。" if model["balance"] is not None else "。")])
    if model["report_summary"]:
        lines.extend(["", model["report_summary"]])
    lines.extend(["", "## 论文导航", "", "星级表示本方向的阅读优先级。相关性解释为AI判断；原始摘要与核实范围另行标注。", "", "|编号|重要性|年份 / Venue|论文题目|为什么保留|链接|", "|---|---|---|---|---|---|"])
    for p in model["papers"]:
        rating = "★" * p["_stars"] if p["_stars"] else "未评级"
        reason = text(p.get("why_keep_zh") or "未提供保留理由")
        match = text(p.get("match_description_zh"))
        lines.append(f"|[{p['_id']}](#{p['_id'].lower()})|{rating}|{md(p.get('year'))} / {md(p['_venue'])}|{md(p['title'])}|{md(match + '。' if match else '')}{md(reason)}|{markdown_links(p)}|")
        for version in p["_versions"]:
            lines.append(f"|↳||||{md(version.get('year'))} · {md(version['_venue'])} · {md(version['title'])}|{markdown_links(version)}|")
    lines.extend(["", "## 逐篇信息", ""])
    for p in model["papers"]:
        lines.extend([f'<a id="{p["_id"].lower()}"></a>', f"### {p['_id']} · {md(p['title'])}", "", f"作者：{md(p['_authors'] or '未提供')}。", "", f"{md(p.get('year'))} · {md(p['_venue'])} · {p['_origin']} · {p['_evidence']}", "", f"**为什么保留：** {md(p.get('why_keep_zh') or '未提供')}", ""])
        if p.get("evidence_limit_zh"):
            lines.extend([f"**证据边界：** {md(p['evidence_limit_zh'])}", ""])
        if trace := discovery_trace(p):
            lines.extend([f"**发现路径：** {md(trace)}", ""])
        lines.extend([markdown_links(p), ""])
        for field in ("version_additions_zh", "measurement_note_zh"):
            if p.get(field):
                lines.extend([md(p[field]), ""])
        for version in version_markdown(p):
            lines.extend([version, ""])
        if p.get("abstract"):
            label = "原始摘要" if p.get("abstract_kind") == "source_abstract" else "摘要（来源未确认）"
            if p.get("abstract_truncated"):
                label += "；已截断"
            lines.extend([f"<details><summary>{label}</summary>", "", md(p["abstract"]), "", "</details>", ""])
    if model["supplements"]:
        lines.extend(["## 补充阅读", ""])
        for p in model["supplements"]:
            links = " · ".join(f"[{label}](<{url}>)" for label, url in external_links(p))
            lines.extend([f"- **{md(p.get('title'))}**，{md(p.get('year'))}，{md(authors_text(p.get('authors')))}。{md(p.get('why_keep_zh'))} {links}", ""])
    if model["audit"]:
        lines.extend(["## 筛选审计", "", "|论文|决定|证据与边界|", "|---|---|---|"])
        for decision in model["audit"]:
            lines.append(f"|{md(decision.get('title'))}|{md(decision.get('label') or decision.get('decision'))}|{md(decision.get('reason') or decision.get('reason_zh'))}|")
        lines.append("")
    lines.extend(["## 检索范围与限制", ""])
    if model["summary"].get("stopping_reason_zh"):
        lines.extend([md(model["summary"]["stopping_reason_zh"]), ""])
    for note in model["report_notes"]:
        lines.extend([f"- {md(note)}", ""])
    unresolved = model["summary"].get("unresolved_query_ids") or []
    if unresolved:
        lines.extend(["未完成查询：" + "、".join(f"`{md(value)}`" for value in unresolved) + "。", ""])
    lines.extend(["MD与HTML使用同一份已筛选论文数据。PDF链接不保证机构访问权限；没有本地PDF的论文不显示本地入口。完整论文元数据与断点保存在[检索数据](<检索数据/>)中。", ""])
    return "\n".join(lines)


def html_links(paper):
    links = [f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>' for label, url in paper["_links"]]
    if paper["_local_pdf"]:
        links.append(f'<a href="{esc(paper["_local_pdf"].as_uri())}">本地PDF</a>')
    return "".join(links) or '<span class="muted">有效链接未确认</span>'


def render_html(model):
    articles = []
    for index, p in enumerate(model["papers"]):
        try:
            year = int(p.get("year", 0))
        except (TypeError, ValueError):
            year = 0
        relevance = p.get("best_lite_similarity")
        if relevance is None:
            relevance = (p.get("ranking") or {}).get("best_lite_similarity")
        try:
            relevance = float(relevance) if relevance is not None else -1
        except (TypeError, ValueError):
            relevance = -1
        search = " ".join(text(p.get(key)) for key in ("title", "why_keep_zh", "match_description_zh", "abstract", "doi")) + " " + p["_authors"] + " " + p["_venue"]
        search += " " + " ".join(" ".join(text(v.get(k)) for k in ("title", "doi", "year", "venue", "venue_name", "authors")) for v in p["_versions"])
        abstract = ""
        if p.get("abstract"):
            label = "原始摘要" if p.get("abstract_kind") == "source_abstract" else "摘要（来源未确认）"
            if p.get("abstract_truncated"):
                label += " · 已截断"
            abstract = f'<details class="abstract"><summary>{esc(label)}</summary><p lang="en">{esc(p["abstract"])}</p></details>'
        tier = f' · {esc(p["venue_tier"])}' if p.get("venue_tier") else ""
        rating = "★" * p["_stars"] if p["_stars"] else "未评级"
        caveat = f'<p class="evidence"><strong>证据边界</strong>{esc(p["evidence_limit_zh"])}</p>' if p.get("evidence_limit_zh") else ""
        articles.append(f'''<article class="paper" id="{p['_id'].lower()}" data-order="{index}" data-stars="{p['_stars']}" data-year="{year}" data-venue="{esc(p['_venue'])}" data-origin="{esc(p['_origin'])}" data-relevance="{relevance}" data-search="{esc(search)}">
<div class="paper-index">{p['_id']}</div><div class="paper-content">
<div class="paper-meta"><span>{esc(p.get('year'))} · {esc(p['_venue'])}{tier}</span><span class="stars" aria-label="阅读优先级 {p['_stars']} 星">{rating}</span><span class="origin">{esc(p['_origin'])}</span></div>
<h2>{esc(p['title'])}</h2>{version_html(p)}<p class="authors">{esc(p['_authors'] or '作者未提供')}</p>
<p class="match">{esc(p.get('match_description_zh'))}</p><p class="reason">{esc(p.get('why_keep_zh') or '保留理由未提供')}</p><p class="muted">{esc(p.get('version_additions_zh'))} {esc(p.get('measurement_note_zh'))}</p>
{f'<p class="muted">发现路径：{esc(discovery_trace(p))}</p>' if discovery_trace(p) else ''}
{caveat}<div class="paper-bottom"><nav class="paper-links" aria-label="{p['_id']}论文链接">{html_links(p)}</nav><span class="evidence-label">{esc(p['_evidence'])}</span></div>{abstract}</div></article>''')
    venues = sorted({p["_venue"] for p in model["papers"]})
    origins = sorted({p["_origin"] for p in model["papers"]})
    options = lambda values: "".join(f'<option value="{esc(value)}">{esc(value)}</option>' for value in values)
    supplementary = ""
    if model["supplements"]:
        items = []
        for p in model["supplements"]:
            links = " · ".join(f'<a href="{esc(url)}" target="_blank" rel="noopener noreferrer">{label}</a>' for label, url in external_links(p))
            items.append(f'<li><h3>{esc(p.get("title"))}</h3><p class="muted">{esc(p.get("year"))} · {esc(authors_text(p.get("authors")))}</p><p>{esc(p.get("why_keep_zh"))}</p><div>{links}</div></li>')
        supplementary = '<section class="supplement"><h2>补充阅读</h2><ul>' + "".join(items) + '</ul></section>'
    cost = f"期间账户已用 +{model['cost']} 次" if model["cost"] is not None else "账户计数变化未记录"
    if model["balance"] is not None:
        cost += f" · 余额 {model['balance']} 次"
    stats = "".join(f'<div><strong>{esc(value)}</strong><span>{label}</span></div>' for value, label in stat_items(model))
    limitations = []
    if model["summary"].get("stopping_reason_zh"):
        limitations.append(f'<p>{esc(model["summary"]["stopping_reason_zh"])}</p>')
    limitations.extend(f'<p>{esc(note)}</p>' for note in model["report_notes"])
    unresolved = model["summary"].get("unresolved_query_ids") or []
    if unresolved:
        limitations.append('<p>未完成查询：' + "、".join(f'<code>{esc(value)}</code>' for value in unresolved) + '。</p>')
    css = (ASSETS / "report.css").read_text(encoding="utf-8")
    js = (ASSETS / "report.js").read_text(encoding="utf-8")
    logo = base64.b64encode((ASSETS / "chipseeker_logo.jpg").read_bytes()).decode("ascii")
    intro = f'<p class="intro">{esc(model["report_summary"])}</p>' if model["report_summary"] else ""
    goal = search_goal_label(model)
    goal_meta = f"<span>检索目标：{esc(goal)}</span>" if goal else ""
    audit = ""
    if model["audit"]:
        entries = ''.join(f'<tr><td>{esc(d.get("title"))}</td><td>{esc(d.get("label") or d.get("decision"))}</td><td>{esc(d.get("reason") or d.get("reason_zh"))}</td></tr>' for d in model["audit"])
        audit = f'<section class="limitations"><details><summary>筛选审计</summary><div class="audit-table"><table><thead><tr><th>论文</th><th>决定</th><th>证据与边界</th></tr></thead><tbody>{entries}</tbody></table></div></details></section>'
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>结果报告 · {esc(model['title'])}</title><style>{css}</style></head>
<body><main><header><div class="brand"><img src="data:image/jpeg;base64,{logo}" alt="ChipSeeker 标志" width="112" height="48">芯寻 <span>UltraSearch</span></div><h1>{esc(model['title'])}</h1><div class="header-meta"><span>{esc(model['date'])} · 论文检索结果</span>{goal_meta}<span>{esc(cost)}</span><a href="{quote(MD_NAME)}">Markdown</a></div>{intro}</header>
<section class="stats" aria-label="检索统计">{stats}</section><p class="status {'partial' if model['partial'] else 'complete'}">{status_text(model)}</p>
<section class="results" aria-labelledby="results-heading"><div class="section-heading"><h2 id="results-heading">论文结果</h2><span id="visible-count" aria-live="polite">{len(model['papers'])} / {len(model['papers'])} 篇</span></div>
<form class="filters" id="filters" role="search"><label class="search-field">检索结果<input type="search" id="search" placeholder="题目、作者、摘要、DOI" autocomplete="off"></label>
<label>Venue<select id="venue"><option value="">全部</option>{options(venues)}</select></label>
<label>优先级<select id="stars"><option value="0">全部</option><option value="5">五星</option><option value="4">四星及以上</option><option value="3">三星及以上</option></select></label>
<label>来源<select id="origin"><option value="">全部</option>{options(origins)}</select></label>
<label>排序<select id="sort"><option value="default">推荐顺序</option><option value="stars">优先级</option><option value="year">年份最新</option><option value="relevance">Lite相关性</option></select></label><button type="reset">重置</button></form>
<noscript><p class="muted">JavaScript未启用，以下展示全部论文。</p></noscript>
<div id="paper-list">{''.join(articles)}</div><p id="no-results" hidden>没有符合筛选条件的论文。</p></section>{supplementary}
{audit}<section class="limitations"><h2>检索范围与限制</h2>{''.join(limitations)}<p class="muted">星级与保留理由为AI阅读建议，不代表论文质量认证。原始摘要与全文核对范围逐篇标注。PDF访问取决于来源权限。</p></section>
<footer>芯寻 UltraSearch · <a href="{quote(MD_NAME)}">结果报告.md</a> · <a href="{quote(DATA_DIR)}/">检索数据</a></footer></main><script>{js}</script></body></html>'''


def render_workspace(workspace, *, overwrite=False):
    workspace = Path(workspace).expanduser().resolve()
    source = workspace / DATA_DIR / "papers.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    previous = workspace / DATA_DIR / "report-data.json"
    if previous.is_file():
        saved = json.loads(previous.read_text(encoding="utf-8"))
        for key in ("manual_exclusions", "version_relations"):
            combined = list(payload.get(key) or [])
            for entry in saved.get(key) or []:
                if entry not in combined:
                    combined.append(entry)
            payload[key] = combined
        if "review_policy" not in payload and saved.get("review_policy"):
            payload["review_policy"] = saved["review_policy"]
        for key in ("screening_audit", "gap_checks"):
            if key not in payload:
                payload[key] = saved.get(key) or ([] if key == "screening_audit" else {})
    if payload.get("schema") == "ultrasearch-screened-papers/v2" or "review_policy" in payload:
        from review_workflow import plan_review, prepare_review

        payload = prepare_review(payload, workspace / DATA_DIR)
        pending_review = plan_review(payload)["screening_candidates"]
        if pending_review:
            payload["summary"] = {**(payload.get("summary") or {}), "status": "partial_pending_screening"}
            payload["report_notes_zh"] = [*(payload.get("report_notes_zh") or []),
                                          f"{len(pending_review)} 项库外候选尚待证据筛选，未自动写入主表。"]
    model = normalize_report(payload, workspace)
    structured = {
        "schema": "ultrasearch-report/v2", "generated_at": payload.get("generated_at"),
        "title": model["title"], "summary": model["summary"], "partial": model["partial"],
        "review_policy": model["review_policy"], "manual_exclusions": model["manual_exclusions"],
        "papers": [_public_record(p) for p in model["papers"]],
        "screening_audit": model["audit"], "version_relations": model["relations"],
        "relation_audit": model["relation_audit"],
        **{key: model[key] for key in ("gap_checks", "gap_check_plan", "research_branches", "source_checks", "reference_leads", "searches")},
    }
    outputs = [(workspace / MD_NAME, render_markdown(model)), (workspace / HTML_NAME, render_html(model)),
               (workspace / DATA_DIR / "report-data.json", json.dumps(structured, ensure_ascii=False, indent=2)),
               (workspace / DATA_DIR / "publications.ris", render_ris(model))]
    if not overwrite and any(path.exists() for path, _ in outputs):
        raise FileExistsError("A report already exists. Preserve user edits; pass --overwrite only for an intended regeneration.")
    staged = []
    try:
        for path, content in outputs:
            temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            staged.append((temporary, path))
        for temporary, path in staged:
            temporary.replace(path)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return {"workspace": str(workspace), "markdown": str(workspace / MD_NAME), "html": str(workspace / HTML_NAME), "retained_papers": len(model["papers"]), "partial": model["partial"]}


def exclude_publication(workspace, doi, reason):
    from review_policy import record_manual_exclusion
    from work_versions import normalize_doi
    workspace = Path(workspace).expanduser().resolve()
    path = workspace / DATA_DIR / "papers.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected = next((p for p in payload["papers"] if normalize_doi(p.get("doi")) == normalize_doi(doi)), None)
    if selected is None:
        raise ValueError("The publication DOI must exist in this workspace's papers.json.")
    payload = record_manual_exclusion(payload, selected, reason)
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"workspace": str(workspace), "excluded_doi": normalize_doi(doi), "reason": reason}


def _public_record(record):
    return {key: [_public_record(v) for v in value] if key == "versions" else value
            for key, value in record.items() if not key.startswith("_")}


def render_ris(model):
    from work_versions import normalize_doi, publication_key
    lines, seen = [], set()
    for work in model["papers"]:
        for p in [work, *work["_versions"]]:
            identity = publication_key(p)
            if identity in seen:
                continue
            seen.add(identity)
            kind = p.get("publication_type") or p.get("publication_status") or (p.get("review_evidence") or {}).get("publication_type")
            typ = {"journal": "JOUR", "journal-article": "JOUR", "conference": "CONF", "conference-paper": "CONF", "proceedings-article": "CONF"}.get(kind, "GEN")
            fields = [("TY", typ), ("TI", p.get("title")), ("PY", p.get("year")), ("T2", p.get("venue_name") or p.get("venue")), ("DO", normalize_doi(p.get("doi")))]
            authors = p.get("authors") or []
            if isinstance(authors, str):
                authors = [a.strip() for a in authors.split(";")]
            fields.extend(("AU", a) for a in authors)
            fields.extend(("UR", url) for _, url in external_links(p))
            for key, value in fields:
                if value:
                    lines.append(f"{key}  - {' '.join(str(value).split())}")
            lines.extend(["ER  -", ""])
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    initialize = sub.add_parser("init")
    initialize.add_argument("--parent", required=True)
    initialize.add_argument("--name", required=True, help="AI-summarized concise topic name, not an arbitrary slug.")
    initialize.add_argument("--resume", action="store_true")
    render = sub.add_parser("render")
    render.add_argument("--workspace", required=True)
    render.add_argument("--overwrite", action="store_true")
    exclude = sub.add_parser("exclude", help="Persist a manual exclusion before the next render or refresh.")
    exclude.add_argument("--workspace", required=True)
    exclude.add_argument("--doi", required=True)
    exclude.add_argument("--reason", default="用户手动排除")
    review = sub.add_parser("review-next", help="Plan or record follow-ups before rendering.")
    review.add_argument("--workspace", required=True)
    review.add_argument("--receipt", help="Record a follow-up and queue verified discoveries for screening.")
    args = parser.parse_args()
    try:
        if args.command == "init":
            workspace = init_workspace(args.parent, args.name, resume=args.resume)
            result = {"workspace": str(workspace), "data_dir": str(workspace / DATA_DIR), "queries": str(workspace / DATA_DIR / "queries.json"), "checkpoints": str(workspace / DATA_DIR / "checkpoints"), "union": str(workspace / DATA_DIR / "union.json")}
        elif args.command == "exclude":
            result = exclude_publication(args.workspace, args.doi, args.reason)
        elif args.command == "review-next":
            from review_workflow import review_workspace

            result = review_workspace(Path(args.workspace).expanduser().resolve() / DATA_DIR, args.receipt)
        else:
            result = render_workspace(args.workspace, overwrite=args.overwrite)
        print(json.dumps(result))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"Research output error: {error}\n")


if __name__ == "__main__":
    main()

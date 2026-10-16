#!/usr/bin/env python3
"""Create tidy research workspaces and render curated paper data offline."""

import argparse
import base64
import html
import json
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit


DATA_DIR = "检索数据"
MD_NAME = "结果报告.md"
HTML_NAME = "结果报告.html"
ASSETS = Path(__file__).resolve().parents[1] / "assets"


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
    if source:
        links.append(("来源", source))
    if doi and doi != source:
        links.append(("DOI", doi))
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
    return "仅题目/元数据；摘要缺失"


def normalize_report(payload, workspace):
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        raise ValueError("papers.json must be an object containing a curated papers list.")
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
        rows.append(row)
    failures = summary.get("failed_queries", len(summary.get("unresolved_query_ids") or []))
    if isinstance(failures, list):
        failures = len(failures)
    failures = int(failures or 0)
    partial = failures > 0 or str(summary.get("status", "")).startswith("partial")
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
        "report_notes": payload.get("report_notes_zh") or [],
    }


def stat_items(model):
    summary = model["summary"]
    return [
        (len(model["papers"]), "保留论文"),
        (summary.get("unique_papers", "未记录"), "去重候选"),
        (summary.get("successful_queries", "未记录"), "成功查询"),
        (model["failures"], "未完成查询"),
    ]


def status_text(model):
    if model["partial"]:
        return f"本轮仍有{model['failures']}项查询未完成，结果为部分检索记录，不代表已搜全。" if model["failures"] else "本轮检索尚未完成，不代表已搜全。"
    return "本轮检索已完成；不承诺穷尽所有文献。"


def markdown_links(paper):
    links = [f"[{label}](<{url}>)" for label, url in paper["_links"]]
    if paper["_local_pdf"]:
        links.append(f"[本地PDF](<{paper['_local_pdf'].as_posix()}>)")
    return " · ".join(links) or "未确认有效链接"


def render_markdown(model):
    lines = [f"# 结果报告：{md(model['title'])}", "", f"检索日期：{md(model['date'])} · 芯寻 UltraSearch", "", f"> {status_text(model)}", ""]
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
    lines.extend(["", "## 逐篇信息", ""])
    for p in model["papers"]:
        lines.extend([f'<a id="{p["_id"].lower()}"></a>', f"### {p['_id']} · {md(p['title'])}", "", f"作者：{md(p['_authors'] or '未提供')}。", "", f"{md(p.get('year'))} · {md(p['_venue'])} · {p['_origin']} · {p['_evidence']}", "", f"**为什么保留：** {md(p.get('why_keep_zh') or '未提供')}", ""])
        if p.get("evidence_limit_zh"):
            lines.extend([f"**证据边界：** {md(p['evidence_limit_zh'])}", ""])
        lines.extend([markdown_links(p), ""])
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
<h2>{esc(p['title'])}</h2><p class="authors">{esc(p['_authors'] or '作者未提供')}</p>
<p class="match">{esc(p.get('match_description_zh'))}</p><p class="reason">{esc(p.get('why_keep_zh') or '保留理由未提供')}</p>
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
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light"><title>结果报告 · {esc(model['title'])}</title><style>{css}</style></head>
<body><main><header><div class="brand"><img src="data:image/jpeg;base64,{logo}" alt="ChipSeeker 标志" width="112" height="48">芯寻 <span>UltraSearch</span></div><h1>{esc(model['title'])}</h1><div class="header-meta"><span>{esc(model['date'])} · 论文检索结果</span><span>{esc(cost)}</span><a href="{quote(MD_NAME)}">Markdown</a></div>{intro}</header>
<section class="stats" aria-label="检索统计">{stats}</section><p class="status {'partial' if model['partial'] else 'complete'}">{status_text(model)}</p>
<section class="results" aria-labelledby="results-heading"><div class="section-heading"><h2 id="results-heading">论文结果</h2><span id="visible-count" aria-live="polite">{len(model['papers'])} / {len(model['papers'])} 篇</span></div>
<form class="filters" id="filters" role="search"><label class="search-field">检索结果<input type="search" id="search" placeholder="题目、作者、摘要、DOI" autocomplete="off"></label>
<label>Venue<select id="venue"><option value="">全部</option>{options(venues)}</select></label>
<label>优先级<select id="stars"><option value="0">全部</option><option value="5">五星</option><option value="4">四星及以上</option><option value="3">三星及以上</option></select></label>
<label>来源<select id="origin"><option value="">全部</option>{options(origins)}</select></label>
<label>排序<select id="sort"><option value="default">推荐顺序</option><option value="stars">优先级</option><option value="year">年份最新</option><option value="relevance">Lite相关性</option></select></label><button type="reset">重置</button></form>
<noscript><p class="muted">JavaScript未启用，以下展示全部论文。</p></noscript>
<div id="paper-list">{''.join(articles)}</div><p id="no-results" hidden>没有符合筛选条件的论文。</p></section>{supplementary}
<section class="limitations"><h2>检索范围与限制</h2>{''.join(limitations)}<p class="muted">星级与保留理由为AI阅读建议，不代表论文质量认证。原始摘要与全文核对范围逐篇标注。PDF访问取决于来源权限。</p></section>
<footer>芯寻 UltraSearch · <a href="{quote(MD_NAME)}">结果报告.md</a> · <a href="{quote(DATA_DIR)}/">检索数据</a></footer></main><script>{js}</script></body></html>'''


def render_workspace(workspace, *, overwrite=False):
    workspace = Path(workspace).expanduser().resolve()
    source = workspace / DATA_DIR / "papers.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    model = normalize_report(payload, workspace)
    outputs = [(workspace / MD_NAME, render_markdown(model)), (workspace / HTML_NAME, render_html(model))]
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
    args = parser.parse_args()
    try:
        if args.command == "init":
            workspace = init_workspace(args.parent, args.name, resume=args.resume)
            result = {"workspace": str(workspace), "data_dir": str(workspace / DATA_DIR), "queries": str(workspace / DATA_DIR / "queries.json"), "checkpoints": str(workspace / DATA_DIR / "checkpoints"), "union": str(workspace / DATA_DIR / "union.json")}
        else:
            result = render_workspace(args.workspace, overwrite=args.overwrite)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError) as error:
        parser.exit(1, f"Research output error: {error}\n")


if __name__ == "__main__":
    main()

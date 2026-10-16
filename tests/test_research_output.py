import importlib.util
import base64
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/chipseeker-ultra-search/scripts/research_output.py"
SPEC = importlib.util.spec_from_file_location("research_output_test", SCRIPT)
output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(output)


def payload():
    return {
        "generated_at": "2026-10-16T00:00:00Z",
        "summary": {"research_direction": "pulsed cryogenic LNA", "status": "partial", "failed_queries": 1,
                    "successful_queries": 3, "unique_papers": 12, "unresolved_query_ids": ["failed-angle"],
                    "quota_at_start": {"credits_used": 9}, "quota_at_finish": {"credits_used": 12, "credits_left": 88}},
        "papers": [{"title": "A Pulsed LNA", "year": "2025", "authors": ["A. Author"], "venue_name": "TMTT",
                    "importance_stars": 5, "abstract": "Source abstract", "abstract_kind": "source_abstract",
                    "why_keep_zh": "测试保留理由", "evidence_limit_zh": "摘要核实，未读全文", "doi": "10.1109/example",
                    "pdf_link": "https://example.org/paper.pdf", "source_in_current_corpus": True}],
    }


def prepare(tmp_path):
    workspace = output.init_workspace(tmp_path, "量子读出LNA")
    (workspace / output.DATA_DIR / "papers.json").write_text(json.dumps(payload(), ensure_ascii=False), encoding="utf-8")
    return workspace


def test_parent_gets_one_topic_folder_not_scattered_outputs(tmp_path):
    workspace = prepare(tmp_path)
    output.render_workspace(workspace)
    assert list(tmp_path.iterdir()) == [workspace]
    assert {p.name for p in workspace.iterdir()} == {"结果报告.md", "结果报告.html", "检索数据"}


def test_new_run_collision_creates_separate_folder_and_resume_is_explicit(tmp_path):
    first = output.init_workspace(tmp_path, "Example")
    marker = first / "manual.txt"
    marker.write_text("user edit")
    second = output.init_workspace(tmp_path, "Example")
    assert first != second
    assert second.name.startswith("芯寻_Example_")
    assert output.init_workspace(tmp_path, "Example", resume=True) == first
    assert marker.read_text() == "user edit"


@pytest.mark.parametrize("name", ["../../方向", "bad:name?", "芯寻_脉冲低温LNA"])
def test_topic_name_stays_within_parent(tmp_path, name):
    workspace = output.init_workspace(tmp_path, name)
    assert workspace.parent == tmp_path.resolve()
    assert workspace.name.startswith("芯寻_")


def test_missing_title_is_not_silently_fabricated(tmp_path):
    with pytest.raises(ValueError, match="title"):
        output.normalize_report({"papers": [{}]}, tmp_path)


def test_existing_report_edits_are_preserved_without_explicit_overwrite(tmp_path):
    workspace = prepare(tmp_path)
    output.render_workspace(workspace)
    report = workspace / output.MD_NAME
    report.write_text("user edit", encoding="utf-8")
    with pytest.raises(FileExistsError):
        output.render_workspace(workspace)
    assert report.read_text(encoding="utf-8") == "user edit"


def test_same_titles_links_priority_partial_status_in_both_formats(tmp_path):
    workspace = prepare(tmp_path)
    result = output.render_workspace(workspace)
    markdown = Path(result["markdown"]).read_text(encoding="utf-8")
    html = Path(result["html"]).read_text(encoding="utf-8")
    for document in (markdown, html):
        for value in ("A Pulsed LNA", "★★★★★", "https://example.org/paper.pdf", "测试保留理由", "failed-angle"):
            assert value in document
        assert "未完成" in document
    assert "检索期间账户已用增加：3次" in markdown
    assert "期间账户已用 +3 次" in html
    assert result["retained_papers"] == 1 and result["partial"]
    assert '<script src=' not in html and '<link rel="stylesheet"' not in html


def test_shared_account_delta_is_not_mislabeled_as_a_run_invoice(tmp_path):
    data = payload()
    data["summary"]["successful_queries"] = 11
    data["summary"]["quota_at_start"] = {"credits_used": 0}
    data["summary"]["quota_at_finish"] = {"credits_used": 18, "credits_left": 82}
    data["report_notes_zh"] = ["本次确认完成11次请求；同期其他会话也在使用此账户。"]
    model = output.normalize_report(data, tmp_path)
    markdown, html = output.render_markdown(model), output.render_html(model)
    assert "检索期间账户已用增加：18次" in markdown
    assert "成功查询：11" in markdown
    for document in (markdown, html):
        assert "其他会话" in document
        assert "本轮API扣除" not in document and "本轮扣除" not in document


def test_explicit_zero_transport_failures_remains_zero_in_partial_research(tmp_path):
    data = payload()
    data["summary"].update(status="partial_budget_limited", failed_queries=0, unresolved_query_ids=["not-yet-run"])
    model = output.normalize_report(data, tmp_path)
    assert model["failures"] == 0 and model["partial"]
    assert "本轮检索尚未完成" in output.status_text(model)


def test_logo_is_embedded_for_portable_offline_reports(tmp_path):
    class Images(HTMLParser):
        def __init__(self):
            super().__init__()
            self.images = []

        def handle_starttag(self, tag, attrs):
            if tag == "img":
                self.images.append(dict(attrs))

    parser = Images()
    parser.feed(output.render_html(output.normalize_report(payload(), tmp_path)))
    assert len(parser.images) == 1
    logo = parser.images[0]
    assert logo["alt"] and int(logo["width"]) > 0 and int(logo["height"]) > 0
    prefix, encoded = logo["src"].split(",", 1)
    assert prefix == "data:image/jpeg;base64"
    assert base64.b64decode(encoded, validate=True) == (output.ASSETS / "chipseeker_logo.jpg").read_bytes()
    assert base64.b64decode(encoded).startswith(b"\xff\xd8\xff")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,evil", "//example.org", "https://user:pass@example.org", "https://[invalid", "https://a/\nevil"])
def test_unsafe_or_malformed_links_are_rejected(url):
    assert output.safe_url(url) == ""


def test_untrusted_paper_text_is_escaped_not_executed(tmp_path):
    data = payload()
    data["papers"][0]["title"] = '</h2><script>alert("injected")</script>'
    data["papers"][0]["abstract"] = '<img src=x onerror="alert(1)">'
    model = output.normalize_report(data, tmp_path)
    html = output.render_html(model)
    assert '<script>alert("injected")</script>' not in html
    assert '<img src=x' not in html
    assert "&lt;script&gt;" in html


def test_local_pdf_link_only_exists_for_real_pdf(tmp_path):
    workspace = prepare(tmp_path)
    pdf = workspace / "papers" / "Example.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"%PDF-1.4\n")
    data = payload()
    data["papers"][0]["local_pdf_path"] = "papers/Example.pdf"
    model = output.normalize_report(data, workspace)
    assert pdf.as_uri() in output.render_html(model)
    pdf.unlink()
    model = output.normalize_report(data, workspace)
    assert "本地PDF</a>" not in output.render_html(model)


def test_missing_abstract_and_unrated_paper_stay_explicit(tmp_path):
    model = output.normalize_report({"papers": [{"title": "Unknown"}]}, tmp_path)
    html = output.render_html(model)
    assert "未评级" in html
    assert "摘要缺失" in html
    assert "来源未标注" in html
    assert "未确认有效链接" in output.render_markdown(model)


def test_generated_html_has_one_unique_anchor_per_paper(tmp_path):
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.ids = []

        def handle_starttag(self, tag, attrs):
            self.ids += [value for key, value in attrs if key == "id"]

    data = payload()
    data["papers"] *= 2
    parser = Parser()
    parser.feed(output.render_html(output.normalize_report(data, tmp_path)))
    assert len(parser.ids) == len(set(parser.ids))
    assert "p01" in parser.ids and "p02" in parser.ids

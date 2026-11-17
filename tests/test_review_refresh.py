import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/chipseeker-ultra-search"
SPEC = importlib.util.spec_from_file_location("refresh_output", SKILL / "scripts/research_output.py")
output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(output)


def write_payload(workspace, value):
    (workspace / output.DATA_DIR / "papers.json").write_text(json.dumps(value), encoding="utf-8")


def test_explicit_exclusion_survives_a_fresh_collector_replacing_the_source(tmp_path):
    workspace = output.init_workspace(tmp_path, "refresh")
    original = {"papers": [{"title": "Preserved manual choice", "doi": "10.1234/example"}]}
    write_payload(workspace, original)
    output.render_workspace(workspace)
    output.exclude_publication(workspace, "https://doi.org/10.1234/example", "User decision")
    assert output.render_workspace(workspace, overwrite=True)["retained_papers"] == 0
    write_payload(workspace, original)
    assert output.render_workspace(workspace, overwrite=True)["retained_papers"] == 0
    saved = json.loads((workspace / output.DATA_DIR / "report-data.json").read_text())
    assert saved["manual_exclusions"] and saved["screening_audit"][0]["decision"] == "manual_exclusion"


def test_saved_exclusion_blocks_a_new_version_even_when_old_seed_is_absent(tmp_path):
    payload = json.loads((SKILL / "examples/circuit-review.json").read_text(encoding="utf-8"))
    relation = next(r for r in payload["version_relations"] if r["status"] == "confirmed")
    payload["papers"] = [p for p in payload["papers"] if p["doi"] in (relation["from_doi"], relation["to_doi"])]
    workspace = output.init_workspace(tmp_path, "new-version")
    write_payload(workspace, payload)
    output.exclude_publication(workspace, relation["from_doi"], "User decision")
    assert output.render_workspace(workspace)["retained_papers"] == 0
    payload["papers"] = [p for p in payload["papers"] if p["doi"] == relation["to_doi"]]
    payload.pop("version_relations")
    write_payload(workspace, payload)
    assert output.render_workspace(workspace, overwrite=True)["retained_papers"] == 0


def test_pending_gap_check_is_partial_research_with_zero_transport_failures(tmp_path):
    payload = {"schema": "ultrasearch-screened-papers/v2", "generated_at": "2026-11-17", "papers": [],
               "summary": {"status": "completed", "failed_queries": 0},
               "research_branches": [{"branch_id": "unresolved-mechanism", "candidate_count": 0}]}
    model = output.normalize_report(payload, tmp_path)
    assert model["partial"] and model["failures"] == 0
    assert model["gap_check_plan"]["checks"]


def test_installed_skill_replays_acceptance_without_repo_checkout_or_credentials(tmp_path):
    target = tmp_path / "installed-skills"
    subprocess.run([sys.executable, str(ROOT / "install.py"), "--skills-dir", str(target)], check=True, capture_output=True)
    env = {key: value for key, value in os.environ.items() if key not in ("CHIPSEEKER_API_KEY", "CHIPSEEKER_BASE_URL")}
    result = subprocess.run([sys.executable, str(target / "chipseeker-ultra-search/scripts/verify_circuit_review.py"),
                             "--parent", str(tmp_path / "reports")], env=env, cwd=tmp_path, check=True, capture_output=True)
    receipt = json.loads(result.stdout)
    assert receipt["retained_papers"] == 4
    assert receipt["formal_publications"] == 6
    assert receipt["version_directions_verified"] == 4
    assert receipt["network_requests"] == 0

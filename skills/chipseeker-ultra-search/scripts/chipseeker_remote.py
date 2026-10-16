#!/usr/bin/env python3
"""Use the ChipSeeker remote retrieval API from a user-owned coding agent."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://chipseeker.online"
CONFIG_PATH = Path.home() / ".chipseeker" / "config.json"


class RemoteError(RuntimeError):
    def __init__(self, message, status=0, payload=None):
        super().__init__(message)
        self.status = int(status or 0)
        self.payload = payload or {}


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {} if default is None else default


def _write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_config():
    config = _read_json(CONFIG_PATH, {})
    config["base_url"] = str(os.environ.get("CHIPSEEKER_BASE_URL") or config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    config["api_key"] = str(os.environ.get("CHIPSEEKER_API_KEY") or config.get("api_key") or "").strip()
    return config


def save_config(base_url, api_key, email=""):
    payload = {
        "base_url": str(base_url or DEFAULT_BASE_URL).rstrip("/"),
        "api_key": str(api_key or "").strip(),
        "email": str(email or "").strip().lower(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_json(CONFIG_PATH, payload)
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return payload


def api_request(method, path, *, payload=None, api_key="", base_url="", timeout=240):
    config = load_config()
    base_url = str(base_url or config["base_url"]).rstrip("/")
    api_key = str(api_key or config["api_key"]).strip()
    headers = {"Accept": "application/json", "User-Agent": "ChipSeeker-UltraSearch/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method.upper())
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(raw)
        except json.JSONDecodeError:
            error_payload = {"detail": raw or exc.reason}
        raise RemoteError(str(error_payload.get("detail") or exc.reason), exc.code, error_payload) from exc
    except URLError as exc:
        raise RemoteError(f"Cannot reach ChipSeeker: {exc.reason}") from exc


def remote_search(payload, timeout=900, poll_seconds=2.0):
    submitted = api_request("POST", "/api/v1/search", payload=payload, timeout=60)
    job_id = str(submitted.get("job_id", "") or "")
    if not job_id:
        return submitted
    deadline = time.monotonic() + max(30, int(timeout))
    while time.monotonic() < deadline:
        state = api_request("GET", f"/api/v1/search/jobs/{job_id}", timeout=60)
        status = str(state.get("status", "") or "")
        if status == "done":
            return state.get("result", {})
        if status == "error":
            message = str(state.get("error") or "ChipSeeker search failed.")
            status_code = 429 if "quota" in message.lower() or "limit" in message.lower() else 503
            raise RemoteError(message, status_code, state)
        time.sleep(max(0.5, float(poll_seconds)))
    raise RemoteError(f"ChipSeeker search job {job_id} timed out locally; retry with the same checkpoint.", 504)


def _years(value):
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return [int(value[0]), int(value[1])]
    raw = str(value or "2000:2100").strip()
    separator = ":" if ":" in raw else "-" if "-" in raw else ""
    if separator:
        start, end = raw.split(separator, 1)
        return [int(start), int(end)]
    year = int(raw)
    return [year, year]


def normalize_request(entry, defaults=None):
    defaults = defaults or {}
    mode = str(entry.get("mode", defaults.get("mode", "lite")) or "lite").strip().lower().replace("-", "_")
    request = {
        "mode": mode,
        "query": str(entry.get("query", "") or "").strip(),
        "top_k": int(entry.get("top_k", defaults.get("top_k", 100)) or 200),
        "years": _years(entry.get("years", defaults.get("years", "2000:2100"))),
        "venues": list(entry.get("venues", defaults.get("venues", [])) or []),
        "abstract_chars": int(entry.get("abstract_chars", defaults.get("abstract_chars", 0)) or 0),
        "result_view": str(entry.get("result_view", defaults.get("result_view", "titles")) or "titles"),
    }
    for field in ("must_have", "expression"):
        value = entry.get(field, defaults.get(field, ""))
        if value:
            request[field] = str(value)
    for field in ("fields", "all_terms", "any_terms", "exact_titles", "dois", "authors"):
        values = entry.get(field, defaults.get(field, []))
        if values:
            request[field] = list(values)
    return request


def _normalize_doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return value.rstrip(".,; ")


def _normalize_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", value)


def _identity(paper):
    doi = _normalize_doi(paper.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    title = _normalize_title(paper.get("title", ""))
    year = str(paper.get("year", "") or "")
    return f"title-year:{title}|{year}"


def _rank_paper(paper):
    retrievals = paper.get("retrievals", [])
    semantic = [float(item.get("similarity", 0) or 0) for item in retrievals if item.get("mode") != "keyword"]
    best_similarity = max(semantic) if semantic else (0.7 if retrievals else 0.0)
    family_count = max(1, len({item.get("query_family") or item.get("query_id") for item in retrievals}))
    corroboration = min(100.0, 50.0 + 15.0 * (family_count - 1))
    relevance = min(100.0, 85.0 * best_similarity + 0.15 * corroboration)
    venue_quality = float(paper.get("venue_quality", 50) or 50)
    try:
        year = int(re.search(r"\d{4}", str(paper.get("year", ""))).group())
    except (AttributeError, ValueError):
        year = 0
    current_year = datetime.now(timezone.utc).year
    recency = 0.0 if not year else max(40.0, 100.0 - 5.0 * max(0, current_year - year))
    return {
        "final_score": round(0.65 * relevance + 0.25 * venue_quality + 0.10 * recency, 2),
        "relevance": round(relevance, 2),
        "venue_quality": round(venue_quality, 2),
        "recency": round(recency, 2),
        "query_corroboration": round(corroboration, 2),
        "venue_tier": str(paper.get("venue_tier", "C") or "C"),
    }


def merge_responses(responses, attempted):
    merged = {}
    raw_count = 0
    failed = []
    searches = []
    last_quota = {}
    for response in responses:
        entry = response.get("_entry", {})
        query_id = str(entry.get("id", ""))
        status = response.get("status", "completed")
        searches.append(
            {
                "query_id": query_id,
                "query_family": entry.get("query_family", ""),
                "mode": entry.get("mode", "lite"),
                "query": entry.get("query", ""),
                "status": status,
                "result_count": int(response.get("result_count", 0) or 0),
                "error": response.get("error", ""),
            }
        )
        if status != "completed":
            failed.append(query_id)
            continue
        last_quota = response.get("quota") or last_quota
        for item in response.get("results", []):
            raw_count += 1
            key = _identity(item)
            if key not in merged:
                merged[key] = dict(item)
                merged[key]["retrievals"] = []
            merged[key]["retrievals"].append(
                {
                    "query_id": query_id,
                    "query_family": entry.get("query_family", ""),
                    "mode": entry.get("mode", "lite"),
                    "rank": item.get("rank"),
                    "similarity": item.get("similarity"),
                }
            )
    papers = list(merged.values())
    for paper in papers:
        paper["retrieval_count"] = len(paper["retrievals"])
        paper["retrieval_family_count"] = len(
            {item.get("query_family") or item.get("query_id") for item in paper["retrievals"]}
        )
        paper["ranking"] = _rank_paper(paper)
    papers.sort(key=lambda item: item["ranking"]["final_score"], reverse=True)
    return {
        "schema": "chipseeker-remote-collect/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "attempted_queries": len(attempted),
            "successful_queries": len(attempted) - len(failed),
            "failed_queries": len(failed),
            "raw_results": raw_count,
            "unique_papers": len(papers),
        },
        "failed_queries": failed,
        "retry_query_ids": failed,
        "searches": searches,
        "quota": last_quota,
        "papers": papers,
    }


def _checkpoint_path(directory, query_id):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(query_id or "query"))
    return Path(directory) / f"{safe}.json"


def _run_entry(entry, defaults, checkpoint_dir, retries):
    query_id = str(entry.get("id") or hashlib.sha1(json.dumps(entry, sort_keys=True).encode()).hexdigest()[:12])
    entry = dict(entry)
    entry["id"] = query_id
    checkpoint = _checkpoint_path(checkpoint_dir, query_id) if checkpoint_dir else None
    request = normalize_request(entry, defaults)
    config = load_config()
    fingerprint = hashlib.sha256(json.dumps(
        {"request": request, "base_url": config["base_url"], "email": config.get("email", "")},
        ensure_ascii=False, sort_keys=True,
    ).encode("utf-8")).hexdigest()
    existing = {}
    if checkpoint and checkpoint.exists():
        existing = _read_json(checkpoint, {})
        if existing.get("_request_fingerprint") != fingerprint:
            existing = {}
        if existing.get("status") == "completed":
            existing["_entry"] = entry
            return existing
    request_id = existing.get("_client_request_id") or uuid.uuid4().hex
    request["client_request_id"] = request_id
    checkpoint_metadata = {"_client_request_id": request_id, "_request_fingerprint": fingerprint}
    if checkpoint:
        _write_json(checkpoint, {"status": "pending", **checkpoint_metadata})
    last_error = None
    for attempt in range(max(0, retries) + 1):
        try:
            response = remote_search(request)
            response["status"] = "completed"
            response["_entry"] = entry
            response.update(checkpoint_metadata)
            if checkpoint:
                _write_json(checkpoint, {key: value for key, value in response.items() if key != "_entry"})
            return response
        except RemoteError as exc:
            last_error = exc
            if exc.status in {400, 401, 402, 403, 429} or attempt >= retries:
                break
            time.sleep(min(8, 2 ** attempt))
    failed = {
        "status": "failed",
        "result_count": 0,
        "results": [],
        "error": str(last_error or "Unknown error"),
        "http_status": getattr(last_error, "status", 0),
        "_entry": entry,
        **checkpoint_metadata,
    }
    if checkpoint:
        _write_json(checkpoint, {key: value for key, value in failed.items() if key != "_entry"})
    return failed


def command_configure(args):
    api_key = args.api_key or os.environ.get("CHIPSEEKER_API_KEY", "")
    if not api_key:
        raise ValueError("Provide --api-key or set CHIPSEEKER_API_KEY.")
    quota = api_request("GET", "/api/v1/quota", base_url=args.base_url, api_key=api_key)
    save_config(args.base_url, api_key, quota.get("email", ""))
    print(json.dumps({"ok": True, "email": quota.get("email"), "config_path": str(CONFIG_PATH)}, ensure_ascii=False))


def command_auth_send(args):
    response = api_request("POST", "/api/v1/auth/send-code", payload={"email": args.email}, base_url=args.base_url)
    print(json.dumps(response, ensure_ascii=False))


def command_auth_verify(args):
    response = api_request(
        "POST",
        "/api/v1/auth/verify",
        payload={"email": args.email, "code": args.code, "label": args.label},
        base_url=args.base_url,
    )
    save_config(args.base_url, response["api_key"], response.get("email", args.email))
    print(json.dumps({"ok": True, "email": response.get("email"), "config_path": str(CONFIG_PATH)}, ensure_ascii=False))


def command_quota(_args):
    print(json.dumps(api_request("GET", "/api/v1/quota"), ensure_ascii=False, indent=2))


def command_search(args):
    if args.request:
        payload = _read_json(args.request, {})
    else:
        payload = normalize_request(
            {
                "mode": args.mode,
                "query": args.query,
                "top_k": args.top_k,
                "years": args.years,
                "abstract_chars": args.abstract_chars,
                "result_view": args.result_view,
            }
        )
    response = remote_search(payload)
    if args.output:
        _write_json(args.output, response)
    print(json.dumps(response, ensure_ascii=False))


def command_collect(args):
    spec = _read_json(args.query_spec, {})
    entries = list(spec.get("queries", []))
    if not entries:
        raise ValueError("The query spec contains no queries.")
    defaults = dict(spec.get("defaults", {}))
    workers = max(1, min(int(args.workers), 4, len(entries)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_entry, entry, defaults, args.checkpoint_dir, args.retries) for entry in entries]
        responses = [future.result() for future in futures]
    merged = merge_responses(responses, entries)
    try:
        merged["quota"] = api_request("GET", "/api/v1/quota").get("quota", merged["quota"])
    except RemoteError:
        pass
    _write_json(args.output, merged)
    print(json.dumps(merged["summary"], ensure_ascii=False))
    if merged["failed_queries"]:
        print("Retry query IDs: " + ", ".join(merged["failed_queries"]), file=sys.stderr)


def build_parser():
    parser = argparse.ArgumentParser(description="Remote ChipSeeker client for user-owned AI agents.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    configure = subparsers.add_parser("configure")
    configure.add_argument("--base-url", default=DEFAULT_BASE_URL)
    configure.add_argument("--api-key", default="")
    configure.set_defaults(handler=command_configure)

    auth_send = subparsers.add_parser("auth-send")
    auth_send.add_argument("--base-url", default=DEFAULT_BASE_URL)
    auth_send.add_argument("--email", required=True)
    auth_send.set_defaults(handler=command_auth_send)

    auth_verify = subparsers.add_parser("auth-verify")
    auth_verify.add_argument("--base-url", default=DEFAULT_BASE_URL)
    auth_verify.add_argument("--email", required=True)
    auth_verify.add_argument("--code", required=True)
    auth_verify.add_argument("--label", default="Codex")
    auth_verify.set_defaults(handler=command_auth_verify)

    quota = subparsers.add_parser("quota")
    quota.set_defaults(handler=command_quota)

    search = subparsers.add_parser("search")
    search.add_argument("--request", default="")
    search.add_argument("--mode", choices=("lite", "keyword", "filtered_lite"), default="lite")
    search.add_argument("--query", default="")
    search.add_argument("--top-k", type=int, default=100)
    search.add_argument("--years", default="2000:2100")
    search.add_argument("--abstract-chars", type=int, default=0)
    search.add_argument("--result-view", choices=("titles", "standard"), default="titles")
    search.add_argument("--output", default="")
    search.set_defaults(handler=command_search)

    collect = subparsers.add_parser("collect")
    collect.add_argument("--query-spec", required=True)
    collect.add_argument("--checkpoint-dir", default="")
    collect.add_argument("--output", required=True)
    collect.add_argument("--workers", type=int, default=3)
    collect.add_argument("--retries", type=int, default=1)
    collect.set_defaults(handler=command_collect)
    return parser


def main():
    args = build_parser().parse_args()
    try:
        args.handler(args)
    except (RemoteError, ValueError) as exc:
        print(f"ChipSeeker error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

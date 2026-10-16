#!/usr/bin/env python3
"""Use the ChipSeeker remote retrieval API from a user-owned coding agent."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import ipaddress
import json
import math
import os
import random
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


DEFAULT_BASE_URL = "https://chipseeker.online"
CONFIG_PATH = Path.home() / ".chipseeker" / "config.json"
MAX_AUTO_RETRY_WAIT = 60


def _retry_after_seconds(value):
    try:
        seconds = float(value or 0)
    except (ValueError, TypeError):
        try:
            when = parsedate_to_datetime(str(value))
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return 0.0
    return max(0.0, seconds) if math.isfinite(seconds) else 0.0


class RemoteError(RuntimeError):
    def __init__(self, message, status=0, payload=None, retry_after=0):
        super().__init__(message)
        self.status = int(status or 0)
        self.payload = payload or {}
        self.retry_after = _retry_after_seconds(retry_after)


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
    saved_base_url = str(config.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    env_key = str(os.environ.get("CHIPSEEKER_API_KEY") or "").strip()
    config["base_url"] = str(os.environ.get("CHIPSEEKER_BASE_URL") or saved_base_url).rstrip("/")
    config["api_key"] = env_key or str(config.get("api_key") or "").strip()
    # A base-URL override alone must never rebind a saved credential.
    config["_api_key_base_url"] = config["base_url"] if env_key else saved_base_url
    return config


def _origin(url):
    message = "Use an HTTPS base URL (HTTP is allowed only for loopback development)."
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in url) or "\\" in url:
        raise ValueError(message)
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
        if not host or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError(message)
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError(message)
    except ValueError:
        raise ValueError(message) from None
    return parsed.scheme, host.lower(), port if port is not None else (443 if parsed.scheme == "https" else 80)


class _RejectRedirects(HTTPRedirectHandler):
    def http_error_302(self, req, fp, code, msg, headers):
        fp.close()
        raise RemoteError("ChipSeeker redirects are disabled; use the intended base URL directly.", code)

    http_error_301 = http_error_302
    http_error_303 = http_error_302
    http_error_307 = http_error_302
    http_error_308 = http_error_302


def save_config(base_url, api_key, email=""):
    base_url = str(base_url or DEFAULT_BASE_URL).rstrip("/")
    _origin(base_url)
    payload = {
        "base_url": base_url,
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
    if not path.startswith("/") or path.startswith("//") or "\\" in path or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in path):
        raise ValueError("Use an absolute API path, not a URL.")
    api_path = urlsplit(path).path.rstrip("/")
    is_auth = api_path == "/api/v1/auth" or api_path.startswith("/api/v1/auth/")
    api_key = "" if is_auth else str(api_key or "").strip()
    config = load_config() if not base_url or (not is_auth and not api_key) else {}
    base_url = str(base_url or config["base_url"]).rstrip("/")
    origin = _origin(base_url)
    if not is_auth and not api_key:
        api_key = config["api_key"]
        if api_key and origin != _origin(config.get("_api_key_base_url", config["base_url"])):
            raise ValueError("Stored/environment API credentials cannot be used for a different origin; configure it explicitly.")
    headers = {"Accept": "application/json", "User-Agent": "ChipSeeker-UltraSearch/1.0"}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = Request(f"{base_url}{path}", data=data, headers=headers, method=method.upper())
    try:
        with build_opener(_RejectRedirects()).open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            error_payload = json.loads(raw)
        except json.JSONDecodeError:
            error_payload = {"detail": raw or exc.reason}
        retry_after = _retry_after_seconds(exc.headers.get("Retry-After", 0))
        message = str(error_payload.get("detail") or exc.reason)
        if raw.lstrip().startswith("<"):
            message = f"HTTP {exc.code}: ChipSeeker gateway unavailable; retrying is safe with the same checkpoint."
        raise RemoteError(message[:2000], exc.code, error_payload, retry_after) from exc
    except URLError as exc:
        raise RemoteError(f"Cannot reach ChipSeeker: {exc.reason}") from exc
    except (TimeoutError, ConnectionError, HTTPException) as exc:
        raise RemoteError(f"ChipSeeker transport error: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RemoteError("ChipSeeker returned invalid JSON.", 502) from exc


def _backoff(error, attempt):
    return max(error.retry_after, min(30, 2 ** min(attempt + 1, 5)) + random.uniform(0, 0.5))


def remote_search(payload, timeout=900, poll_seconds=2.0, *, job_id="", on_state=None):
    payload = dict(payload)
    payload.setdefault("client_request_id", uuid.uuid4().hex)
    deadline = time.monotonic() + max(30, int(timeout))
    transport_failures = 0
    while time.monotonic() < deadline:
        try:
            if not job_id:
                submitted = api_request("POST", "/api/v1/search", payload=payload, timeout=60)
                job_id = str(submitted.get("job_id", "") or "")
                if not job_id:
                    if isinstance(submitted.get("results"), list):
                        return submitted
                    raise RemoteError("ChipSeeker submission omitted the job ID.", 502)
                if on_state:
                    on_state({"job_id": job_id, "status": submitted.get("status", "queued")})
            state = api_request("GET", f"/api/v1/search/jobs/{job_id}", timeout=60)
            transport_failures = 0
        except RemoteError as exc:
            if exc.status not in {0, 429, 500, 502, 503, 504} or transport_failures >= 4:
                raise
            delay = _backoff(exc, transport_failures)
            if exc.retry_after > MAX_AUTO_RETRY_WAIT or delay >= max(0, deadline - time.monotonic()):
                raise
            transport_failures += 1
            if on_state:
                on_state({"job_id": job_id, "status": "retrying", "http_status": exc.status, "retry_in_seconds": round(delay, 1)})
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
            continue
        status = str(state.get("status", "") or "")
        if on_state:
            on_state(state)
        if status == "done":
            result = state.get("result")
            if not isinstance(result, dict) or not isinstance(result.get("results"), list):
                raise RemoteError("Completed job has no valid result payload.", 502, state)
            return result
        if status == "error":
            message = str(state.get("error") or "ChipSeeker search failed.")
            status_code = 402 if "credits exhausted" in message.lower() else 503
            raise RemoteError(message, status_code, state)
        if status not in {"queued", "running"}:
            raise RemoteError(f"Unrecognized search job status: {status}", 502, state)
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

    def list_value(field):
        value = entry.get(field, defaults.get(field, [])) or []
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{field} must be a list, not a string or scalar.")
        return list(value)

    mode = str(entry.get("mode", defaults.get("mode", "lite")) or "lite").strip().lower().replace("-", "_")
    request = {
        "mode": mode,
        "query": str(entry.get("query", "") or "").strip(),
        "top_k": int(entry.get("top_k", defaults.get("top_k", 100)) or 200),
        "years": _years(entry.get("years", defaults.get("years", "2000:2100"))),
        "venues": list_value("venues"),
        "abstract_chars": int(entry.get("abstract_chars", defaults.get("abstract_chars", 0)) or 0),
        "result_view": str(entry.get("result_view", defaults.get("result_view", "titles")) or "titles"),
    }
    for field in ("must_have", "expression"):
        value = entry.get(field, defaults.get(field, ""))
        if value:
            request[field] = str(value)
    for field in ("fields", "all_terms", "any_terms", "exact_titles", "dois", "authors"):
        values = list_value(field)
        if values:
            request[field] = list(values)
    return request


def _normalize_doi(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value)
    return value.rstrip(".,; ")


def _normalize_title(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(char for char in value if char.isalnum())


def _identity(paper):
    doi = _normalize_doi(paper.get("doi", ""))
    if doi:
        return f"doi:{doi}"
    title = _normalize_title(paper.get("title", ""))
    year = str(paper.get("year", "") or "")
    return f"title-year:{title}|{year}"


def _rank_paper(paper):
    retrievals = [item for item in paper.get("retrievals", []) if item.get("purpose", "discovery") != "metadata"]
    semantic = [float(item.get("similarity", 0) or 0) for item in retrievals if item.get("mode") != "keyword"]
    best_similarity = max(semantic) if semantic else None
    family_count = max(1, len({item.get("query_family") or item.get("query_id") for item in retrievals}))
    corroboration = min(100.0, 50.0 + 15.0 * (family_count - 1))
    relevance = min(100.0, 85.0 * best_similarity + 0.15 * corroboration) if semantic else None
    venue_quality = float(paper.get("venue_quality", 50) or 50)
    try:
        year = int(re.search(r"\d{4}", str(paper.get("year", ""))).group())
    except (AttributeError, ValueError):
        year = 0
    current_year = datetime.now(timezone.utc).year
    recency = 0.0 if not year else max(40.0, 100.0 - 5.0 * max(0, current_year - year))
    return {
        "final_score": round(0.65 * (relevance or 0) + 0.25 * venue_quality + 0.10 * recency, 2),
        "relevance": round(relevance, 2) if relevance is not None else None,
        "best_lite_similarity": best_similarity,
        "relevance_basis": "semantic" if semantic else "literal_only_unrated",
        "venue_quality": round(venue_quality, 2),
        "recency": round(recency, 2),
        "query_corroboration": round(corroboration, 2),
        "venue_tier": str(paper.get("venue_tier", "C") or "C"),
    }


def merge_responses(responses, attempted):
    responses = list(responses)
    merged = {}
    raw_count = 0
    failed = []
    pending = []
    searches = []
    last_quota = {}
    title_dois = {}
    for response in responses:
        if response.get("status", "completed") != "completed":
            continue
        for item in response.get("results", []):
            doi = _normalize_doi(item.get("doi"))
            title_key = (_normalize_title(item.get("title")), str(item.get("year", "") or ""))
            if doi and title_key[0]:
                title_dois.setdefault(title_key, set()).add(doi)
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
                "purpose": entry.get("purpose", "discovery"),
                "http_status": response.get("http_status"),
                "retry_not_before": response.get("_retry_not_before"),
                "job_id": response.get("_client_job_id"),
                "candidate_count": response.get("candidate_count"),
                "matched_count": response.get("matched_count"),
                "possibly_truncated": int(response.get("result_count", 0) or 0) >= int(entry.get("top_k", 100)),
            }
        )
        if status != "completed":
            (pending if status in {"pending", "queued", "running"} else failed).append(query_id)
            continue
        last_quota = response.get("quota") or last_quota
        for item in response.get("results", []):
            raw_count += 1
            key = _identity(item)
            if not item.get("doi"):
                title_key = (_normalize_title(item.get("title")), str(item.get("year", "") or ""))
                candidates = title_dois.get(title_key, set())
                if len(candidates) == 1:
                    key = "doi:" + next(iter(candidates))
            if key not in merged:
                merged[key] = dict(item)
                merged[key]["retrievals"] = []
            target = merged[key]
            for field, value in item.items():
                if field not in {"rank", "similarity", "retrievals", "abstract", "abstract_kind", "abstract_truncated"} and value and not target.get(field):
                    target[field] = value
            if len(str(item.get("abstract", "") or "")) > len(str(target.get("abstract", "") or "")):
                target["abstract"] = item["abstract"]
                target["abstract_kind"] = item.get("abstract_kind", "source_abstract")
                target["abstract_truncated"] = item.get("abstract_truncated", False)
            merged[key]["retrievals"].append(
                {
                    "query_id": query_id,
                    "query_family": entry.get("query_family", ""),
                    "mode": entry.get("mode", "lite"),
                    "rank": item.get("rank"),
                    "similarity": item.get("similarity"),
                    "purpose": entry.get("purpose", "discovery"),
                }
            )
    papers = list(merged.values())
    for paper in papers:
        paper["retrieval_count"] = len(paper["retrievals"])
        paper["retrieval_family_count"] = len(
            {item.get("query_family") or item.get("query_id") for item in paper["retrievals"] if item.get("purpose") != "metadata"}
        )
        paper["ranking"] = _rank_paper(paper)
    papers.sort(key=lambda item: item["ranking"]["final_score"], reverse=True)
    return {
        "schema": "chipseeker-remote-collect/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "running" if pending else "partial" if failed else "completed",
        "summary": {
            "attempted_queries": len(attempted),
            "successful_queries": sum(s["status"] == "completed" for s in searches),
            "failed_queries": len(failed),
            "pending_queries": len(pending),
            "raw_results": raw_count,
            "unique_papers": len(papers),
            "successful_discovery_queries": sum(s["status"] == "completed" and s["purpose"] != "metadata" for s in searches),
            "successful_metadata_queries": sum(s["status"] == "completed" and s["purpose"] == "metadata" for s in searches),
            "discovery_raw_results": sum(s["result_count"] for s in searches if s["status"] == "completed" and s["purpose"] != "metadata"),
            "metadata_raw_results": sum(s["result_count"] for s in searches if s["status"] == "completed" and s["purpose"] == "metadata"),
        },
        "failed_queries": failed,
        "pending_queries": pending,
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
    entry["top_k"] = request["top_k"]
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
        if float(existing.get("_retry_not_before", 0) or 0) > time.time():
            existing["_entry"] = entry
            return existing
    request_id = existing.get("_client_request_id") or uuid.uuid4().hex
    request["client_request_id"] = request_id
    checkpoint_metadata = {"_client_request_id": request_id, "_request_fingerprint": fingerprint}
    if existing.get("_client_job_id"):
        checkpoint_metadata["_client_job_id"] = existing["_client_job_id"]

    last_notice = [None, 0.0]

    def on_state(state):
        if state.get("job_id"):
            checkpoint_metadata["_client_job_id"] = state["job_id"]
        status = state.get("status", "pending")
        if checkpoint:
            _write_json(checkpoint, {"status": "pending", "remote_status": status, **checkpoint_metadata})
        now = time.monotonic()
        if status != last_notice[0] or now - last_notice[1] >= 30:
            print(json.dumps({"query_id": query_id, "status": status, "position": state.get("position", 0), "retry_in_seconds": state.get("retry_in_seconds")}), file=sys.stderr, flush=True)
            last_notice[:] = [status, now]
    if checkpoint:
        _write_json(checkpoint, {"status": "pending", **checkpoint_metadata})
    last_error = None
    for attempt in range(max(0, retries) + 1):
        try:
            response = remote_search(request, job_id=checkpoint_metadata.get("_client_job_id", ""), on_state=on_state)
            response["status"] = "completed"
            response["_entry"] = entry
            response.update(checkpoint_metadata)
            if checkpoint:
                _write_json(checkpoint, {key: value for key, value in response.items() if key != "_entry"})
            return response
        except RemoteError as exc:
            last_error = exc
            if exc.payload.get("status") == "error":
                checkpoint_metadata.pop("_client_job_id", None)
            if exc.status in {400, 401, 402, 403} or exc.retry_after > MAX_AUTO_RETRY_WAIT or attempt >= retries:
                break
            time.sleep(_backoff(exc, attempt))
    failed = {
        "status": "failed",
        "result_count": 0,
        "results": [],
        "error": str(last_error or "Unknown error"),
        "http_status": getattr(last_error, "status", 0),
        "retry_after_seconds": getattr(last_error, "retry_after", 0),
        "_entry": entry,
        **checkpoint_metadata,
    }
    if failed["retry_after_seconds"]:
        failed["_retry_not_before"] = time.time() + failed["retry_after_seconds"]
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
    printed = response if not args.output else {"output": str(args.output), "result_count": response.get("result_count"), "quota": response.get("quota")}
    print(json.dumps(printed, ensure_ascii=False))


def command_collect(args):
    spec = _read_json(args.query_spec, {})
    entries = list(spec.get("queries", []))
    if not entries:
        raise ValueError("The query spec contains no queries.")
    ids = [str(entry.get("id", "")) for entry in entries if isinstance(entry, dict)]
    if len(ids) != len(entries) or len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", query_id) for query_id in ids):
        raise ValueError("Every query needs a unique ID containing only letters, digits, dots, underscores or hyphens.")
    if any(entry.get("purpose", "discovery") not in {"discovery", "metadata"} for entry in entries):
        raise ValueError("purpose must be discovery or metadata.")
    defaults = dict(spec.get("defaults", {}))
    for entry in entries:
        normalize_request(entry, defaults)
    workers = max(1, min(int(args.workers), 4, len(entries)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_run_entry, entry, defaults, args.checkpoint_dir, args.retries): index for index, entry in enumerate(entries)}
        responses = [None] * len(entries)
        for future in concurrent.futures.as_completed(futures):
            index = futures[future]
            responses[index] = future.result()
            done = [response for response in responses if response is not None]
            snapshot = [response if response is not None else {"status": "pending", "_entry": entries[i], "result_count": 0, "results": []} for i, response in enumerate(responses)]
            _write_json(args.output, merge_responses(snapshot, entries))
            response = responses[index]
            print(json.dumps({"query_id": entries[index]["id"], "status": response["status"], "results": response.get("result_count", 0), "completed": len(done), "total": len(entries)}), file=sys.stderr, flush=True)
    merged = merge_responses(responses, entries)
    active_cooldown = any(float(response.get("_retry_not_before", 0) or 0) > time.time() for response in responses)
    if not active_cooldown:
        try:
            merged["quota"] = api_request("GET", "/api/v1/quota").get("quota", merged["quota"])
        except RemoteError:
            pass
    _write_json(args.output, merged)
    print(json.dumps(merged["summary"], ensure_ascii=False))
    if merged["failed_queries"]:
        print("Retry query IDs: " + ", ".join(merged["failed_queries"]), file=sys.stderr)
        return 2
    return 0


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
        return int(args.handler(args) or 0)
    except (RemoteError, ValueError) as exc:
        print(f"ChipSeeker error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

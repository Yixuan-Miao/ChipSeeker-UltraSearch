# ChipSeeker Remote Access

## Authentication

The client stores its configuration at `~/.chipseeker/config.json`. `CHIPSEEKER_API_KEY` and `CHIPSEEKER_BASE_URL` override the saved values for the current process.

```powershell
python <skill-dir>\scripts\chipseeker_remote.py configure --base-url https://chipseeker.online --api-key <key>
python <skill-dir>\scripts\chipseeker_remote.py auth-send --email <email>
python <skill-dir>\scripts\chipseeker_remote.py auth-verify --email <email> --code <six-digits>
python <skill-dir>\scripts\chipseeker_remote.py quota
```

Do not print, summarize, or save the API key outside the client configuration. If the server reports an invalid or revoked key, ask the user for a replacement key or repeat the email verification flow.

The API key is created at account registration. Email verification retrieves that same key; it does not issue additional keys. Users can view or reset it at `/api-access`. Resetting invalidates the old key while preserving email-owned API credits and usage history.

`quota` returns `credits_total`, `credits_used`, `credits_reserved`, and `credits_left`. API credits are recharged by the administrator to the registered email, independent of website membership. Each successful retrieval costs one credit, including zero-result queries. Failed retrievals, login, quota checks, and progress polling are free.

## Single Search

Write a JSON request and pass it to `search --request`. Supported modes are `lite`, `keyword`, and `filtered_lite`.

```json
{
  "mode": "lite",
  "query": "cryogenic InP low-noise amplifier for qubit readout",
  "top_k": 150,
  "years": [2000, 2026],
  "venues": [],
  "abstract_chars": 0,
  "result_view": "titles"
}
```

Keyword and filtered Lite also accept `fields`, `all_terms`, `any_terms`, `exact_titles`, `dois`, and `authors`. Filtered Lite requires a semantic `query` plus at least one structured constraint. The remote API returns at most 200 papers per query.

## Multi-query Collection

The remote collector accepts the same query-spec shape described in [query-spec.md](query-spec.md), except remote UltraSearch does not use Pro. It runs up to four searches concurrently, writes one checkpoint per query ID, and merges results by DOI first and normalized title plus year second.

```powershell
python <skill-dir>\scripts\chipseeker_remote.py collect `
  --query-spec <workspace>\queries.json `
  --checkpoint-dir <workspace>\checkpoints `
  --output <workspace>\union.json `
  --workers 3 `
  --retries 1
```

Inspect `failed_queries`, `retry_query_ids`, `searches`, and `quota` in `union.json`. Keep the same checkpoint directory when appending a follow-up round. A completed query ID is reused; a failed query is retried. Use a new ID whenever the query meaning changes.

## Error Handling

- `400`: invalid request; fix the query spec rather than retrying unchanged.
- `401`: missing, revoked, or invalid API key; reconfigure or authenticate by email.
- `402`: API credits exhausted; report the balance and ask the user to contact the administrator for an email-account recharge. Do not keep retrying.
- `429`: quota exhausted or server busy. Check `quota`; retry later only for server-busy responses.
- `503`: retrieval failed. Keep the checkpoint and retry the same query.

Do not interpret a failed query as evidence that no relevant paper exists.

Search submission accepts a `client_request_id`. Reuse it only when resuming or retrying the same request. Completed requests return their saved result without another charge, including after a server restart. The collector saves its request ID before submitting a query. New workspaces generate new request IDs so a deliberate fresh search sees the current corpus.

---
name: chipseeker-ultra-search
description: Use the ChipSeeker remote IC-paper API from the user's Codex or Claude Code. Expand research terminology, repeatedly search Lite and structured keywords, deduplicate and screen titles and abstracts, and save a precise, high-recall paper report to the requested folder.
---

# ChipSeeker UltraSearch

Use the user's own AI to plan, iterate, screen, and write reports. ChipSeeker supplies remote retrieval results at `https://chipseeker.online`. This Skill works without a local ChipSeeker installation or paper database.

## Connect

The client is `scripts/chipseeker_remote.py`, relative to this Skill directory. Use Python 3; no third-party packages are needed. Read [references/remote-api.md](references/remote-api.md) for API details and troubleshooting.

When a key is provided, configure it immediately, then check `quota`. A process environment variable `CHIPSEEKER_API_KEY` can pass the key without putting it in a command-line argument:

```text
python CLIENT configure --base-url https://chipseeker.online
python CLIENT quota
```

The client also accepts `configure --api-key KEY`. Never echo the key or write it into a research folder, report, query spec, or Git repository. The client stores credentials in `~/.chipseeker/config.json`.

When the user provides only email:

1. Run `auth-send --email EMAIL`.
2. Ask the user for the six-digit verification code.
3. Run `auth-verify --email EMAIL --code CODE`.

Each email has one API key, generated at registration. Email login retrieves that existing key. Read `credits_left`, `credits_reserved`, and `credits_used` from `quota`. New accounts have zero API credits; the administrator recharges the email account. Website membership does not grant unlimited API use.

Each successful Lite, Keyword, or filtered-Lite retrieval costs one API credit, including zero-result queries. Failed retrievals, login, quota checks, polling, and local processing cost no credits. If credits are exhausted, report it and stop making billable requests until the user recharges.

## Interpret the Research Target

Extract the target circuit or system, mandatory constraints, optional preferences, acceptable adjacent terminology, and exclusions. Respect the user's technology, frequency, temperature, venue, year, and application limits. Do not silently turn preferences into hard filters.

UltraSearch finds papers. Do not add unsolicited idea generation, benchmarking, full-text analysis, fixed result categories, or project-management documents.

## Search Loop

1. Create materially different query families: direct specifications, acronyms and expanded names, technologies, applications, circuit mechanisms, operating conditions, and receiver/front-end/system terms when the target block may be embedded.
2. Start with several broad Lite searches. Do not generate a Cartesian product of all keywords. Use 100-200 candidates per query, `result_view: titles`, and `abstract_chars: 0` for low-token discovery.
3. Run a structured plan using the client:

```text
python CLIENT collect --query-spec WORKSPACE/queries.json --checkpoint-dir WORKSPACE/checkpoints --output WORKSPACE/union.json --workers 3
```

Read [references/query-spec.md](references/query-spec.md) for the query-plan format. The collector merges by DOI, then normalized title plus year, and preserves retrieval evidence.

4. Screen titles first. Remove obvious false positives while preserving uncertain candidates. Use the user's inclusion criteria, not forced categories.
5. For uncertain titles, use a selective Keyword query with `dois` or `exact_titles`, `result_view: standard`, and `abstract_chars: 6000` to retrieve source abstracts. Batch identity selectors when useful; each lookup costs one API credit.
6. Expand from retained titles and abstracts: recover missing terminology, process aliases, design techniques, application phrases, or embedded receiver descriptions. Use selective Keyword or filtered Lite when exact authors, terms, or identities are useful.
7. Append the new queries to the plan and rerun with the same checkpoint folder. Successful checkpoints are reused. Each changed query needs a new ID. Transport retries reuse their saved `client_request_id`, so they do not charge twice; a fresh workspace starts a fresh search against the current library.

Default to Lite. The remote API does not require Pro or a server-funded LLM. A conference/journal follow-up or author-series search is optional when the results expose a likely useful lead. External web searches are selective and should add only papers that strongly match the user's target or address a suspected corpus gap.

## Stopping and Quality

Failed queries are not zero-yield evidence. Inspect `failed_queries`, `retry_query_ids`, and `searches`; retry correctable failures and explicitly report unresolved ones.

Stop when late, materially different successful query families add no new retained publication after deduplication, newly discovered terminology has been searched, and the remaining candidates have been screened according to the user's scope. Two independent late searches with no new retained publication are useful evidence of saturation; raw candidate counts alone are not.

Do not claim absolute completeness. Report the practical scope and any remaining failures or result truncation. Query `coverage` labels describe search intent, not proof of completeness.

Rank retained publications by relevance, venue tier/quality, publication year, and corroboration from independent query families. The collector's score is an ordering aid, not a measure of scientific validity. Multiple cosmetic queries do not provide independent corroboration.

The API returns at most 200 candidates per query; query families and filters should recover missed directions. Keep the final list at no more than 200 retained papers unless the user asks otherwise. If a scope remains too broad, prefer the newest publication year and disclose the restricted output window.

## Files and Report

Use the exact destination requested by the user. If none is given, choose a timestamped topic folder in the current workspace. Keep `queries.json`, `checkpoints/`, `union.json`, and the requested final Markdown/JSON files. Avoid generic briefs, diaries, placeholder files, and private developer paths.

Write the user-facing report in Chinese by default, preserving English paper titles and technical terms. For each retained paper include title, authors, year, venue/tier, DOI/source/PDF links, relevance/ranking evidence, and one short Chinese sentence explaining the match. Include a compact search summary with successful/failed query counts, raw/unique/retained papers, retry status, truncation, and the stopping reason.

Never invent a paper, abstract, metric, or URL. Distinguish source abstracts from AI summaries. Do not automatically download PDFs or promise institutional PDF access. If the user asks for a local-PDF navigation column, link only files that actually exist. A paper the user has explicitly deleted stays marked as deleted and is not downloaded again automatically.

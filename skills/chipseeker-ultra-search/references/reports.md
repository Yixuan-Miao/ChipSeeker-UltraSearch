# Research Folder and Paired Reports

The user's path is the parent, not a dumping ground for individual result files. Create a short topic subfolder first. Keep only the two human reports and one AI-data folder at its top level:

```text
USER_PARENT/
  芯寻_<concise-topic>/
    结果报告.md
    结果报告.html
    检索数据/
      papers.json
      queries.json
      union.json
      quota.json
      checkpoints/
```

Use the user's actual research terms in the topic name. Summarize them naturally in Chinese or mixed Chinese/English. Do not invent a process or frequency. Chinese filenames are intentional interface labels; all operating instructions remain English. A new run gets a timestamp suffix if this name already exists; do not silently overwrite or merge distinct searches.

## Initialize and Search

`OUTPUT` below is `scripts/research_output.py` in this Skill; `CLIENT` is `scripts/chipseeker_remote.py`. Both use only the Python standard library.

```text
python OUTPUT init --parent USER_PARENT --name CONCISE_TOPIC
```

Use the returned workspace/data paths, not a guessed folder name. `init --resume` is only for explicitly continuing the existing topic run. Put the original research direction and search map inside `检索数据/queries.json`; do not add a separate project brief.

```text
python CLIENT collect --query-spec WORKSPACE/检索数据/queries.json --checkpoint-dir WORKSPACE/检索数据/checkpoints --output WORKSPACE/检索数据/union.json --workers 3
```

## Curated Paper Data

The collector's union is a candidate pool, **not** a reviewed reading list. The AI must screen it, hydrate plausible abstracts, rank retained papers and write `检索数据/papers.json`. The renderer does not perform semantic screening or invent missing reasons.

```json
{
  "schema": "ultrasearch-screened-papers/v1",
  "generated_at": "YYYY-MM-DDTHH:MM:SS+00:00",
  "summary": {
    "research_direction": "the user's original direction",
    "status": "completed or partial",
    "successful_queries": 0,
    "failed_queries": 0,
    "unique_papers": 0,
    "unresolved_query_ids": [],
    "stopping_reason_zh": "Evidence-based stopping reason, not an absolute completeness claim.",
    "quota_at_start": {"credits_used": 0},
    "quota_at_finish": {"credits_used": 0, "credits_left": 0}
  },
  "report_summary_zh": "A short Chinese conclusion about the actual search result and its limits.",
  "report_notes_zh": ["Optional material scope or provenance notes."],
  "papers": [
    {
      "title": "Exact English paper title",
      "authors": ["Author"],
      "year": "2025",
      "venue": "Full venue name",
      "venue_name": "Venue abbreviation",
      "importance_stars": 5,
      "match_description_zh": "Precise type of relevance, without forcing categories",
      "why_keep_zh": "Short Chinese explanation of why this is worth reading.",
      "evidence_limit_zh": "What is and is not confirmed, when material.",
      "doi": "",
      "doi_link": "",
      "primary_source": "",
      "pdf_link": "",
      "open_pdf_link": "",
      "local_pdf_path": null,
      "abstract": "Retrieved source abstract; never fabricate one.",
      "abstract_kind": "source_abstract",
      "full_text_read": false,
      "origin": "chipseeker_remote_api",
      "source_in_current_corpus": true,
      "retrievals": []
    }
  ],
  "supplementary_reading": [],
  "screening_decisions": []
}
```

Keep all useful API metadata, evidence and screening decisions in this file even if the HTML does not display them. Unknown fields/metrics stay unknown. Mark externally verified papers with `origin: external_primary_source` and `source_in_current_corpus: false`; give a primary source. A discovered reference is not automatically an externally read full text.

For an actual local PDF, set its absolute path or a path relative to the topic folder. The renderer links it only if that PDF exists. Never link guessed downloaded paths. For supplementary theses/reports, include `title`, `year`, `authors`, `source`, `pdf` and `why_keep_zh`, and do not count them as additional peer-reviewed demonstrations.

## Render and Check

```text
python OUTPUT render --workspace WORKSPACE
```

This writes `结果报告.md` and `结果报告.html` from the same curated paper list and evidence. The HTML includes offline search over titles/authors/abstracts, venue/priority/source filters, sort controls, source/PDF links and expandable source abstracts. The existing ChipSeeker logo from `assets/chipseeker_logo.jpg`, styling and scripts are embedded; opening the HTML directly is enough, even after copying it without the Skill assets. No app installation, CDN or dev server is needed.

Existing reports are protected by default. Use `--overwrite` only for an intended regeneration after checking for user edits; otherwise back up or incorporate those edits first. If the user deliberately edits the report, do not erase their work just to re-render.

Use `status: partial` whenever a budget, outage or time limit leaves material planned research unfinished, even when `failed_queries` is zero. Put the unresolved research branches and user-authorized limits in the summary/notes, separately from failed transport query IDs. Do not call a sparse but successfully screened direction a service failure. For a deliberately bounded trial, state its representative scope rather than implying exhaustive coverage.

Field names ending in `_zh` are fixed renderer keys. Fill their user-facing values in Chinese; do not translate or rename the keys. Preserve English publication titles and source abstracts. Explain uncertain or supporting matches individually without imposing fixed report categories.

`quota_at_start` and `quota_at_finish` are account-wide snapshots. Recharges affect available balance, while other sessions can change `credits_used`. Reconcile their difference with the run's completed request IDs, free checkpoint reuse and pending jobs. If the numbers cannot be attributed to this run, say so explicitly; do not describe a shared-account delta as an exact per-run invoice.

The renderer labels this delta as account usage growth during the search period, not an attributable run invoice. Use a short report note for independently confirmed request counts and any shared-account discrepancy. Do not alter quota snapshots to force the numbers to agree.

Check retained-paper counts, partial status, visible links and mobile text wrapping. Do not claim that generating a prettier report performed new searches. Use an available browser tool for a focused interaction check when practical, not an elaborate visual-validation project.

When migrating an old flat run, move only the identified run files into the new topic folder's `检索数据/`. Preserve the previous report there as `原始报告.md`, then produce the paired reports. Leave unrelated files in the user's parent folder alone. Validate resolved paths before moving a directory and never overwrite a previous run.

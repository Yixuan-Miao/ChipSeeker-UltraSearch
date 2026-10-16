# Remote Query Plan

```json
{
  "schema": "chipseeker-agent-query-spec/v1",
  "defaults": {
    "years": "2000:2100",
    "abstract_chars": 0,
    "result_view": "titles",
    "top_k": 150
  },
  "queries": [
    {
      "id": "direct",
      "mode": "lite",
      "query": "cryogenic InP 4-8 GHz low-noise amplifier",
      "query_family": "direct-specification"
    },
    {
      "id": "readout",
      "mode": "lite",
      "query": "cryogenic low-noise front end for superconducting qubit readout",
      "query_family": "application-context"
    },
    {
      "id": "literal-inp-lna",
      "mode": "keyword",
      "all_terms": ["InP"],
      "any_terms": ["LNA", "low-noise amplifier"],
      "fields": ["title", "abstract", "keywords", "ieee_terms"],
      "query_family": "literal-circuit",
      "top_k": 200
    }
  ]
}
```

Modes: `lite`, `keyword`, `filtered_lite`. `filtered_lite` requires a semantic `query` plus structured selectors. Use `all_terms`, `any_terms`, `exact_titles`, `dois`, and `authors` for literal constraints. Query text in `keyword` mode uses the keyword engine's expression syntax; exact identities should use structured selectors instead.

`years` accepts `[START, END]` or `"START:END"` in the client. `top_k` is limited to 200 results per query. `venues` and `fields` are arrays. A `standard` response may include up to 6000 source-abstract characters per paper.

Give every query a unique ID. Use a new ID when its meaning changes. Append new queries to the same plan to preserve the union and checkpoints across rounds.

The collector emits `papers`, `searches`, `summary`, `failed_queries`, `retry_query_ids`, and `quota`. Each paper retains its retrieval sources and ranking components. Screen the resulting metadata using the user's scope; failed queries do not establish literature coverage or absence.

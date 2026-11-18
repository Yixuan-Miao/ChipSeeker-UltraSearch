# Remote Query Plan

```json
{
  "schema": "chipseeker-agent-query-spec/v1",
  "scope": {"direction": "cryogenic InP amplifiers for qubit readout", "search_goal": "coverage"},
  "defaults": {
    "years": "1800:2100",
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

Use the user's year interval when specified. For broad discovery, set a wide interval explicitly as above to avoid the client's omitted-year fallback. The optional focused circuit-review profile uses the ten years preceding the search date for main-list discovery unless the user specifies another range. A venue or year discovery filter should not carry into an unrestricted identity check.

Give every query a unique ID. Use a new ID when its meaning changes. Append new queries to the same plan to preserve the union and checkpoints across rounds.

Keep the plan at `TOPIC_WORKSPACE/检索数据/queries.json`, the union at `检索数据/union.json`, and the checkpoints at `检索数据/checkpoints/`. The requested output path is the parent of a new `芯寻_简练方向名` topic folder; initialize it with `scripts/research_output.py` as described in [reports.md](reports.md).

Store the agreed objective in `scope.search_goal`: `coverage`, `precision`, `balanced` (both or a bounded compromise), or `representative` (quick trial). Keep it on resume unless the user changes the objective. Record a compact `scope`, `search_map`, query `rationale` and observed `findings` as useful. These local fields are not sent to the API and do not change billing or choose a report admission profile. The reports show the recorded objective. See [search-strategy.md](search-strategy.md).

`max_api_requests` optionally caps the total number of registered query IDs in a plan, including pending, hydration and scope probes. `collect` rejects an oversized plan before sending anything, but still runs every unfinished query in an allowed plan. Other descriptive budget fields are not executable limits. Append only the next authorized batch; preserve previous entries and checkpoints.

Set `purpose: discovery` (the default) for paper discovery and `purpose: metadata` for DOI/title abstract hydration. This field stays local: it does not change server retrieval or billing. Metadata lookups enrich the retained record but do not count as independent discovery evidence. Both kinds of successful requests cost one credit, even with zero results.

For identity hydration, batch up to 50 DOI values or 20 exact titles per request, within the 200-result cap. Verify selector semantics before combining unrelated literal constraints: `all_terms` are requirements, while `any_terms` are alternatives. Missing identity results may reflect filters, alternate metadata or absent indexing; they are not proof that a paper is fictitious.

The collector emits `papers`, `searches`, `summary`, `failed_queries`, `retry_query_ids`, and `quota`. Saved searches include effective bounds and an `identity_lookup` marker for Keyword DOI and exact-title selectors. A zero result means no visible record under those bounds, not a statement about the whole literature. Each paper retains its retrieval sources and ranking components. Screen the resulting metadata using the user's scope; failed queries do not establish literature coverage or absence.

The union is saved after every finished query. Partial runs set `status: partial` and exit with code 2 while preserving usable data. Progress appears on stderr; full paper metadata stays in the JSON file. Inspect `successful_discovery_queries`, `successful_metadata_queries`, `discovery_raw_results`, `metadata_raw_results` and `possibly_truncated` in addition to total counts.

Some responses identify one requested data rate in `target_rate_gbps` and classify the available title or abstract evidence in `rate_evidence`. When all completed discovery queries identify the same rate, the union places records reporting operation at that rate (`operating`) ahead of records citing it for comparison (`comparison`) within the classified positions. Unclassified and conflicting records retain their original positions. Semantic similarity and ranking scores remain available for inspection. These labels describe the retrieved metadata. Use the source paper to verify measurements and supported modes.

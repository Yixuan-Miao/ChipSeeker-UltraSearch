# Knowledge-Guided Adaptive Search

## Clarify Broad Prompts Before Expansion

The user's AI owns this conversational gate; the API remains a retrieval tool. Do not reject broad queries at the API layer: a single Lite query is useful for a trial or scope probe, even when a full UltraSearch would be premature.

Before creating the multi-query plan, decide whether the prompt identifies a meaningful research subdirection. A bare block/acronym ("ADC", "CTLE") or generic qualifiers ("recent ADC", "good CTLE papers", "JSSC ADC") usually do not. Judge the technical scope, not word count or a fixed keyword blacklist. An application plus a concrete challenge, or an architecture plus a specific design problem, often suffices; do not require frequency, process, year and numerical specifications all at once.

Ask one concise, decision-changing question at a time, offering a few technically useful examples without choosing for the user. For ADC, ask about architecture or the problem/application: noise-shaping SAR, high-speed time-interleaved conversion, or low-power sensor interfaces. For CTLE, ask about the design objective and context: adaptive equalization, low-power SerDes, or linearity/bandwidth co-design. If the answer is still broad, follow up on the remaining ambiguity. A direction such as "pulsed cryogenic LNA for qubit readout" is already actionable; do not block it on unspecified process or band.

When breadth is uncertain or the user needs examples, optionally make one Lite scope probe, saving it as a discovery query with a scope-probe rationale. Inspect the diversity of retrieved titles and selected abstracts, then use those actual themes to ask a better question. Do not launch the normal 6-10-query opening while the scope remains unresolved. Record the probe's credit use and reuse its results after clarification. A result count equal to `top_k` only indicates a filled candidate page, not a total relevant-paper count or proof that the topic is too broad; low counts alone do not prove the scope is precise either.

Continue clarification until the user chooses a useful subdirection. If they do not know which to choose, offer evidence-grounded options. If they explicitly want a quick software test, general overview, or no questions, honor that with a bounded trial/representative search and disclose its limits. Do not trap them in an endless interview or pretend a broad trial has exhausted the literature. Never silently impose a latest-year or technology restriction merely to make an undefined topic fit the output cap.

## Interpret the Research Problem

UltraSearch replaces the user's incomplete keyword planning with the user's AI's domain understanding. The goal is high-recall, precise paper discovery, not merely running a query list. The AI chooses, revises and retires queries; ChipSeeker does not decide the overall research strategy.

Expand the user's intent into distinct facets where useful:

- Circuit/system and embedded forms: LNA, receiver, readout front end, readout IC or SoC.
- Physical mechanism or implementation: transistor bias, supply gating, feedback, matching, switching, bias network, heterogeneous integration.
- Problem/metric: noise, power, recovery, start-up, settling, stability, thermal load, interference or backaction.
- Operating context: temperature, frequency, technology, timing and application. Do not assume an unspecified material or band is a hard constraint.
- Vocabulary: expanded names, acronyms, hyphenation, older terms and terminology used by adjacent communities.
- Measurement evidence and likely precursors, when they bear directly on the user's target.

Do not require every desired property in every query. Overloaded descriptions can bury a relevant paper that uses different language. Alternate precise intent queries with broader mechanism/application queries. If broadening a mandatory property for discovery, retain only papers meeting it or clearly label why a supporting paper is useful.

Resolve consequential polysemy before spending on expansion: a block name, acronym or application term can denote different architectures or communities. Use the user's context and retrieved evidence, not the first plausible expansion. A focused request does not need an interview merely because some numerical specifications are unstated.

Choose explicit date and venue filters consistent with the user's scope. The client's historical year default starts at 2000; it is not a user exclusion of earlier work. Widen it when foundational or predecessor literature may matter. For identity verification, avoid retaining discovery-only venue/year/technology restrictions that could hide the very record being checked.

## Layout, Observe, Expand

For an open research direction, a useful opening round often has 6-10 distinct Lite queries. Adjust this to the target rather than treating it as a quota. Exact-paper lookup does not require such a broad opening. Several queries differing only in word order count as one angle, not an intelligently covered direction.

Keep the search map inside `检索数据/queries.json`, for example:

```json
{
  "scope": {
    "direction": "the user's original words",
    "required": ["actual mandatory constraints"],
    "preferences": ["soft preferences"],
    "open_leads": ["specific unsolved terminology or evidence gaps"]
  },
  "search_map": [
    {
      "facet": "bias switching and recovery",
      "rationale": "Recover papers not explicitly calling the circuit pulsed.",
      "query_ids": ["bias-recovery"],
      "findings": "Short evidence-based note after the round."
    }
  ],
  "queries": [
    {
      "id": "bias-recovery",
      "mode": "lite",
      "query": "fast settling switched bias cryogenic low-noise amplifier",
      "query_family": "bias-recovery",
      "rationale": "Tests turn-on behavior rather than repeating the application phrase."
    }
  ]
}
```

After each useful round:

1. Screen titles, keeping plausible but ambiguous candidates.
2. Hydrate selected abstracts in batches with `purpose: metadata`. Inspect source abstract quality and truncation; do not equate a missing abstract with irrelevance.
3. Extract newly observed search terms, circuit mechanisms, limiting phenomena, application roles and authors. A useful unexpected hit should influence the next round.
4. Follow unresolved leads with new Lite angles; use Keyword for precise identities, authors or vocabulary. Track new retained papers after deduplication, not raw result count.
5. Revisit a narrow initial interpretation when a broader query returns genuinely useful papers; do not silently relax hard constraints.

Keep a small branch record: retrieval question, tested query IDs, new useful evidence, unresolved lead, and next action or reason to stop. A branch marked tested is not necessarily resolved. Prefer the next query that could expose a distinct missing mechanism or correct a plausible misunderstanding. Skip variants that merely reshuffle the same terms without testing another question.

Do not preload speculative follow-ups into the executable plan. The collector runs all unfinished entries; it does not pause for semantic review or enforce a budget stored in descriptive metadata. Add only the next justified batch. Under a hard budget, account for pending submissions and allow for paid abstract hydration before launching more discovery. Do not spend the entire allowance on title-only retrievals and leave no way to screen ambiguous matches.

## Screening and Ranking Decisions

Apply eligibility before preference ranking. A directly relevant older circuit paper can outrank a new high-tier system paper that does not address the target circuit. Venue tier and year are useful priorities, not proof of relevance, validation or correctness. Keep a justified weak-venue match when it supplies unique target evidence; do not pad the list with prestigious but generic references.

Lite similarity is a retrieval signal, not a calibrated probability, and query families can have different score distributions. Do not adopt one universal cutoff or call every result above it a confirmed match. Inspect a diverse sample of promising and borderline titles, then hydrate plausible uncertain candidates. Literal `similarity=1` is not measured semantic certainty. Multiple paraphrases and metadata lookups must not inflate independent corroboration.

For each decisive inclusion or exclusion, identify the property actually supported by available metadata. Distinguish demonstrated operation from a proposed use, measured hardware from simulation, static operation from duty cycling, and a control signal from a switched signal path. Do not demand full-text metrics merely to discover papers, but do not invent them from abstracts. Missing evidence can justify an uncertainty note rather than automatic rejection.

For duplicate conflicts, preserve the raw records and inspect DOI, title, authors, year and publication status. Same title plus different DOIs can represent distinct publications; an online-first/print year difference is not by itself evidence of a different experiment. Label related versions only when supported, without imposing a large work-family analysis or merging them indiscriminately.

## Coverage and Failure Decisions

Assess coverage against the chosen research questions, not the number of papers or calls. A capped result page means the returned list may be truncated, not that every returned paper is relevant. Where useful, split a promising capped branch by mechanism, implementation or time window; do not split every full page automatically. A final top-10 request limits the reading list, not discovery to ten initial hits.

An exact-title or DOI query returning zero establishes only that this lookup did not retrieve the record under those selectors and filters. Check both identifiers, normalization and overly restrictive filters before reporting a suspected corpus omission. It does not prove the publication does not exist. Negative discovery results are even weaker evidence when vocabulary, indexing or source abstracts may be incomplete.

Separate three outcomes: planned requests completed; the scoped search reached practical diminishing returns; or work stopped because of a budget, outage or time limit. The collector can establish the first, not the second. Preserve unresolved branch IDs and meaningful deferred leads for a partial run. A sparse direction may legitimately produce only a few strong matches; generic adjacent work is not a substitute for missing target demonstrations.

Before accepting an apparent conclusion, check whether the most likely missed terminology or embedded-system form has been tested. When external checking is permitted, use primary sources to verify strong gap leads; do not turn a targeted gap check into an unbounded web survey. If the user prohibits web access, respect that limit rather than silently substituting private local data.

## Example: Pulsed Cryogenic LNA

This is an illustration of reasoning axes, not a mandatory keyword list for all topics:

|Angle|Why it matters|Possible Lite vocabulary|
|---|---|---|
|Direct application|Find explicit target demonstrations|pulsed cryogenic LNA for qubit readout|
|Duty-cycled operation|Some work describes energy use instead of pulses|duty-cycled low-temperature readout receiver amplifier|
|Bias mechanism|Gate/drain/supply switching may be titled differently|gate-pulsed HEMT amplifier; switched drain bias low-noise amplifier|
|Transient limits|Useful work may focus on recovery rather than application|noise settling, gain recovery, bias network ringing, fast start-up|
|Low-temperature device route|Compare meaningful implementation branches without imposing one|InP HEMT, SiGe HBT, cryo-CMOS switched-bias receiver|
|Measurement|A static noise plot does not establish pulsed recovery|time-resolved noise figure, transient noise characterization|
|System embedding|Power cycling can be part of a larger chip|duty-cycled qubit readout SoC, synchronized receiver activation|

A result mentioning a gate bias low-pass filter or shaped recovery waveform opens follow-ups on bias-network response, pre-emphasis or fast-recovery biasing. These terms must be investigated, not assumed to be demonstrated. A pulse-generator/control paper is not automatically a pulsed LNA. A room-temperature measurement method can be useful support but must not be labelled a cryogenic circuit experiment.

## Economical but Not Premature

- Spend Lite calls to explore distinct hypotheses and missing branches. Save tokens by returning titles first, batching identity lookups and storing full data in files.
- Do not save tokens by ignoring every paper whose title is uncertain; that undermines recall. Read the abstracts of genuinely plausible ambiguous candidates.
- Repeated top hits help corroborate relevance but do not rule out a different terminology cluster.
- If a query reaches the API result cap, consider dividing it by a meaningful mechanism, device route or time window. Do not blindly split every query.
- When evidence suggests a predecessor/follow-up, use author or title queries. Do not build a large work-family tree without a relevance reason.
- Finish with a selective web check, retaining only strong matches verified by primary sources. Record origin and precise evidence boundaries.
- Stop on resolved research branches and diminishing useful discoveries, not on an arbitrary number of calls. If service failures prevent this, retain a partial run and report it honestly.
- Treat API cost and agent context cost separately. Keep full responses on disk, read compact summaries, batch identity lookups and avoid repeatedly printing the same abstracts. A cheap Lite request can still be wasteful if it adds no useful evidence.

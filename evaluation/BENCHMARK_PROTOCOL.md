# Zurich themed-walks benchmark: construction protocol

Fixed on 2026-09-30, before the benchmark is collected and before any run
against it. Any change to these rules is recorded at the end of this file
with its date and reason.

## Purpose

For a personal interest (a person, a movement, a period or event, a theme),
list the places in Zurich that independent parties associate with it. The
benchmark measures coverage (does the system find these places?). It does
not measure precision: a walk lists what its authors chose to show, not
every place connected to the interest. Precision is measured separately by
judged relevance (pooling).

## 1. Area

The city of Zurich and its immediate surroundings, i.e. the bounding box the
system serves (`config.ZURICH_BBOX`: 8.45-8.62 E, 47.30-47.46 N). A stop
outside it is recorded but marked `out_of_area` and not scored.

## 2. Eligible sources

- A themed walk, tour, self-guided route, brochure, map, audio walk, app
  route or article that names concrete places in Zurich connected to one
  specific interest.
- Published by a party independent of this work: tour operators and guides,
  museums, archives, foundations, universities, city departments, churches,
  societies, publishers, travel magazines, blogs.
- Excluded: zuerich.com (Zurich Tourism) and pages that copy it. The system
  reads the Zurich Tourism catalogue, so it cannot also be the benchmark.
- Excluded: generic sightseeing, food, drink and shopping tours.
- Excluded: Wikipedia and Wikidata, which the system itself reads.

## 3. Interests

- An interest enters the benchmark if at least one eligible source lists
  stops for it. The list is derived from the catalogue of walks
  (`benchmark_sources/tours_catalogue.json`), not chosen by us.
- Each interest is identified by its Wikidata item, the seed the system
  starts from; linking a user's interest to that item is not evaluated here.
- An interest is scored only if it has at least 3 stops in the area; one
  with fewer is recorded but reported apart, since one or two stops make
  recall a coin toss.
- The seven interests used while developing the system (Einstein, Joyce,
  Lenin, Jung, Thomas Mann, Zwingli, Dada) form the **development set**.
  Every other interest forms the **test set**, never looked at before the
  final run. Results are reported for both, separately.

## 4. Stops

- Every place a source names for the interest is a stop: no selection by us.
- One stop is one physical place. The same place named by several sources
  is one stop with several sources; the number of sources is kept.
- A stop records: the name used by the source, the address, the connection
  to the interest as the source states it (short), the source URL, and
  whether the place still exists.
- The order of a walk is a walking order, not an order of importance: it is
  ignored (not recorded, not scored).
- Each stop gets a kind of connection, read from the source's own words, not
  from our judgment: **primary** (the person lived, worked, studied, taught,
  performed, founded something, died or is buried there; the event happened
  there), **commemorative** (a monument, plaque, memorial, museum or archive
  devoted to the interest), **associative** (frequented, visited, stayed,
  mentioned, named after). This allows results by kind of connection.
- A place that no longer exists (demolished) stays in, marked `demolished`.
- Areas larger than a site (a whole district, a river, "the Old Town") are
  recorded but marked `area` and not scored.

## 4b. Completeness

The benchmark is not assumed complete: no single walk, and no union of
walks, lists every place connected to an interest. Two consequences:
- recall against the benchmark is the only benchmark metric; precision
  comes from judged relevance;
- where an interest has two or more independent sources, the overlap between
  them gives an estimate of how many places exist in total (capture-recapture,
  Lincoln-Petersen / Chao estimators), reported to show how far each walk,
  and the benchmark, is from complete.

## 5. Enrichment (automatic, then checked)

- Coordinates: geocoded from the address with OpenStreetMap (Nominatim).
- Wikidata item: the item within 60 m whose label names the stop, chosen by
  hand when several match; none if the place has no item.
- Footprint: the OpenStreetMap polygon of the place, kept only if it contains
  the stop's point and is smaller than 0.5 km2.

## 6. Verification

- Every stop is checked against its source URL by a second pass (a different
  person or agent than the one who collected it): the source names the place
  and the connection.
- A stop that cannot be verified is dropped and logged.

## 7. Freezing

- The benchmark is frozen as `ground_truth_zurich.json` with a version, a
  date and a SHA-256 hash recorded here, before the final runs.
- Source pages are archived (URL and retrieval date; a local copy of the
  text where licensing allows).

## 8. Scoring (for reference; implemented in src/evaluate_ground_truth.py)

- A stop is found by a candidate that is its Wikidata item, or lies inside
  it (located in or part of it on Wikidata, or within its OSM footprint).
- Secondary: a candidate within 50 m.
- Metrics per interest: found among all candidates, among the first ten,
  in the first tour; micro-averaged over the development and test sets.
- Main figure: the macro-average over interests (each interest counts the
  same; a few interests have 100-200 stops and would dominate otherwise).
  Secondary: the micro-average over stops.
- Every stop weighs the same within an interest. Secondary figures: recall
  weighted by the number of independent sources citing the stop (a place
  every walk includes counts more than one only a blog mentions), and recall
  by kind of connection.

## Changes

- 2026-09-30: the order of stops is ignored altogether (was: recorded, not scored).
- 2026-09-30: the area is the system's bounding box, not the city limits: Kilchberg and
  Kuesnacht (Thomas Mann, C.G. Jung) are in; the catalogue agent had dropped them.
- 2026-09-30: main figure = macro-average over interests; micro-average secondary.
- 2026-09-30: a work of art that no longer exists (Naegeli graffiti marked not visible by
  the source's own map) is recorded but not scored: the wall remains, the reason to go there
  does not.
- 2026-09-30: seed of the 1917 labour-movement walk = Q208701 (labour movement), not the 1918
  Landesstreik: the walk is about the November 1917 unrest.
- 2026-09-30: eligible sources include a text that lists the places tied to an interest even
  if it is not a walk (e.g. the residence list of the Robert Walser-Handbuch).
- 2026-09-30: excluded sources, in addition to zuerich.com: zh-reformation.ch (published by
  Zurich Tourism) and the BUX app routes (co-developed with Zurich Tourism).
- 2026-09-30: stops named by a source only as walk logistics (meeting, start or end point,
  route direction) with no tie to the interest, and stops whose place the sources do not pin
  down, are recorded but not scored (list: UNSCORED in src/build_benchmark.py).
- 2026-09-30: stops whose only source cannot be read for verification are not scored (VoiceMap
  tours: bot check, robots.txt forbids text and data mining).

## Frozen

- **v1, 2026-09-30**: `ground_truth_zurich.v1.json` (also `ground_truth_zurich.json`),
  SHA-256 `ce2e3b2daa7ff6ad5bb5b24a8a757db4056ad91e151b3665ab2f38b5d53db288`. 37 interests (35 scored: dev 7, test 28), 1006 stops collected, 639 scored
  (dev 104, test 535), all verified against their sources by a second pass
  (`benchmark_sources/verification_*.json`). Built by `src/build_benchmark.py` from
  `benchmark_sources/stops_group_{A,B,C}.json`.

## v2: published walks, one walk per case (decided 2026-09-30, night)

v1 went beyond recommended tours (articles, handbooks, databases of all works, fountain
inventories). v2 keeps only PUBLISHED WALKS: guided or self-guided walking tours with a route
(app routes and audio walks included) published by a guide, tour operator, museum, foundation,
archive, association, church, university or city department. Not walks: articles and travel
blogs, handbooks and scholarly texts, databases or maps of all works or places, inventories,
event pages, press releases. Classification: benchmark_sources/source_classes.json.

- Unit of evaluation: the single walk. Given the walk's subject (its interest's seed), the system
  proposes candidates and a tour; they are compared with the walk's own stops.
- A walk's stops are the v1 stops it names (same collection, same verification, same scored
  flags). A walk is scored if it has at least 3 scored stops in the area.
- Metrics per walk: share of its stops found among the first 30 candidates, among the first 10,
  and in the system's first tour; and the share of the system's tour stops that are walk stops.
  Main figure: macro-average over walks; dev/test as in v1 (by the walk's interest).
- v1 is kept (commit d38da55) and may be reported as a secondary, wider benchmark.

## ZT: a benchmark of Zurich Tourism's own themed tours (decided 2026-10-01)

A separate benchmark made only of the themed tours Zurich Tourism publishes (zuerich.com and
its microsites, e.g. zh-reformation.ch): the most representative "recommended tours" of the
city, excluded from v1/v2 because the text layer T is extracted from the same publisher's POI
catalogue. Built to decide how to proceed; on it W is a clean measure, while W+T must be read
as possibly circular (same publisher). Unit: the single tour; stops: every stop the tour names;
same stop rules, enrichment and verification as v1. Files: benchmark_sources/zt/zt_tours.json
-> ground_truth_zt.json (src/build_benchmark.py --zt). v1 and v2 are untouched.

## Changes after the first final runs (2026-10-01) -- disclosed

- Main coverage figure: stops cited by at least 2 independent sources (consensus). Secondary:
  key stops (cited by >= 2 sources AND a notable place: a Wikidata item or a POI of the
  open-data catalogue); all stops reported apart. Reason: a single-source private address
  (e.g. one of Einstein's ten lodgings) weighed as much as the Grossmuenster; the protocol
  already planned source-weighted recall. Decided after seeing first results; both figures and
  the all-stops figure are reported.
- v1.1: named places left without a Wikidata item by the automatic enrichment get the item
  found by name within 150 m (qid_by = "name within 150 m"); address-named stops untouched.
- Coverage is complemented by judged relevance on the pooled top-10 (precision, discoveries
  outside the benchmark), which the benchmark cannot measure.

## Human judging round (pre-registered 2026-10-01, before any human sees the lists)

Fixed before the round: the system version (git tag, frozen W+T run), the value threshold
(0.5, chosen on the LLM pilot grades, BENCHMARK_NOTES.md "Value threshold"), and everything below.
Built by `src/build_human_round.py`; the page shows only `human_round/questionnaire_data.json`;
the origin of each item is in `human_round/key.json`, never shown to assessors.

- **Assessors**: three, not authors of the system or of the benchmark collection, working
  independently (each sees and stores only their own answers). Allowed: maps and web search.
- **Interests**: all 35 scored interests of v1.1.
- **Lists**: experts = the 10 benchmark stops cited by most independent sources; system = the
  stops of the frozen W+T run with value >= 0.5 (at most 10). A place on both lists is one item.
- **Part A, places (blind)**: every item once, interests and items in random order per
  assessor. Each item shows place, address, map link and one neutral sentence on its link to the
  interest, written in the same style for every item whatever its origin
  (`human_round/connections.json`, written from the raw claims before the round; rules: one
  sentence, at most 25 words, plain facts, no source names, no quotes, no hedging, no ranking
  words). Question: "For a visitor interested in X, how worth visiting is this place?"
  0 not related / 1 marginal / 2 relevant / 3 must-see; "cannot judge" allowed.
- **Part C, lists (blind)**: for each interest with a system list (n stops), the system list and
  the experts' first n, as A and B in random order (fixed by seed), shown with the same neutral
  sentences. Question: "Which list would you rather follow on a walk about X?" A / B / no
  preference, optional comment.
- **Part B, explanations (after A and C are closed; A and C are then locked)**: each system
  stop with its explanation as the app shows it (reasons, hops, source with link, evidence).
  Questions: is the stated link correct (yes / partly / no / cannot tell after checking the
  source); does the cited source support it (yes / no / no source opened); is the explanation
  clear to a visitor (yes / no).
- **Measures**: per list, mean grade, share relevant (>= 2), share must-see (3), macro over
  interests; nDCG@10 for the system ranking; discoveries (system places graded >= 2 that the
  experts do not list); pairwise preference counts with a sign test; explanation correctness,
  support and clarity rates. Agreement: Fleiss kappa (4 grades, and relevant yes/no); final
  grade = median of the three. Persons, movements and themes reported apart; dev and test apart.

### Addendum (2026-10-02, before any place is checked): LLM lists and errors

- **Third source**: the LLM baseline lists (`llm_baseline/`, Gemini 3 Pro and GPT 6 Thinking, web
  search ON; closed-book not possible in the available interfaces) enter Part A blind like the
  other two lists, with neutral sentences written from the LLM's own link sentence. Part B pairs
  are drawn among the three lists at equal length.
- **Error classes** (decided with F.S.: one non-existent place ruins a tour):
  (1) *invented*: never existed, or not in the area; (2) *gone but presented as visitable*:
  existed, nothing to see today (no building, plaque or marker), and the list presents it as a
  place to visit; a former site presented as such, as published walks do, is not an error;
  (3) *false link*: the place exists but its stated link to the interest is wrong.
- **Who checks**: the assessors, in Part C (after A and B are locked), for every system and LLM
  place: "Does this place exist and can it be seen today?" (yes / gone / does not exist /
  cannot tell) and "Is the stated link true?" (yes / partly / no / cannot tell); majority of
  three. Expert places: existence and links were verified against their sources when the
  benchmark was built (`demolished` flag, `verification_*.json`); the same classes are applied to
  that record (disclosed).
- **Penalties**: a place in class 1 or 3 gets grade 0 whatever its Part A grade; class 2 gets
  at most 1. **Failed list**: a list with at least one place in class 1 or 3. Reported per
  source: error rate per class, share of failed lists, and all grade measures after penalties
  (and before, for reference).

### Addendum (2026-10-02, decided with F.S., before implementation): hybrid list (graph + LLM)

- **Idea**: LLM suggestions are used only as hints; a suggested place enters the tour only if
  the graph links it to the interest by a sourced path, and its explanation is that path.
  No new LLM calls: the saved Gemini 3 Pro and GPT 6 Thinking answers are reused.
- **Grounding**: each LLM place (geocoded as in `src/evaluate_llm_baseline.py`) is matched to the
  candidates of the frozen W+T run, all candidates kept (not only the first 30), with the
  benchmark's rule (same item, inside, or within 50 m). Matched = grounded; otherwise dropped.
- **List**: value of a candidate h(c) = 1 - (1 - s(c)) (1 - 0.5 [c named by at least one LLM]),
  where s(c) is the system's stop value: an LLM mention counts as evidence of value 0.5, never as
  the explanation. Hybrid list: candidates with h >= 0.5, by h, at most 10. Equivalently: the
  system's list plus the grounded LLM places.
- **Reported**: grounding rate of LLM places (share with a sourced path), per model; the hybrid
  list enters Part A (no new places beyond system and LLM ones) and the Part B pairs as a fourth
  source; same measures and penalties as the other lists.

### Addendum (2026-10-02, before the lists are generated): five sources, workload

- Sources per interest: experts (10), system (value >= 0.5, at most 10), hybrid (at most 10),
  Gemini, GPT (their full answers, at most 10). Part A grades their union once, blind.
- Part B becomes one ranking task per interest: every non-empty list, cut at its first 5
  places, labelled A-E in random order; the assessor orders them from the one they would rather
  follow to the least (ties allowed). Pairwise preferences are read off the rankings (sign test);
  an empty list counts as last. Replaces the A/B pairs.
- Part C (existence and link of every system, hybrid and LLM place): each place is checked by
  one assessor (assigned in rotation), and a random 20% by all three to measure agreement.
- Neutral sentences: as before; the place name shown is the first source's name (experts,
  system, hybrid, Gemini, GPT); a known residual cue, disclosed.
- Neutral sentences state only what the source's own claim says (no knowledge of the writer
  added); when the claim gives no specific link (e.g. a station of a route, a place "associated
  with" a subject), the sentence says "A place linked to X; no specific link is stated", whatever
  the source.

### System changes after inspecting results (2026-10-02) -- disclosed
Policy (F.S.): the reported system is the best one, including fixes found in review and testing;
every such change is listed here and in the paper. Before the human round:
(1) Overpass failures are never cached as "no members" (implementation bug);
(2) events are kept and become reasons of the place where they happened (P276), not excluded;
(3) a part (P527) of a theme is followed only if it acknowledges the theme (P361, P1269 or P279).
(2) and (3) were found on test interests (witch persecution).
(4) a reason folded into a stop is not shown when it is reached only through the stop itself
(e.g. "Jung -> University of Zurich <- a conference held there"): it adds nothing to the stop.

### Addendum (2026-10-02, decided with F.S., before any RAG answer is seen): system + LLM
- The hybrid list is replaced by grounded generation: the LLM (Gemini 3 Pro and GPT 6 Thinking,
  web search on) receives, per interest, the first 10 stops of the frozen W+T run with their fact
  chains and sources (`llm_rag/PROMPT_*.txt`) and composes the list: it may keep, drop or reorder
  graph stops (never change their facts), enrich them with facts, and add new stops; every added
  fact and new stop needs a URL and the exact quoted sentence, else it is dropped. A "graph" stop
  not among the given ids counts as an altered context (reported).
- One flow for every interest (no person/theme branch). In the round: 5 sources (experts, system,
  system+LLM [one list per model], Gemini, GPT); if both models are run, Part B ranks up to 6
  lists. Part C also checks every added fact (does the quoted page state it?).
- The write-back loop (added facts stored as RDF-star facts with origin LLM and lower confidence)
  is described in the paper, not evaluated.
- (2026-10-02, before the round) Part C asks "would a visitor understand from this explanation why the
  place is proposed?" for every explained place, LLM-only ones included (stated link, no source), so
  that clarity is compared across sources; "do the cited sources support it?" only where sources exist.

### System changes after inspecting results (2026-10-08) -- second round
Found by diagnosing why test themes had no candidates (their seeds are global concepts that no place
of the area is linked to in Wikidata), before the human round:
(5) concepts named in text: a sentence of a place's text (tourism description, blue plaque, public-art
title, OSM inscription; `zurich_texts.json`) that names a concept by one of its German or English
Wikidata labels (whole word, inflection, or a word the label begins) or aliases (whole word/phrase,
one-word alias >= 7 letters) is a fact (concept, nami:mentionedAt, place), confidence 0.6 / 0.5,
+0.1 if the place's name or more than one sentence names it; a term named by > 60 places is unused.
Fixed rules, no per-interest setting.
(6) every interest that is not a person is treated as a concept (area members are looked up for it),
not only those with topical concepts.
Effect on the 13 themes with agreed stops (recall of agreed stops): @5 .04 -> .14, @10 .06 -> .15;
people unchanged. All runs, the RAG prompts and the human-round lists are regenerated.

### Addendum (2026-10-08, before any assessor starts): LLM citations and per-citation checks
- The LLMs alone are also run under the citation rule of system + LLM (`llm_baseline/sources/`,
  PROMPT_SOURCES_1-5: every place with URL and exact quote). Parts A and B keep the pre-registered
  LLM lists (answers without the rule); the cited answers serve O3.
- Quote check (automatic, both for system + LLM and for the LLMs alone): the quote is on the page up to
  case, punctuation and at most 10% of its words, in order; system + LLM keeps only verified facts.
- Part C asks, for every cited sentence (system + LLM and LLMs alone): is it on the page? (as quoted /
  same in other words / the page does not say this / the page does not open). A citation of the LLMs
  alone joins the check of the item it names, or is a check of its own. The automatic result is kept in
  key.json (llm_citations) and never shown. Questionnaire data version human-round-2.

## Addendum (2026-10-08): round 6, NAMI + a language model on the task of round 5

Round 5 gives each model the interest only and asks for the places of a walk with all their reasons, one fact per reason, each with a URL and an exact quote (temporary chats, web search on). Round 6 gives the same task to a model that also receives NAMI's first ten stops with their reasons; the system shows a reason the model adds only if its quote is on the cited page, and a new place only if it keeps such a reason.

The prompt of round 6 is part of the design of the hybrid configuration. It is designed on the 7 development interests only (Einstein, Joyce, Lenin, Dada, Reformation, Thomas Mann, Jung), frozen, and then run on the test interests, on which the results are reported, compared with round 5 on the same interests. Variants: v1 ("you may add reasons to a kept stop"; first two blocks answered, kept in evaluation/llm_rag_reasons/v1_initial/), v2 (every place, kept or new, must get all further reasons, as strongly as in round 5). The selection criterion on dev is the number of verified reasons per place shown after the check; the chosen variant and the date it is frozen are recorded here before the test run.

Round 6, choice of the prompt (2026-10-09). On the development interests answered by every list (6), verified reasons per place shown after the check, mean (median): v2 NAMI+GPT 3.78 (3), NAMI+Gemini 3.61 (3); v3 NAMI+GPT 4.64 (4), NAMI+Gemini 3.44 (2.5). Places shown: v2 23 and 23; v3 28 and 18. Over both models v3 shows more verified reasons (192 against 170) on as many places (46), so v3 is chosen and frozen on 2026-10-09; the test prompts are generated from it (evaluation/llm_rag_reasons/v3/test/) and no further variant is tried.

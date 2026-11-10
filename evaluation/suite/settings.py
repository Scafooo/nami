"""Configuration of the evaluation suite (v2). Everything the suite runs, samples and computes is fixed
here, before any run; a change after the first run is recorded in CHANGES.md with its date and reason.

All results come from this suite only: no output of earlier rounds is read."""

from __future__ import annotations

import os
from pathlib import Path

SUITE = Path(__file__).resolve().parent
CODE = SUITE.parent.parent
SRC = CODE / "src"
BENCHMARK = CODE / "evaluation" / "ground_truth_zurich.json"

# Where the suite writes. NAMI_SUITE_WORK moves it all elsewhere (a dry run on a few interests, for instance).
WORK = Path(os.environ.get("NAMI_SUITE_WORK", SUITE))
RUNS = WORK / "runs"               # raw outputs of every condition
LISTS = WORK / "lists"             # every condition in one common form (lists.py)
LLM = WORK / "llm"                 # prompts to send and answers to paste
# The folder the user works in: code/evaluation/annotazioni/ (or <work>/annotazioni for a dry run).
ANNO = (WORK if "NAMI_SUITE_WORK" in os.environ else CODE / "evaluation") / "annotazioni"
ANNOT_OUT = ANNO / "da_annotare"                    # the workbooks for the annotators, and nothing else
ANNOT_BACK = ANNO / "compilati"                    # the filled workbooks, any file name
ANNOT_KEYS = WORK / "annotations_keys"             # what each row is: never sent
RESULTS = WORK / "results"

SEED = 20261021                    # every random draw of the suite

# Local Wikidata SPARQL endpoint on the evaluation machine (None: the public endpoint, cached in store_gp/).
# The environment variable NAMI_WIKIDATA_SPARQL overrides it.
WIKIDATA_SPARQL = None

# ---------------------------------------------------------------- the system, frozen
# No search budget (paper, 2026-11-05): the search ends when the frontier is empty, as Proposition 1 assumes.
PARAMS = dict(lam=0.5, theta=0.5, tau=0.15, d0=2000, d_pred=5, beam=50, max_expansions=0, topical_weight=0.8)   # d_pred 5: tune.py, 2026-11-06
K = 10                             # places compared per list

# ---------------------------------------------------------------- conditions on NAMI
# name -> overrides of PARAMS and switches of lib/pipeline.py. Each is run on every benchmark interest.
NAMI = {
    "C1_nami":         {},                                                    # Wikidata + city sources (the system)
    "C2_wikidata":     {"wikidata_only": True},                               # ablation: without the city's sources
    "C3_noprov":       {"ignore_provenance": True},                           # conf = trust = 1, no theta
    "A1_no_eta":       {"d0": 10**9, "d_pred": 10**9},                        # no specificity
    "A3_no_derived":   {"no_derived": True},                                  # no derived seeds
    "A4_class_up":     {"allow_class_up": True},                              # condition (iii) off
    "S_lam04":         {"lam": 0.4},                                          # sensitivity of the first ten to lambda
    "S_lam06":         {"lam": 0.6},
    "A2_no_decay":     {"lam": 1.0},                                          # no decay per hop
}
# Slow (hours per interest without a budget): run on their own, `suite.py run --only NB_nobudget`.
NAMI_SLOW: dict = {}           # none since the budget is gone

# Control by trust (Prop. 3): a fabricated source asserting wrong person-place facts, at three trusts.
INJECT = dict(n_facts=20, source="https://injected.example.org/", trusts=(1.0, 0.5, 0.0), predicate="livedAt")

# ---------------------------------------------------------------- profiles with two interests (+ one to avoid)
# Each pair shares places in Zurich that a visitor with both interests should get first.
PAIRS: list = []                   # two-interest profiles dropped (2026-11-05); the former list:
_PAIRS_V1 = [
    ("Q937", "Q41532"),      # Einstein + C. G. Jung
    ("Q6882", "Q6034"),      # Joyce + Dada
    ("Q1394", "Q6034"),      # Lenin + Dada
    ("Q37030", "Q1383821"),  # Thomas Mann + exile writers
    ("Q123839", "Q24906109"),  # Bullinger + Reformation
    ("Q1411919", "Q1279400"),  # Katharina von Zimmern + women's history
    ("Q122370", "Q115483"),  # Gottfried Keller + Max Frisch
    ("Q115569", "Q23642627"),  # Alfred Escher + silk industry
    ("Q1511", "Q846047"),    # Wagner + music history
    ("Q58033", "Q692021"),   # Else Lasker-Schueler + Jewish Zurich
]
AVOID: list = []                   # dropped with notInterestedIn

# ---------------------------------------------------------------- a second city, Wikidata only
# No benchmark there: its lists are judged for pertinence only (A2). Set ENABLED to False to skip it.
CITY2 = dict(
    ENABLED=False,                         # second city dropped (2026-11-05): Zurich only
    name="Basel",
    area="Q78",
    bbox=(7.55, 47.52, 7.64, 47.60),        # west, south, east, north
)   # its interests and subdivision classes: runs/city2.json, written by `suite.py city2-check`
CITY2_CANDIDATES = ["Erasmus of Rotterdam", "Paracelsus", "Hans Holbein the Younger", "Friedrich Nietzsche",
                    "Leonhard Euler", "Jean Tinguely", "Theodor Herzl", "Hermann Hesse", "Jacob Burckhardt",
                    "Basel Carnival"]

# ---------------------------------------------------------------- language models
# Answers are pasted by hand from the chat (decision D3); each run in a new chat without memory.
MODELS = ("gpt", "gemini")              # Claude dropped (2026-11-09)
LLM_RUNS = 1                        # one chat per prompt (2026-11-06)
LLM_BLOCKS = 7                     # prompts per run (interests per prompt = 35 / 7)

# ---------------------------------------------------------------- annotation tasks
# A1 reasons: every reason shown at `places_per_list` places drawn at random from each list of Table 3 that gives
# reasons, and `dropped` reasons the check does not show in NAMI + model (its strictness).
A1 = dict(places_per_list=30, dropped=40, controls=0,      # 2026-11-09: no controls
          lists=["C1_nami", "llm_gpt_run1", "llm_gemini_run1",
                 "hybrid_gpt_run1", "hybrid_gemini_run1"])
# A2 places: the pool, every place of every list of Tables 3 and 4, each graded 0-3 by two of `annotators` people.
A2 = dict(annotators=15, per_place=15, controls=0, full_lists=None, sample_secondary=None)  # 2026-11-09: every place to all 15, no controls
GUIDES_K = 10                      # stops of the published guides drawn at random per interest, as a list
A3 = None                          # dropped (2026-11-05): no separate task on extracted facts
A4 = None                          # dropped (2026-11-05): the guides are a compared list, not a truth to check
EXCLUDE_IF_CONTROL_ERRORS_ABOVE = 0.20          # an annotator failing more controls than this is excluded

# ---------------------------------------------------------------- statistics
BOOTSTRAP = 10_000
PERMUTATIONS = 10_000
PAIRED_TESTS = [                   # fixed in advance, Holm-corrected together
    ("C1_nami", "llm_gpt"), ("C1_nami", "llm_gemini"),
    ("hybrid_gpt", "llm_gpt"), ("hybrid_gemini", "llm_gemini"),
    ("C1_nami", "guides"), ("C1_nami", "B_onehop"),
    ("C1_nami", "C2_wikidata"), ("C1_nami", "C3_noprov"), ("C1_nami", "A1_no_eta"), ("C1_nami", "A3_no_derived"),
]

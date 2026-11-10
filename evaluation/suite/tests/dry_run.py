"""Dry run of the whole suite on two interests, in a separate folder (tests/_dry/), to check that every step
works before the real run. The language-model answers are taken from a fixture and the annotations are
FILLED AT RANDOM: nothing of this folder is a result. Delete tests/_dry/ afterwards.

    NAMI_SUITE_WORK=tests/_dry NAMI_SUITE_INTERESTS=Q6034,Q23642627 python tests/dry_run.py"""

from __future__ import annotations

import json
import os
import random
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
assert os.environ.get("NAMI_SUITE_WORK"), "set NAMI_SUITE_WORK to a scratch folder"
from lib import suite_paths  # noqa: E402,F401

import settings  # noqa: E402

settings.PAIRS = [("Q6034", "Q23642627")]
settings.AVOID = [("Q6034", "http://www.wikidata.org/entity/Q3918")]
settings.LLM_RUNS = 1
settings.LLM_BLOCKS = 1
settings.A2["annotators"] = 2

from lib import annotate, baselines, lists, llm, results, run_nami, sheets  # noqa: E402

baselines.MAX_LEVEL = 30           # dry run on the public endpoint: fewer neighbours (real run: 300)

FIX = Path(__file__).resolve().parent / "fixture_answers.json"

real_city2 = settings.SUITE / "runs" / "city2.json"
settings.RUNS.mkdir(parents=True, exist_ok=True)
if real_city2.exists():
    c2 = json.loads(real_city2.read_text())
    c2["interests"] = c2["interests"][4:5]
    (settings.RUNS / "city2.json").write_text(json.dumps(c2))

run_nami.main(["C1_nami", "C2_wikidata", "C3_noprov", "A3_no_derived", "M_pairs", "M_avoid",
               "C9_inject_t1.0", "C9_inject_t0.0", "Z2_wikidata"])
baselines.main()
llm.prompts()
fx = json.loads(FIX.read_text())
for setting, text in fx.items():
    for m in settings.MODELS:
        f = settings.LLM / setting / "run1" / f"{m}_block1.txt"
        if f.exists():
            f.write_text(text)
llm.process()
lists.main()
annotate.main()
rng = random.Random(0)
for task in ("A1", "A2", "A3", "A4"):
    files = sorted(settings.ANNOT_OUT.glob(f"{task}_*.xlsx"))
    for k, f in enumerate(files):
        for who in (("ann1", "ann2") if task != "A2" else (f"ann{k + 1}",)):
            rows = sheets.read(f)
            out = settings.ANNOT_BACK / task
            out.mkdir(parents=True, exist_ok=True)
            head = list(next(iter(rows.values())).keys())
            filled = []
            for rid, r in rows.items():
                for c in head:
                    if c.startswith(("Q1", "Q2", "Q3")):
                        r[c] = rng.choice(["yes", "yes", "partly", "no"])
                    elif c.startswith("G "):
                        r[c] = rng.choice(["0", "1", "2", "3"])
                    elif c.startswith("E "):
                        r[c] = rng.choice(["yes", "yes", "no"])
                filled.append([r[c] for c in head])
            import csv
            with open(out / f"{who}_{f.stem}.csv", "w", newline="", encoding="utf-8-sig") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(head)
                w.writerows(filled)
results.main()
print("dry run done:", settings.WORK)

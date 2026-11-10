"""`suite.py status`: what each step has produced and what is still missing, in the order of the steps."""

from __future__ import annotations

import json

from lib.suite_paths import CODE  # noqa: F401

import settings
from lib import bench, sheets
from lib.run_nami import conditions


def main() -> None:
    n = len(bench.interests())
    print(f"work folder: {settings.WORK}")
    print("1  NAMI runs (suite.py run)")
    for c in conditions(None, slow=True):
        f = settings.RUNS / "nami" / f"{c}.json"
        if f.exists():
            runs = json.loads(f.read_text())["runs"]
            err = sum("error" in r for r in runs)
            print(f"   {c:20s} {len(runs) - err:3d} profiles" + (f", {err} errors (rerun: suite.py run --only {c})" if err else ""))
        else:
            print(f"   {c:20s} missing")
    print("2  baselines (suite.py baselines)")
    for b in ("B_onehop", "B_prepackaged", "B_ppr"):
        print(f"   {b:20s} {'ok' if (settings.RUNS / 'baselines' / f'{b}.json').exists() else 'missing'}")
    print("3  language models (suite.py llm-prompts, paste answers, suite.py llm-process)")
    for setting in ("alone", "hybrid", "city2"):
        for r in range(1, settings.LLM_RUNS + 1):
            d = settings.LLM / setting / f"run{r}"
            for m in settings.MODELS:
                files = sorted(d.glob(f"{m}_block*.txt")) if d.exists() else []
                filled = sum(f.stat().st_size > 0 for f in files)
                done = (settings.RUNS / "llm" / f"{setting}_{m}_run{r}.json").exists()
                print(f"   {setting:7s} run{r} {m:7s} answers pasted {filled}/{len(files) or '-'}  processed: {'yes' if done else 'no'}")
    print(f"4  lists (suite.py lists): {len(list(settings.LISTS.glob('*.json'))) if settings.LISTS.exists() else 0} lists")
    print("5  annotations (suite.py annotate, then put the filled files in annotations/returned/<task>/)")
    for task in ("A1", "A2", "A3", "A4"):
        built = (settings.ANNOT_KEYS / f"{task}_key.json").exists()
        back = sheets.returned(task, settings.ANNOT_BACK)
        print(f"   {task}: built {'yes' if built else 'no'}, returned {len(back)} file(s) {sorted(back)}")
    print(f"6  results (suite.py results): {'report.md present' if (settings.RESULTS / 'report.md').exists() else 'not yet'}")
    print(f"   ({n} benchmark interests)")

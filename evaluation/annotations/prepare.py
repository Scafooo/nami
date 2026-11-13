"""Prepares the human assessment. Runs everything automatic (NAMI and its variants, the baselines, the check
of the citations of the language models), then writes the workbooks for the assessors into workbooks/.
It can be run again at any time: it resumes where it stopped.

    python prepare.py                          (from evaluation/annotations/)
    python prepare.py --without-wikidata       (uses the saved runs only)"""

import argparse
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parent.parent / "suite"
sys.path.insert(0, str(SUITE))
from lib import suite_paths  # noqa: E402,F401

import settings  # noqa: E402
from lib import annotate, baselines, lists, llm, run_nami  # noqa: E402


def answers_missing() -> list[Path]:
    out = []
    for setting in ("alone", "hybrid"):
        for f in sorted((settings.LLM / setting).glob("run*/*_block*.txt")):
            if f.stat().st_size == 0:
                out.append(f)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--without-wikidata", action="store_true",
                    help="do not run NAMI or the baselines: use the saved runs only")
    a = ap.parse_args()
    if a.without_wikidata:
        print("1/4 NAMI and baselines: skipped, using the saved runs")
    else:
        print("1/4 NAMI and baselines")
        run_nami.main(None, False, None)
        baselines.main()
    print("2/4 language models")
    llm.prompts()
    missing = answers_missing()
    if missing:
        print(f"\n{len(missing)} answers of the models are missing. For each empty file below, open a NEW chat of the")
        print("model named in the file, with web search on, paste the PROMPT_<n>.txt of the same folder, and paste")
        print("the whole answer into the empty file. Then run prepare.py again.\n")
        for f in missing[:12]:
            print("  ", f)
        if len(missing) > 12:
            print(f"   ... and {len(missing) - 12} more")
        return
    llm.process()
    print("3/4 lists")
    lists.main()
    print("4/4 workbooks")
    annotate.main()


if __name__ == "__main__":
    main()

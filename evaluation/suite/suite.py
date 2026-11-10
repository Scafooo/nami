"""Evaluation suite v2, one entry point. See README.md for the order of the steps.

    python suite.py status                      what is done, what is missing
    python suite.py run [--only C ...] [--interests Q ...] [--slow]
    python suite.py city2-check                 resolves the second city's interests (once)
    python suite.py baselines                   one hop, pre-packaged tour, personalized PageRank
    python suite.py llm-prompts                 prompts to paste into the chats
    python suite.py llm-process                 reads the pasted answers, checks every quote
    python suite.py lists                       every condition in one form, with the automatic checks
    python suite.py annotate                    workbooks for the annotators (and their keys)
    python suite.py results                     every table, figure datum and number of the paper
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib import suite_paths  # noqa: E402,F401


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--only", nargs="*")
    r.add_argument("--interests", nargs="*")
    r.add_argument("--slow", action="store_true", help="also the run without budget (hours)")
    for c in ("status", "city2-check", "baselines", "llm-prompts", "llm-process", "lists", "annotate", "results"):
        sub.add_parser(c)
    a = ap.parse_args()
    if a.cmd == "run":
        from lib import run_nami
        run_nami.main(a.only, a.slow, a.interests)
    elif a.cmd == "status":
        from lib import status
        status.main()
    elif a.cmd == "city2-check":
        from lib import city2
        city2.main()
    elif a.cmd == "baselines":
        from lib import baselines
        baselines.main()
    elif a.cmd == "llm-prompts":
        from lib import llm
        llm.prompts()
    elif a.cmd == "llm-process":
        from lib import llm
        llm.process()
    elif a.cmd == "lists":
        from lib import lists
        lists.main()
    elif a.cmd == "annotate":
        from lib import annotate
        annotate.main()
    elif a.cmd == "results":
        from lib import results_v2
        results_v2.main()


if __name__ == "__main__":
    main()

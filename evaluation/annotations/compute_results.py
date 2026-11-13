"""Computes the results from the filled workbooks in filled/ (any file name: the task is recognised from the
file). Writes results.md next to this script, and the LaTeX rows and figure data into ../suite/results/.

    python compute_results.py                  (from evaluation/annotations/)"""

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "suite"))
from lib import suite_paths  # noqa: E402,F401

import settings  # noqa: E402
from lib import results_v2 as results  # noqa: E402

results.main()
out = HERE / "results.md"
shutil.copy(settings.RESULTS / "report.md", out)
print("->", out)

"""Calcola i risultati dai file compilati in compilati/ (qualsiasi nome: il compito si riconosce dal file).
Scrive risultati.md qui accanto; tabelle LaTeX e dati per le figure in ../suite/results/.

    ../../.venv/bin/python calcola_risultati.py        (dalla cartella annotazioni/)"""

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "suite"))
from lib import suite_paths  # noqa: E402,F401

import settings  # noqa: E402
from lib import results_v2 as results  # noqa: E402

ap = argparse.ArgumentParser()
a = ap.parse_args()
results.main()
out = HERE / "risultati.md"
shutil.copy(settings.RESULTS / "report.md", out)
print("->", out)

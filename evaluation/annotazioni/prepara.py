"""Prepara i file da annotare. Esegue tutto ciò che è automatico (le run di NAMI, i baseline, il controllo delle
citazioni), poi scrive in da_annotare/ i file per gli annotatori. Si può rilanciare quante volte serve: riprende da dove era.

    ../../.venv/bin/python prepara.py                  (dalla cartella annotazioni/)

Con Wikidata in locale: prima  export NAMI_WIKIDATA_SPARQL=http://<endpoint locale>"""

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
    ap.add_argument("--senza-wikidata", action="store_true",
                    help="non calcola NAMI ne' i baseline: usa solo le run gia' salvate (Wikidata non disponibile)")
    a = ap.parse_args()
    if a.senza_wikidata:
        print("1/4 NAMI e baseline: saltato (--senza-wikidata), uso le run salvate")
    else:
        print("1/4 NAMI e baseline (automatico)")
        run_nami.main(None, False, None)
        baselines.main()
    print("2/4 modelli linguistici")
    llm.prompts()
    missing = answers_missing()
    if missing:
        print(f"\nMancano {len(missing)} risposte dei modelli. Per ciascun file vuoto qui sotto: apri una chat NUOVA del")
        print("modello (gpt o gemini, come nel nome), ricerca web attiva, incolla il PROMPT_<n>.txt della stessa cartella,")
        print("e incolla la risposta intera nel file vuoto. Poi rilancia prepara.py.\n")
        for f in missing[:12]:
            print("  ", f)
        if len(missing) > 12:
            print(f"   ... e altri {len(missing) - 12}")
        return
    llm.process()
    print("3/4 liste")
    lists.main()
    print("4/4 file da annotare")
    annotate.main()


if __name__ == "__main__":
    main()

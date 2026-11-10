"""Puts code/src and the suite on the import path, so that suite modules use the code of NAMI as it is.
The suite's own settings are in settings.py (src/ has its own config.py)."""

from __future__ import annotations

import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parent.parent
CODE = SUITE.parent.parent
SRC = CODE / "src"
for p in (str(SUITE), str(SRC)):     # src first: its modules import `config`; suite modules as lib.*
    if p in sys.path:
        sys.path.remove(p)
    sys.path.insert(0, p)

# A local Wikidata (e.g. QLever or Blazegraph on the evaluation machine): every request the code of NAMI
# sends to the public SPARQL endpoint goes there instead. Set it in settings.WIKIDATA_SPARQL or in the
# environment variable NAMI_WIKIDATA_SPARQL. The Wikibase API (wbsearchentities, labels), which a SPARQL
# store does not offer, is still called online when its cache misses.
import os as _os

PUBLIC_SPARQL = "https://query.wikidata.org/sparql"


def use_local_wikidata(endpoint: str | None) -> None:
    if not endpoint:
        return
    import requests
    original = requests.Session.request
    if getattr(original, "_seme_local", False):
        return

    def request(self, method, url, *args, **kwargs):
        if isinstance(url, str) and url.startswith(PUBLIC_SPARQL):
            url = endpoint + url[len(PUBLIC_SPARQL):]
        return original(self, method, url, *args, **kwargs)

    request._seme_local = True
    requests.Session.request = request
    print(f"Wikidata SPARQL -> {endpoint}", flush=True)


try:
    import settings as _settings
    use_local_wikidata(_os.environ.get("NAMI_WIKIDATA_SPARQL") or getattr(_settings, "WIKIDATA_SPARQL", None))
except ImportError:
    pass

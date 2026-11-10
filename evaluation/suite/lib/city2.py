"""Second city (settings.CITY2), Wikidata only: resolves its interests to Wikidata items and finds the
classes of its subdivisions (excluded as places, as Zurich's districts are). Writes runs/city2.json, which
the run of condition Z2_wikidata reads; check it by hand once: a wrong item would be a wrong interest."""

from __future__ import annotations

import json
import time

import requests

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
import settings
from lib.baselines import sparql

OUT = settings.RUNS / "city2.json"


def search(name: str) -> dict | None:
    r = requests.get("https://www.wikidata.org/w/api.php", timeout=30,
                     params={"action": "wbsearchentities", "search": name, "language": "en", "format": "json", "limit": 1},
                     headers={"User-Agent": code_config.USER_AGENT})
    time.sleep(1.0)
    hits = r.json().get("search", []) if r.ok else []
    return {"id": hits[0]["id"], "label": hits[0].get("label"), "description": hits[0].get("description")} if hits else None


def main() -> None:
    c2 = settings.CITY2
    interests = [x for x in (search(n) for n in settings.CITY2_CANDIDATES) if x]
    rows = sparql(f"""SELECT ?class ?label (COUNT(?x) AS ?n) WHERE {{
      ?x wdt:P131 wd:{c2['area']} ; wdt:P31 ?class .
      ?class rdfs:label ?label FILTER(LANG(?label) = "en")
      FILTER(CONTAINS(LCASE(?label), "quarter") || CONTAINS(LCASE(?label), "district") || CONTAINS(LCASE(?label), "neighbourhood"))
    }} GROUP BY ?class ?label ORDER BY DESC(?n) LIMIT 5""")
    classes = [{"id": r["class"]["value"].rsplit("/", 1)[-1], "label": r["label"]["value"], "n": int(r["n"]["value"])} for r in rows]
    out = {**{k: v for k, v in c2.items() if k != "ENABLED"}, "interests": interests,
           "subdivision_classes": [c["id"] for c in classes], "subdivision_found": classes}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False))
    for i in interests:
        print(f"  {i['id']:10s} {i['label']}  ({i.get('description')})")
    print("  subdivisions:", classes)
    print(f"-> {OUT}  (check the items above; edit the file if one is wrong)")

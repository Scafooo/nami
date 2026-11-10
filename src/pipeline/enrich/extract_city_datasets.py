"""Facts of G_P from two structured open datasets of the City of Zurich:

- Kunst im Stadtraum (public art, data/raw/stadt_zuerich_kunst_im_stadtraum.geojson):
  every work is a place; a person its title names is commemorated there
  (nami:commemoratedAt), and each of its authors created a work there
  (nami:createdAt);
- Widmungstafeln (street dedications, data/raw/stadt_zuerich_widmungstafeln.geojson):
  the street is named after the person the dedication names (wdt:P138), and
  the dedication text is kept as evidence.

People are resolved to Wikidata through German Wikipedia titles (only to reach
the item). A place is the Wikidata item within NEAR_M whose label names it,
else a node of its own (https://example.org/nami/data/plaque/<prefix><id>) written
with its point and label.

Run: python src/pipeline/enrich/extract_city_datasets.py"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402
from extract_mentions import (CACHE_FILE, DISAMBIGUATION, HUMAN, MOVEMENTS, Wikidata, candidates,  # noqa: E402
                              name_tokens, spans, turtle_string)

ART = config.RAW_DIR / "stadt_zuerich_kunst_im_stadtraum.geojson"
DEDICATIONS = config.RAW_DIR / "stadt_zuerich_widmungstafeln.geojson"
ART_SRC = "https://data.stadt-zuerich.ch/dataset/geo_kunst_im_stadtraum"
DED_SRC = "https://data.stadt-zuerich.ch/dataset/geo_widmungstafel"
OUT_FILE = config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_city_datasets.ttl"
NODE = "https://example.org/nami/data/plaque/"
NEAR_M = 60
PERSON = re.compile(r"^\s*([^()\n]+?)\s*\((?:\d{3,4}|um|ca\.|geb\.)[^)]*\)")   # "Franz Fischer (1900-1980)"


def people_in(text: str) -> list[str]:
    """Names written as 'Name (dates)', one per line."""
    return [m.group(1).strip() for line in text.splitlines() for m in [PERSON.match(line)] if m
            and "unbekannt" not in m.group(1).lower()]


def main() -> None:
    wd = Wikidata(CACHE_FILE)
    west, south, east, north = config.ZURICH_BBOX
    items = []      # (kind, id, title, lat, lon, [(name, relation, evidence)])
    for f in json.loads(ART.read_text())["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        lon, lat = f["geometry"]["coordinates"][:2]
        title = (p.get("titel") or "").strip("[] ")
        rels = [(a, "createdAt", f"{title}: {a.strip()}") for a in people_in(p.get("autoren") or "")]
        # a person the title names is commemorated there ("Richard-Wagner-Stele", "Büste von Richard Wagner")
        rels += [(c, "commemoratedAt", title) for sp in spans(title.replace("-", " ")) for c, _, _ in candidates(sp)
                 if len(c.split()) > 1]
        items.append(("art", str(p.get("objectid")), title or (p.get("standort") or ""), lat, lon, rels,
                      ART_SRC, p.get("standort")))
    for f in json.loads(DEDICATIONS.read_text())["features"]:
        p = f["properties"]
        if not f.get("geometry"):
            continue
        g = f["geometry"]["coordinates"]
        lon, lat = (g[0] if isinstance(g[0], list) else g)[:2]
        text = re.sub(r"\r", "", p.get("widmungstext") or "")
        first = text.split("\n")[0]
        name = re.sub(r"\s*\(.*$", "", first).strip()
        items.append(("ded", str(p.get("objectid")), p.get("strasse") or "", lat, lon,
                      [(name, "P138", " ".join(text.split()))] if name else [], DED_SRC, p.get("strasse")))
    items = [x for x in items if west <= x[4] <= east and south <= x[3] <= north and x[5]]
    names = {n for x in items for n, _, _ in x[5]}
    print(f"{len(items)} works and dedications naming someone, {len(names)} names", flush=True)
    try:
        title_qid = wd.titles(names, wiki="de")
        ents = wd.entities({q for q in title_qid.values() if q and q != DISAMBIGUATION})
        near = wd.near({f"{x[0]}{x[1]}": (x[3], x[4]) for x in items if x[0] == "art"}, NEAR_M)
        # a street is long: its dedication plaque can lie far from the street item's point
        near.update({k.removesuffix("@400"): v for k, v in
                     wd.near({f"{x[0]}{x[1]}@400": (x[3], x[4]) for x in items if x[0] == "ded"}, 400).items()})
    finally:
        wd.save()

    lines = ["# Facts of G_P from City of Zurich open data: public art (Kunst im Stadtraum) and street",
             "# dedications (Widmungstafeln), by src/pipeline/enrich/extract_city_datasets.py.",
             "@prefix nami: <https://example.org/nami/ontology#> .", "@prefix wd:  <http://www.wikidata.org/entity/> .",
             "@prefix wdt: <http://www.wikidata.org/prop/direct/> .",
             "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
             "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", ""]
    n_facts, review = 0, []
    for kind, oid, title, lat, lon, rels, src, where in items:
        facts = []
        for name, rel, evidence in rels:
            q = title_qid.get(name)
            ent = ents.get(q) if q and q != DISAMBIGUATION else None
            if not ent or not (HUMAN in ent["p31"] or MOVEMENTS & set(ent["p31"])):
                continue
            facts.append((q, rel, evidence))
        if not facts:
            continue
        key = f"{kind}{oid}"
        nt_title = name_tokens(title)
        best, score = None, 0.0
        for it in near.get(key, []):
            for label in it["labels"]:
                lt = name_tokens(label)
                if nt_title and lt:
                    sc = len(nt_title & lt) / max(len(nt_title), len(lt))
                    if sc > score:
                        best, score = it["id"], sc
        if best and score >= 0.5:
            place = f"wd:{best}"
        else:
            place = f"<{NODE}{kind}_{oid}>"
            label = title if kind == "ded" or not where else f"{title} ({where})"
            lines.append(f"{place} rdfs:label {turtle_string(label)} ;\n"
                         f"    geo:hasGeometry [ geo:asWKT \"POINT({lon} {lat})\"^^geo:wktLiteral ] .")
        for q, rel, evidence in dict.fromkeys(facts):
            if rel == "P138":
                lines.append(f"{place} wdt:P138 wd:{q} {{| nami:source <{src}#{oid}> ;\n"
                             f"    nami:evidence {turtle_string(evidence)} ;\n    nami:confidence 0.9 |}} .")
            else:
                lines.append(f"wd:{q} nami:{rel} {place} {{| nami:source <{src}#{oid}> ;\n"
                             f"    nami:evidence {turtle_string(evidence)} ;\n    nami:confidence 0.9 |}} .")
            n_facts += 1
        review.append({"kind": kind, "id": oid, "title": title, "place": place,
                       "facts": [{"qid": q, "label": ents[q]["label"], "relation": r} for q, r, _ in facts]})
    OUT_FILE.write_text("\n".join(lines) + "\n")
    (config.ROOT_DIR / "output" / "city_datasets_review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False))
    print(f"{n_facts} facts from {len(review)} works/dedications -> {OUT_FILE}; {wd.calls} API calls")


if __name__ == "__main__":
    main()

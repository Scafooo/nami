"""The texts the open sources of G_P give about each place, indexed by the place they
describe, so that a concept a text names can be linked to that place at retrieval time
(CuratedFactSource.area_members): the Zurich Tourism descriptions (English, German, French and Italian: the languages of the country, and English),
the City of Zurich's blue heritage plaques, its public-art inventory (title and place),
and the inscriptions of OpenStreetMap memorials.

A place is identified exactly as the extractors of the people facts do (extract_mentions.py,
extract_plaque_mentions.py, extract_city_datasets.py, extract_osm_memorials.py): the Wikidata
item nearby whose label names it, else its own node; the index keeps the point and the label
of every place, and the source and sentences of every text.

Run: python src/pipeline/enrich/extract_texts.py"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402
from extract_mentions import (CACHE_FILE, MAX_LINK_M, POI, WD, Wikidata, catalogue_facts, link_place,  # noqa: E402
                              load_pois, name_of, name_tokens, plain, sentences, texts)

OUT_FILE = config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_texts.json"
NODE = "https://example.org/nami/data/plaque/"
NEAR_M = 60
PLAQUES = config.RAW_DIR / "stadt_zuerich_blaue_tafeln.geojson"
PLAQUE_SRC = "https://data.stadt-zuerich.ch/dataset/geo_liste_der_informationstafeln_an_denkmalschutz_objekten"
ART = config.RAW_DIR / "stadt_zuerich_kunst_im_stadtraum.geojson"
ART_SRC = "https://data.stadt-zuerich.ch/dataset/geo_kunst_im_stadtraum"
OSM = config.RAW_DIR / "osm_zurich_memorials.json"


def best_item(names: list[set[str]], nearby: list[dict], digits: bool = False) -> str | None:
    """The nearby item whose label shares at least half of the words of one of the names
    (with digits, all the numbers of the name too), as the plaque and dataset extractors do."""
    best, score = None, 0.0
    for it in nearby:
        for label in it["labels"]:
            lt = name_tokens(label)
            for nt in names:
                if nt and lt and not (digits and {t for t in nt if t.isdigit()} - lt):
                    sc = len(nt & lt) / max(len(nt), len(lt))
                    if sc > score:
                        best, score = it["id"], sc
    return best if best and score >= 0.5 else None


def main() -> None:
    wd = Wikidata(CACHE_FILE)
    west, south, east, north = config.ZURICH_BBOX
    inside = lambda lat, lon: west <= lon <= east and south <= lat <= north
    entries = []        # {place, label, lat, lon, source, sentences}

    # Zurich Tourism: every POI of the area with a description
    pois = load_pois()
    coords, twins = catalogue_facts(config.STORE_DIR)
    ids = sorted(p for p, (lat, lon) in coords.items() if p in pois and inside(lat, lon))
    docs = {}                       # pid -> {lang: sentences}: each language has its own page
    for pid in ids:
        by_lang = {lang: [s for t in texts(pois[pid], lang) for s in sentences(plain(t))] for lang in ("en", "de", "fr", "it")}
        if any(by_lang.values()):
            docs[pid] = by_lang
    # plaques
    plaques = []
    for f in json.loads(PLAQUES.read_text())["features"]:
        p, g = f["properties"], f.get("geometry")
        if not g or not (p.get("text_tafel") or "").strip():
            continue
        lon, lat = g["coordinates"][:2]
        if inside(lat, lon):
            plaques.append((str(p.get("bt_id") or p.get("objectid")), (p.get("titel_tafel") or "").strip(),
                            (p.get("adressen_tafel") or "").strip(), lat, lon,
                            sentences(re.sub(r"\s+", " ", " ".join(x for x in (p.get("titel_tafel"), p.get("text_tafel")) if x)))))
    # public art
    art = []
    for f in json.loads(ART.read_text())["features"]:
        p, g = f["properties"], f.get("geometry")
        if not g:
            continue
        lon, lat = g["coordinates"][:2]
        title = (p.get("titel") or "").strip("[] ")
        if title and inside(lat, lon):
            art.append((str(p.get("objectid")), title, p.get("standort") or "", lat, lon))
    try:
        near = wd.near({pid: coords[pid] for pid in docs}, MAX_LINK_M)
        near_pl = wd.near({pl[0]: (pl[3], pl[4]) for pl in plaques}, NEAR_M)
        near_art = wd.near({f"art{a[0]}": (a[3], a[4]) for a in art}, NEAR_M)
    finally:
        wd.save()

    for pid, by_lang in docs.items():
        place, _ = link_place(pid, pois[pid], near[pid], twins.get(pid))
        lat, lon = coords[pid]
        for lang, sents in by_lang.items():
            if sents:
                entries.append({"place": place, "label": name_of(pois[pid]), "lat": lat, "lon": lon,
                                "source": f"https://www.zuerich.com/{lang}/node/{pid}", "sentences": sents})
    for bid, title, address, lat, lon, sents in plaques:
        item = best_item([name_tokens(title), name_tokens(address)], near_pl.get(bid, []), digits=True)
        entries.append({"place": WD + item if item else f"{NODE}bt_{bid}", "label": title or address,
                        "lat": lat, "lon": lon, "source": f"{PLAQUE_SRC}#bt_{bid}", "sentences": sents})
    for oid, title, where, lat, lon in art:
        item = best_item([name_tokens(title)], near_art.get(f"art{oid}", []))
        entries.append({"place": WD + item if item else f"{NODE}art_{oid}",
                        "label": title if not where else f"{title} ({where})", "lat": lat, "lon": lon,
                        "source": f"{ART_SRC}#{oid}", "sentences": [title]})
    # OpenStreetMap memorials: inscription and name
    for e in json.loads(OSM.read_text())["elements"]:
        t = e.get("tags", {})
        lat, lon = (e["lat"], e["lon"]) if "lat" in e else (e.get("center", {}).get("lat"), e.get("center", {}).get("lon"))
        text = " ".join(x for x in (t.get("inscription"), t.get("name")) if x)
        if lat is None or not text or not inside(lat, lon):
            continue
        own = (t.get("wikidata") or "").split(";")[0].strip()
        entries.append({"place": WD + own if re.match(r"^Q\d+$", own) else f"{NODE}osm_{e['type']}_{e['id']}",
                        "label": t.get("name") or t.get("inscription")[:60], "lat": lat, "lon": lon,
                        "source": f"https://www.openstreetmap.org/{e['type']}/{e['id']}",
                        "sentences": sentences(re.sub(r"\s+", " ", text))})
    OUT_FILE.write_text(json.dumps(entries, ensure_ascii=False))
    print(f"{len(entries)} texts of {len({x['place'] for x in entries})} places -> {OUT_FILE}; {wd.calls} API calls")


if __name__ == "__main__":
    main()

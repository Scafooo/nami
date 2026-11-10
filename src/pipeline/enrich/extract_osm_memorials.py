"""Facts of G_P from OpenStreetMap (open data, ODbL): memorials, plaques, graves and
artworks in the area whose tags link them to a person or movement on Wikidata.

    subject:wikidata = Q...   the person or movement a memorial is about:
                              nami:livedAt if the inscription says so ("Hier wohnte"),
                              nami:buriedAt for a grave or tomb, else nami:commemoratedAt;
    artist:wikidata  = Q...   the artist of an artwork: nami:createdAt.

The place is the object's own Wikidata item (tag wikidata) when it has one, else a node
of its own (https://example.org/nami/data/plaque/osm_<type>_<id>) with its point and name. The
source of each fact is the OSM object; its inscription, or its name, is the evidence.
Data: data/raw/osm_zurich_memorials.json (Overpass query in the docstring of the notes).

Run: python src/pipeline/enrich/extract_osm_memorials.py"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402
from extract_mentions import turtle_string  # noqa: E402

DATA = config.RAW_DIR / "osm_zurich_memorials.json"
OUT_FILE = config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_osm_memorials.ttl"
NODE = "https://example.org/nami/data/plaque/"
LIVED = re.compile(r"\b(hier wohnte|wohnte hier|lebte hier|hier lebte|lived here|wohnhaus)\b", re.I)
QID = re.compile(r"^Q\d+$")


ADDRESSES = config.RAW_DIR / "osm_reverse_addresses.json"


def address(tags: dict, lat: float, lon: float, cache: dict) -> str:
    """The object's own address tags, else the nearest address (Nominatim reverse, cached)."""
    if tags.get("addr:street"):
        return f"{tags['addr:street']} {tags.get('addr:housenumber', '')}".strip()
    key = f"{lat:.6f},{lon:.6f}"
    if key not in cache:
        import time
        import requests
        r = requests.get("https://nominatim.openstreetmap.org/reverse", params={"lat": lat, "lon": lon,
                         "format": "json", "zoom": 18}, headers={"User-Agent": "NAMI-research-prototype/0.1"}, timeout=60)
        a = (r.json().get("address", {}) if r.ok else {})
        cache[key] = " ".join(x for x in (a.get("road") or a.get("pedestrian") or a.get("footway") or a.get("park") or "",
                                          a.get("house_number", "")) if x).strip()
        time.sleep(1.1)
    return cache[key]


def label_of(tags: dict, where: str) -> str:
    """A name a visitor can find: its own name, else what it is and by whom, and where."""
    kind = tags.get("memorial") or tags.get("artwork_type") or tags.get("tourism") or tags.get("historic") or "memorial"
    base = tags.get("name") or kind.replace("_", " ")
    if not tags.get("name") and tags.get("artist_name"):
        base += f" by {tags['artist_name']}"
    elif not tags.get("name") and tags.get("subject"):
        base += f" ({tags['subject']})"
    return f"{base}, {where}" if where else base


def qids(value: str | None) -> list[str]:
    return [q.strip() for q in (value or "").split(";") if QID.match(q.strip())]


def main() -> None:
    elements = json.loads(DATA.read_text())["elements"]
    cache = json.loads(ADDRESSES.read_text()) if ADDRESSES.exists() else {}
    lines = ["# Facts of G_P from OpenStreetMap (ODbL, (c) OpenStreetMap contributors): memorials, plaques,",
             "# graves and artworks linked by subject:wikidata / artist:wikidata. src/pipeline/enrich/extract_osm_memorials.py",
             "@prefix nami: <https://example.org/nami/ontology#> .", "@prefix wd:  <http://www.wikidata.org/entity/> .",
             "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
             "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .", ""]
    n = 0
    for e in elements:
        t = e.get("tags", {})
        about, artists = qids(t.get("subject:wikidata")), qids(t.get("artist:wikidata"))
        if not about and not artists:
            continue
        lat, lon = (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
        own = qids(t.get("wikidata"))
        name = t.get("name") or t.get("inscription") or t.get("memorial") or "memorial"
        if own:
            place = f"wd:{own[0]}"
        else:
            place = f"<{NODE}osm_{e['type']}_{e['id']}>"
            lines.append(f"{place} rdfs:label {turtle_string(label_of(t, address(t, lat, lon, cache)))} ;\n"
                         f"    geo:hasGeometry [ geo:asWKT \"POINT({lon} {lat})\"^^geo:wktLiteral ] .")
        text = " ".join(x for x in (t.get("inscription"), t.get("name")) if x)
        grave = t.get("memorial") in ("grave", "tomb") or t.get("historic") == "tomb" or "grab" in name.lower()
        rel = "livedAt" if LIVED.search(text) else "buriedAt" if grave else "commemoratedAt"
        src = f"https://www.openstreetmap.org/{e['type']}/{e['id']}"
        for q, r in [(q, rel) for q in about] + [(q, "createdAt") for q in artists]:
            if place == f"wd:{q}":
                continue
            lines.append(f"wd:{q} nami:{r} {place} {{| nami:source <{src}> ;\n"
                         f"    nami:evidence {turtle_string(text or name)} ;\n    nami:confidence 0.9 |}} .")
            n += 1
    ADDRESSES.write_text(json.dumps(cache, ensure_ascii=False, indent=0))
    OUT_FILE.write_text("\n".join(lines) + "\n")
    print(f"{n} facts -> {OUT_FILE}")


if __name__ == "__main__":
    main()

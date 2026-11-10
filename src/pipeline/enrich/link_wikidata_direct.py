"""Second-pass Wikidata linking: for POIs link_wikidata.py couldn't
resolve via OSM's wikidata= tag, search Wikidata directly by geography.

One query fetches every Wikidata item inside Zurich's bounding box
instead of one per POI, avoiding link_wikidata.py's Overpass rate-limit
problem. Same two-signal matching rule: unique candidate, within
MAX_DISTANCE_M, name similarity above NAME_THRESHOLD.

Run: python -m pipeline.enrich.link_wikidata_direct
Output: data/geo-ontology/ABox/wikidata_links_direct.ttl"""

from __future__ import annotations

import difflib
import json
import math
import urllib.parse
import urllib.request
from pathlib import Path

import config
from pipeline.enrich.link_wikidata import best_name_ratio, haversine_m, load_pois, name_variants

WDQS_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "NAMI-research-prototype/0.1 (evaluation)"
# Stricter than link_wikidata.py's 0.6: that pass is backed by a human
# having already tagged wikidata= on the matching OSM element; this pass has
# no such corroboration, so geometry+text alone need a higher bar. Verified
# 0.6 accepts a real false positive (Restaurant Enja -> Haus Hiltl, two
# distinct restaurants ~50m apart on the same street); 0.75 excludes it.
MAX_DISTANCE_M = 150
NAME_THRESHOLD = 0.75
OUT_FILE = config.RDF_DIR / "wikidata_links_direct.ttl"
CACHE_FILE = config.RAW_DIR / "wikidata_zurich_box.json"

# Padded from our own POIs' actual lat/lon range.
BBOX_WEST, BBOX_SOUTH = 8.45, 47.30
BBOX_EAST, BBOX_NORTH = 8.62, 47.46


def already_resolved_ids() -> set[str]:
    ids = set()
    f = config.RDF_DIR / "wikidata_links.ttl"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.startswith("<"):
                ids.add(line.split("poi/")[1].split(">")[0])
    return ids


def fetch_wikidata_box() -> list[dict]:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text())
    query = f"""
    SELECT ?item ?coord ?label WHERE {{
      SERVICE wikibase:box {{
        ?item wdt:P625 ?coord .
        bd:serviceParam wikibase:cornerWest "Point({BBOX_WEST} {BBOX_SOUTH})"^^geo:wktLiteral .
        bd:serviceParam wikibase:cornerEast "Point({BBOX_EAST} {BBOX_NORTH})"^^geo:wktLiteral .
      }}
      ?item rdfs:label ?label .
      FILTER(LANG(?label) IN ("en", "de", "fr", "it"))
    }}
    """
    req = urllib.request.Request(
        WDQS_URL + "?query=" + urllib.parse.quote(query),
        headers={"User-Agent": USER_AGENT, "Accept": "application/sparql-results+json"},
    )
    data = urllib.request.urlopen(req, timeout=120).read()
    rows = json.loads(data)["results"]["bindings"]
    CACHE_FILE.write_text(json.dumps(rows))
    return rows


def index_wikidata_items(rows: list[dict]) -> dict[str, dict]:
    """QID -> {lat, lon, labels: [...]}. Coord is repeated per label row."""
    items: dict[str, dict] = {}
    for row in rows:
        qid = row["item"]["value"].rsplit("/", 1)[-1]
        wkt = row["coord"]["value"]  # "Point(lon lat)"
        lon, lat = (float(x) for x in wkt[wkt.index("(") + 1 : wkt.index(")")].split())
        entry = items.setdefault(qid, {"lat": lat, "lon": lon, "labels": set()})
        entry["labels"].add(row["label"]["value"].strip().lower())
    return items


def resolve(poi: dict, items: dict[str, dict]) -> tuple[str, float, float] | None:
    lat, lon = poi["geoCoordinates"]["latitude"], poi["geoCoordinates"]["longitude"]
    poi_names = name_variants(poi)
    passing = []
    for qid, item in items.items():
        dist = haversine_m(lat, lon, item["lat"], item["lon"])
        if dist > MAX_DISTANCE_M:
            continue
        ratio = best_name_ratio(poi_names, list(item["labels"]))
        if ratio < NAME_THRESHOLD:
            continue
        passing.append((qid, dist, ratio))
    if len(passing) == 1:
        qid, dist, ratio = passing[0]
        return qid, round(dist, 1), round(ratio, 3)
    return None


def main() -> None:
    already = already_resolved_ids()
    pois = [p for p in load_pois_with_any_geo() if p["identifier"] not in already]
    print(f"Candidates for direct Wikidata search (not already OSM-linked): {len(pois)}")

    rows = fetch_wikidata_box()
    items = index_wikidata_items(rows)
    print(f"Wikidata items in the Zurich bounding box: {len(items)}")

    matches = []
    for poi in pois:
        result = resolve(poi, items)
        if result:
            matches.append((poi, *result))

    print(f"\nResolved {len(matches)} / {len(pois)} additional POIs.\n")
    print("--- sample for manual verification ---")
    for poi, qid, dist, ratio in matches[:30]:
        name = poi.get("name", {}).get("en") or poi.get("name", {}).get("de") or "?"
        print(f"  poi/{poi['identifier']:>8}  {name!r:60.60}  -> wd:{qid}   (dist={dist}m ratio={ratio})")

    lines = [
        "# owl:sameAs links found by searching Wikidata directly (geography +",
        "# name), for POIs the OSM-tag bridge (link_wikidata.py) couldn't",
        "# resolve -- see src/pipeline/enrich/link_wikidata_direct.py. RDF-star",
        "# annotated (nami:linkMethod/linkDistanceMeters/linkNameSimilarity)",
        "# with the evidence that justified accepting each link.",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix wd:  <http://www.wikidata.org/entity/> .",
        "@prefix nami: <https://example.org/nami/ontology#> .",
        "",
    ]
    for poi, qid, dist, ratio in matches:
        lines.append(
            f"<{config.DATA_IRI}poi/{poi['identifier']}> owl:sameAs wd:{qid} {{|\n"
            f"    nami:linkMethod \"wikidata-geo-name-search\" ;\n"
            f"    nami:linkDistanceMeters {dist} ;\n"
            f"    nami:linkNameSimilarity {ratio}\n"
            f"|}} ."
        )
    OUT_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nWritten {len(matches)} sameAs links -> {OUT_FILE}")


def load_pois_with_any_geo() -> list[dict]:
    """Like link_wikidata.load_pois but without requiring a valid osm_id --
    this pass doesn't need OSM at all."""
    import glob as _glob

    seen: dict[str, dict] = {}
    for f in sorted(config.RAW_DIR.glob("id_*.json")):
        for obj in json.loads(f.read_text()):
            ident = obj.get("identifier")
            geo = obj.get("geoCoordinates")
            has_geo = geo and geo.get("latitude") is not None and geo.get("longitude") is not None
            if ident and "@type" in obj and ident not in seen and has_geo:
                seen[ident] = obj
    return list(seen.values())


if __name__ == "__main__":
    main()

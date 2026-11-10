"""Link Zurich Tourism POIs to Wikidata via their existing OpenStreetMap
cross-reference (nami:osmId), where OSM's own wikidata= tag already
exists.

OSM node/way/relation ids are independent numbering spaces -- the same
number can name a different feature depending on element type, and the
source gives no type hint. So for each osm_id we fetch every candidate
and accept a wikidata= match only when exactly one is corroborated by
BOTH proximity (within MAX_DISTANCE_M) and name similarity (a difflib
ratio above NAME_THRESHOLD). Anything else is left unresolved rather
than guessed.

Run: python -m pipeline.enrich.link_wikidata
Output: data/geo-ontology/ABox/wikidata_links.ttl"""

from __future__ import annotations

import difflib
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

import config

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "NAMI-research-prototype/0.1 (evaluation)"
BATCH_SIZE = 30
BATCH_DELAY_S = 8
MAX_DISTANCE_M = 150
NAME_THRESHOLD = 0.6
OUT_FILE = config.RDF_DIR / "wikidata_links.ttl"
BATCH_CACHE_DIR = config.RAW_DIR / "osm_batches"


def load_pois() -> list[dict]:
    seen: dict[str, dict] = {}
    skipped_bad_osm_id = 0
    for f in sorted(config.RAW_DIR.glob("id_*.json")):
        for obj in json.loads(f.read_text()):
            ident = obj.get("identifier")
            geo = obj.get("geoCoordinates")
            has_geo = geo and geo.get("latitude") is not None and geo.get("longitude") is not None
            osm_id = obj.get("osm_id")
            if ident and "@type" in obj and ident not in seen and osm_id and has_geo:
                if not str(osm_id).strip().isdigit():
                    skipped_bad_osm_id += 1
                    continue
                seen[ident] = obj
    if skipped_bad_osm_id:
        print(f"Skipped {skipped_bad_osm_id} POI(s) with a malformed osm_id (source data quality issue).")
    return list(seen.values())


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def name_variants(poi: dict) -> list[str]:
    names = poi.get("name") or {}
    return [v.strip().lower() for v in names.values() if v]


def best_name_ratio(a_names: list[str], b_names: list[str]) -> float:
    best = 0.0
    for a in a_names:
        for b in b_names:
            best = max(best, difflib.SequenceMatcher(None, a, b).ratio())
    return best


def overpass_query(ids: list[str]) -> list[dict] | None:
    """None means "failed, give up on this batch for now" (as opposed to a
    successful empty result), so the caller can tell the two apart."""
    id_list = ",".join(ids)
    q = f"[out:json][timeout:60];\nnwr(id:{id_list});\nout center tags;"
    req = urllib.request.Request(
        OVERPASS_URL,
        data=q.encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    backoffs = [15, 45, 90]
    for attempt, wait in enumerate([0] + backoffs):
        if wait:
            print(f"    waiting {wait}s before retry...")
            time.sleep(wait)
        try:
            data = urllib.request.urlopen(req, timeout=70).read()
            return json.loads(data).get("elements", [])
        except Exception as exc:
            print(f"    ! Overpass batch failed (attempt {attempt + 1}/{len(backoffs) + 1}): {exc}")
    return None


def fetch_osm_candidates(osm_ids: list[str]) -> dict[str, list[dict]]:
    """Returns {raw_id: [candidate elements across node/way/relation]}.

    Each batch's raw response is cached to disk by its sorted id list, so a
    re-run after a partial failure only re-queries the batches that didn't
    succeed last time, instead of starting over.
    """
    BATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    by_id: dict[str, list[dict]] = {}
    unique_ids = sorted(set(osm_ids), key=int)
    failed_batches = []
    for i in range(0, len(unique_ids), BATCH_SIZE):
        batch = unique_ids[i : i + BATCH_SIZE]
        cache_file = BATCH_CACHE_DIR / f"batch_{batch[0]}_{batch[-1]}.json"
        if cache_file.exists():
            elements = json.loads(cache_file.read_text())
        else:
            print(f"  querying Overpass: {i + len(batch)}/{len(unique_ids)} ids...")
            elements = overpass_query(batch)
            if elements is None:
                failed_batches.append((i, batch))
                continue
            cache_file.write_text(json.dumps(elements))
            time.sleep(BATCH_DELAY_S)
        for el in elements:
            by_id.setdefault(str(el["id"]), []).append(el)

    if failed_batches:
        print(f"\n{len(failed_batches)} batch(es) never succeeded (network/rate-limit) -- "
              f"re-run the script to retry just those, cached batches will be skipped.")
    return by_id


def resolve(poi: dict, candidates: list[dict]) -> tuple[str, str] | None:
    """Returns (qid, reason) if exactly one candidate is corroborated, else None."""
    lat, lon = poi["geoCoordinates"]["latitude"], poi["geoCoordinates"]["longitude"]
    poi_names = name_variants(poi)
    passing = []
    for el in candidates:
        tags = el.get("tags", {})
        qid = tags.get("wikidata")
        if not qid:
            continue
        if el["type"] == "node":
            elat, elon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            elat, elon = center.get("lat"), center.get("lon")
        if elat is None or elon is None:
            continue
        dist = haversine_m(lat, lon, elat, elon)
        if dist > MAX_DISTANCE_M:
            continue
        osm_names = [v.strip().lower() for k, v in tags.items() if k == "name" or k.startswith("name:")]
        ratio = best_name_ratio(poi_names, osm_names) if osm_names else 0.0
        if ratio < NAME_THRESHOLD:
            continue
        passing.append((qid, el["type"], el["id"], dist, ratio, tags.get("name")))
    if len(passing) == 1:
        qid, etype, eid, dist, ratio, osm_name = passing[0]
        note = f"{etype}/{eid} osm_name={osm_name!r}"
        return qid, round(dist, 1), round(ratio, 3), note
    return None


def main() -> None:
    pois = load_pois()
    print(f"POIs with an osm_id: {len(pois)}")
    candidates_by_id = fetch_osm_candidates([p["osm_id"] for p in pois])

    matches = []
    for poi in pois:
        candidates = candidates_by_id.get(str(poi["osm_id"]), [])
        result = resolve(poi, candidates)
        if result:
            qid, dist, ratio, note = result
            matches.append((poi, qid, dist, ratio, note))

    print(f"\nResolved {len(matches)} / {len(pois)} POIs to a Wikidata QID.\n")
    print("--- sample for manual verification ---")
    for poi, qid, dist, ratio, note in matches[:30]:
        name = poi.get("name", {}).get("en") or poi.get("name", {}).get("de") or "?"
        print(f"  poi/{poi['identifier']:>8}  {name!r:60.60}  -> wd:{qid}   (dist={dist}m ratio={ratio} {note})")

    lines = [
        "# owl:sameAs links from Zurich Tourism POIs to Wikidata, derived from",
        "# OpenStreetMap's own wikidata= tag on the matching node/way/relation,",
        "# accepted only when uniquely corroborated by both proximity and name",
        "# similarity to this POI -- see src/pipeline/enrich/link_wikidata.py. Each",
        "# statement is RDF-star annotated (nami:linkMethod/linkDistanceMeters/",
        "# linkNameSimilarity) with the evidence that justified accepting it.",
        "@prefix owl: <http://www.w3.org/2002/07/owl#> .",
        "@prefix wd:  <http://www.wikidata.org/entity/> .",
        "@prefix nami: <https://example.org/nami/ontology#> .",
        "",
    ]
    for poi, qid, dist, ratio, note in matches:
        lines.append(
            f"<{config.DATA_IRI}poi/{poi['identifier']}> owl:sameAs wd:{qid} {{|\n"
            f"    nami:linkMethod \"osm-wikidata-tag\" ;\n"
            f"    nami:linkDistanceMeters {dist} ;\n"
            f"    nami:linkNameSimilarity {ratio}\n"
            f"|}} .  # {note}"
        )
    OUT_FILE.write_text("\n".join(lines) + "\n")
    print(f"\nWritten {len(matches)} sameAs links -> {OUT_FILE}")


if __name__ == "__main__":
    main()

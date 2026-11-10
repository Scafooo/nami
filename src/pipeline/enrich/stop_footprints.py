"""Adds to every stop of the ground truth the footprint of the place on
OpenStreetMap (the polygon of the building, park or cemetery), so that the
evaluation can credit a candidate inside the stop -- a grave in a cemetery, a
plaque on a house -- that Wikidata does not link to it. A footprint is kept
only if it contains the stop's own point and is smaller than MAX_AREA_KM2.

Run: python src/pipeline/enrich/stop_footprints.py"""

from __future__ import annotations

import json
import sys
import time
from math import cos, radians
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402

GROUND_TRUTH = config.ROOT_DIR / "evaluation" / "ground_truth_zurich.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = {"User-Agent": "NAMI-research-prototype/0.1 (evaluation)"}
MAX_AREA_KM2 = 0.5


def rings(geometry: dict) -> list[list[list[float]]]:
    """Outer rings of a Polygon or MultiPolygon, as [lon, lat] lists."""
    if geometry.get("type") == "Polygon":
        return [geometry["coordinates"][0]]
    if geometry.get("type") == "MultiPolygon":
        return [p[0] for p in geometry["coordinates"]]
    return []


def inside(lat: float, lon: float, footprint: list[list[list[float]]]) -> bool:
    """Ray casting over the outer rings."""
    hit = False
    for ring in footprint:
        for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
            if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                hit = not hit
    return hit


def area_km2(ring: list[list[float]]) -> float:
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * cos(radians(lat0)), 110.57
    return abs(sum((x1 * kx) * (y2 * ky) - (x2 * kx) * (y1 * ky) for (x1, y1), (x2, y2) in zip(ring, ring[1:]))) / 2


def footprint(stop: dict) -> list | None:
    city = "" if "Kilchberg" in stop["address"] else ", Zürich"
    for q in (stop["name"].split(" (")[0] + city, stop["address"] + city):
        r = requests.get(NOMINATIM, params={"q": q, "format": "json", "polygon_geojson": 1,
                                            "polygon_threshold": 0.00001, "limit": 5}, headers=UA, timeout=60)
        time.sleep(1.1)
        for hit in r.json() if r.ok else []:
            fp = rings(hit.get("geojson", {}))
            if fp and sum(area_km2(x) for x in fp) <= MAX_AREA_KM2 and inside(stop["lat"], stop["lon"], fp):
                return [[[round(x, 6), round(y, 6)] for x, y in ring] for ring in fp]
    return None


def main() -> None:
    gt = json.loads(GROUND_TRUTH.read_text())
    for it in gt["interests"]:
        for s in it["stops"]:
            if "footprint" not in s:
                s["footprint"] = footprint(s)
                print(f"{it['label'][:10]:10s} {s['name'][:45]:45s} {'polygon' if s['footprint'] else '-'}", flush=True)
    GROUND_TRUTH.write_text(json.dumps(gt, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

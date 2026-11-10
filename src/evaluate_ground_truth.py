"""Scores the best-first scenario report against the Zurich ground truth
(evaluation/ground_truth_zurich.json): themed walks compiled by independent
guides, one per interest.

A ground-truth stop is found by a candidate that has its QID (or one of its
alt_qids); by one inside it, located in or part of it on Wikidata (a grave in
a cemetery, a work in a museum: more specific than the stop, never less) or
whose point lies within the stop's footprint on OpenStreetMap; or
by one within --radius metres of it: most residences have no Wikidata item
and can only be matched by place.

For every single-interest scenario whose seed has a themed walk it reports
recall over all candidates and over the top k, precision at k, the rank of
each stop, and recall/precision of the first tour. Precision is a lower bound:
a walk lists what its authors chose to show, not every place related to the
person.

Run: python src/evaluate_ground_truth.py [--report PATH] [--radius M] [--k K ...] [--out PATH]"""

from __future__ import annotations

import argparse
import json
import time
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import config
from pipeline.enrich.stop_footprints import inside

GROUND_TRUTH = config.ROOT_DIR / "evaluation" / "ground_truth_zurich.json"
CONTAINERS = config.ROOT_DIR / "evaluation" / "wikidata_containers.json"
WD = "http://www.wikidata.org/entity/"


def containers(qids: set[str]) -> dict[str, list[str]]:
    """What each item is located in or part of on Wikidata (P276, P361, P131, one step),
    cached in evaluation/wikidata_containers.json so that scoring is reproducible."""
    import requests
    cache = json.loads(CONTAINERS.read_text()) if CONTAINERS.exists() else {}
    todo = sorted(q for q in qids if q not in cache and q.startswith("Q"))
    for k in range(0, len(todo), 200):
        chunk = todo[k:k + 200]
        query = ("SELECT ?x ?c WHERE { VALUES ?x { " + " ".join(f"wd:{q}" for q in chunk) + " } "
                 "?x wdt:P276|wdt:P361|wdt:P131 ?c . }")
        for attempt in range(20):
            r = requests.post("https://query.wikidata.org/sparql", data={"query": query}, timeout=120,
                              headers={"Accept": "application/sparql-results+json",
                                       "User-Agent": "NAMI-research-prototype/0.1 (evaluation)"})
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(int(r.headers.get("retry-after", 0)) or 30)
                continue
            r.raise_for_status()
            break
        for q in chunk:
            cache[q] = []
        for b in r.json()["results"]["bindings"]:
            cache[b["x"]["value"].removeprefix(WD)].append(b["c"]["value"].removeprefix(WD))
        CONTAINERS.write_text(json.dumps(cache, indent=0))
    return {q: cache.get(q, []) for q in qids}


INSIDE: dict[str, list[str]] = {}


def metres(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(a))


def match(stop: dict, cand: dict, radius: float) -> str | None:
    """How cand matches stop: 'qid', 'inside', 'place', or None."""
    ids = {stop.get("qid"), *stop.get("alt_qids", [])} - {None}
    if cand["id"] in ids:
        return "qid"
    if ids & ({cand.get("located_via")} | set(INSIDE.get(cand["id"], []))):
        return "inside"
    if stop.get("footprint") and cand.get("lat") is not None and inside(cand["lat"], cand["lon"], stop["footprint"]):
        return "inside"
    if cand.get("lat") is not None and metres(stop["lat"], stop["lon"], cand["lat"], cand["lon"]) <= radius:
        return "place"
    return None


def score_run(stops: list[dict], run: dict, radius: float, ks: list[int]) -> dict:
    cands = sorted(run["candidates"], key=lambda c: -c["score"])
    per_stop = []
    for s in stops:
        hit = next(((i, c, m) for i, c in enumerate(cands, 1) if (m := match(s, c, radius))), None)
        per_stop.append({
            "stop": s["name"], "has_qid": s.get("qid") is not None,
            "rank": hit and hit[0], "by": hit and hit[2], "candidate": hit and hit[1]["label"],
        })
    ranks = [p["rank"] for p in per_stop if p["rank"]]
    n = len(stops)
    out = {"stops": n, "candidates": len(cands), "recall": len(ranks) / n}
    for k in ks:
        top = cands[:k]
        out[f"recall@{k}"] = sum(r <= k for r in ranks) / n
        out[f"precision@{k}"] = (sum(any(match(s, c, radius) for s in stops) for c in top) / len(top)) if top else None
    tour = run["tours"][0]["stops"] if run.get("tours") else []
    by_id = {c["id"]: c for c in cands}
    tour_c = [by_id.get(t["id"], t) for t in tour]
    out["tour_stops"] = len(tour_c)
    out["tour_recall"] = sum(any(match(s, c, radius) for c in tour_c) for s in stops) / n
    out["tour_precision"] = (sum(any(match(s, c, radius) for s in stops) for c in tour_c) / len(tour_c)) if tour_c else None
    out["per_stop"] = per_stop
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", type=Path, default=config.ROOT_DIR / "output" / "evaluation_best_first.json")
    ap.add_argument("--radius", type=float, default=50.0)
    ap.add_argument("--k", type=int, nargs="*", default=[5, 10])
    ap.add_argument("--out", type=Path, default=config.ROOT_DIR / "output" / "ground_truth_scores.json")
    args = ap.parse_args()
    gt = json.loads(GROUND_TRUTH.read_text())
    runs = json.loads(args.report.read_text())["runs"]
    INSIDE.update(containers({c["id"] for r in runs for c in r.get("candidates", [])}))
    results = []
    for interest in gt["interests"]:
        run = next((r for r in runs if r["interests"] == [interest["id"]] and "error" not in r), None)
        if run is None:
            print(f"{interest['label']}: no single-interest run in the report")
            continue
        res = {"interest": interest["id"], "label": interest["label"], "scenarios": run["scenario_ids"],
               **score_run(interest["stops"], run, args.radius, args.k)}
        results.append(res)

    head = ["interest", "stops", "cands", "R", *[f"R@{k}" for k in args.k], *[f"P@{k}" for k in args.k], "tour", "R_tour", "P_tour"]
    print(" ".join(f"{h:>8}" for h in head))
    fmt = lambda x: "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))
    for r in results:
        row = [r["label"][:8], r["stops"], r["candidates"], r["recall"], *[r[f"recall@{k}"] for k in args.k],
               *[r[f"precision@{k}"] for k in args.k], r["tour_stops"], r["tour_recall"], r["tour_precision"]]
        print(" ".join(f"{fmt(x):>8}" for x in row))
    # micro-averaged recall, split by whether the stop has a Wikidata item at all
    for label, keep in (("all stops", lambda p: True), ("with QID", lambda p: p["has_qid"]), ("address only", lambda p: not p["has_qid"])):
        ps = [p for r in results for p in r["per_stop"] if keep(p)]
        if ps:
            print(f"micro recall, {label:12s}: {sum(bool(p['rank']) for p in ps)}/{len(ps)} = {sum(bool(p['rank']) for p in ps) / len(ps):.2f}")
    args.out.write_text(json.dumps({"radius_m": args.radius, "report": str(args.report), "results": results}, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()

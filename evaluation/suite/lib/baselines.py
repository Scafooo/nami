"""Step 2: lists that do not come from NAMI, on every benchmark interest, into runs/baselines/<name>.json.

- B_onehop: the places of the city that one Wikidata triple links to the interest, in either direction,
  ranked by the number of such triples, then by Wikipedia editions (no paths, no provenance).
- B_prepackaged: the same ten places for everyone, the items of the city described in most Wikipedia
  editions, administrative areas and events excluded (evaluation/popular_pois.json, a fixed list).
- B_ppr: personalized PageRank from the interest on its neighbourhood in G_P (two hops, read with the
  same Expand as NAMI; a neighbour linked to more than 2,000 triples is kept but not expanded, and at most
  300 neighbours, the least linked first, are), restart 0.15; the ten located places of highest rank. The standard graph-proximity
  ranking, without the value of a path or provenance."""

from __future__ import annotations

import json
import time
from collections import defaultdict

import requests

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
import settings
from lib import bench
from lib.pipeline import build

OUT = settings.RUNS / "baselines"
WD = "http://www.wikidata.org/entity/"
WEST, SOUTH, EAST, NORTH = code_config.ZURICH_BBOX
ONE_HOP = """SELECT ?place (COUNT(DISTINCT ?p) AS ?links) (SAMPLE(?sl) AS ?sitelinks) (SAMPLE(?c) AS ?coord) WHERE {
  { wd:%(q)s ?p ?place } UNION { ?place ?p wd:%(q)s }
  ?place wdt:P625 ?c .
  FILTER(geof:longitude(?c) >= %(w)s && geof:longitude(?c) <= %(e)s && geof:latitude(?c) >= %(s)s && geof:latitude(?c) <= %(n)s)
  OPTIONAL { ?place wikibase:sitelinks ?sl }
  FILTER(STRSTARTS(STR(?p), "http://www.wikidata.org/prop/direct/"))
} GROUP BY ?place"""
PPR_RESTART, PPR_ITER, HUB, MAX_LEVEL = 0.15, 50, 2000, 300
RUN_PPR = False          # dropped from the design on 2026-10-22, before any run (CHANGES.md)
RUN_PREPACKAGED = False  # dropped on 2026-11-05: the paper compares ways of making a guide for an interest


def sparql(q: str) -> list[dict]:
    for attempt in range(5):
        r = requests.post("https://query.wikidata.org/sparql", data={"query": q}, timeout=180,
                          headers={"Accept": "application/sparql-results+json", "User-Agent": code_config.USER_AGENT})
        time.sleep(1.0)
        if r.ok:
            return r.json()["results"]["bindings"]
        time.sleep(10 * (attempt + 1))
    r.raise_for_status()
    return []


def one_hop(qid: str) -> list[dict]:
    rows = []
    for b in sparql(ONE_HOP % {"q": qid, "w": WEST, "s": SOUTH, "e": EAST, "n": NORTH}):
        lon, lat = b["coord"]["value"].split("(")[1].rstrip(")").split()[:2]
        rows.append({"id": b["place"]["value"].rsplit("/", 1)[-1], "lat": float(lat), "lon": float(lon),
                     "score": int(b["links"]["value"]), "sitelinks": int(b.get("sitelinks", {}).get("value", 0))})
    return sorted(rows, key=lambda x: (-x["score"], -x["sitelinks"], x["id"]))[:settings.K]


def ppr(qid: str, source) -> list[dict]:
    seed = WD + qid
    edges = defaultdict(set)
    level, seen = {seed}, {seed}
    for depth in range(2):
        if depth == 1:                                 # a hub links everything: keep its edge, do not expand it
            deg = source.degree(level)
            level = sorted((n for n in level if deg.get(n, 0) <= HUB), key=lambda n: deg.get(n, 0))[:MAX_LEVEL]
        facts = source.expand(set(level))
        nxt = set()
        for node, fs in facts.items():
            for f in fs:
                a, b = f.subject, f.object
                if not isinstance(b, str) or not b.startswith("http"):
                    continue
                edges[a].add(b)
                edges[b].add(a)
                other = b if a == node else a
                if other not in seen:
                    seen.add(other)
                    nxt.add(other)
        level = nxt
    nodes = list(edges)
    if not nodes:
        return []
    rank = {n: (1.0 if n == seed else 0.0) for n in nodes}
    for _ in range(PPR_ITER):
        new = {n: (PPR_RESTART if n == seed else 0.0) for n in nodes}
        for n in nodes:
            out = edges[n]
            if out and rank[n]:
                share = (1 - PPR_RESTART) * rank[n] / len(out)
                for m in out:
                    new[m] += share
        rank = new
    located = source.locate(set(nodes) - {seed})
    best = sorted(located, key=lambda n: -rank.get(n, 0))[:settings.K]
    return [{"id": n.rsplit("/", 1)[-1], "lat": located[n].lat, "lon": located[n].lon, "score": round(rank[n], 6)}
            for n in best]


def save(name: str, runs: list[dict], meta: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps({"condition": name, "meta": meta, "runs": runs}, indent=1, ensure_ascii=False))


def main() -> None:
    interests = bench.interests()
    done = lambda n: (OUT / f"{n}.json").exists()
    if not done("B_onehop"):
        save("B_onehop", [{"profile_id": i["id"], "interests": [i["id"]], "places": one_hop(i["id"])} for i in interests],
             {"rule": __doc__.split("- B_onehop: ")[1].split("\n- ")[0]})
        print("B_onehop done", flush=True)
    if RUN_PREPACKAGED and not done("B_prepackaged"):
        pop = json.loads((CODE / "evaluation" / "popular_pois.json").read_text())
        places = [{"id": r["x"].rsplit("/", 1)[-1], "label": r["l"], "lat": float(r["c"].split()[1].rstrip(")")),
                   "lon": float(r["c"].split("(")[1].split()[0]), "score": int(r["n"])} for r in pop["rows"]][:settings.K]
        save("B_prepackaged", [{"profile_id": i["id"], "interests": [i["id"]], "places": places} for i in interests],
             {"rule": pop["rule"], "date": pop["date"]})
        print("B_prepackaged done", flush=True)
    if RUN_PPR and not done("B_ppr"):
        source = build(settings.PARAMS, {}).source
        runs = []
        for i in interests:
            t = time.perf_counter()
            try:
                runs.append({"profile_id": i["id"], "interests": [i["id"]], "places": ppr(i["id"], source),
                             "seconds": round(time.perf_counter() - t, 1)})
            except Exception as ex:
                runs.append({"profile_id": i["id"], "interests": [i["id"]], "error": f"{type(ex).__name__}: {ex}"})
            print(f"  B_ppr {i['id']:12s} {len(runs[-1].get('places', []))}", flush=True)
        save("B_ppr", runs, {"restart": PPR_RESTART, "iterations": PPR_ITER, "hops": 2, "hub_degree": HUB, "max_level": MAX_LEVEL})

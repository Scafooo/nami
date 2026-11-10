"""Step 4: every condition in one form, lists/<condition>.json, with the automatic checks.

A list gives, per profile, at most K places; a place has its reasons. A reason is either a path of NAMI
(kind "path": its facts, each with its source) or a fact a model states (kind "web": a URL and a quote).
For every reason the automatic check says whether it holds:
  path  every source on it opens and every sentence it was read from is on its source (audit_explanations)
  web   its quote is on the page it cites (process_rag.check, run in llm.process)
and whether the list shows it: a model alone shows all its reasons; with the check (llmcheck_*, hybrid_*)
only those that hold, and a place only if it keeps one. These automatic verdicts are then verified by
people (annotation A1): the paper reports the human ones.

Conditions: every runs/nami/*.json, runs/baselines/*.json and runs/llm/*.json found, and "guides", the
stops of the published guides (GUIDES_K per interest, drawn at random)."""

from __future__ import annotations

import json
from urllib.parse import urlparse

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
import settings
from build_human_round import source_link
from compare_tour_reasons import hop_ok
from lib import fetch

WEST, SOUTH, EAST, NORTH = code_config.ZURICH_BBOX
PAGES = settings.RUNS / "nami_pages_cache.json"
CHECKED = ("C1_nami", "C2_wikidata", "C3_noprov")     # NAMI lists whose reasons are checked automatically


def source_type(url_or_iri: str | None) -> str:
    s = url_or_iri or ""
    if "wikidata-unreferenced" in s or "/entity/statement/" in s:
        return "wikidata_unreferenced"
    if "wikidata-referenced" in s or "wikidata.org" in s:
        return "wikidata_referenced"
    host = urlparse(s).netloc
    if host.endswith("stadt-zuerich.ch"):
        return "city"
    if host.endswith("zuerich.com"):
        return "tourism"
    if "openstreetmap" in s or s.startswith("osm"):
        return "osm"
    if host.endswith("injected.example.org"):
        return "injected"
    return "web" if host else "other"


def in_area(lat, lon, box=(WEST, SOUTH, EAST, NORTH)) -> bool:
    return lat is not None and box[0] <= lon <= box[2] and box[1] <= lat <= box[3]


def nami_reasons(c: dict, cache: dict, check: bool = True) -> list[dict]:
    expl = [("path", e) for e in c.get("explanations", [])] + [("gathered", r["explanation"]) for r in c.get("reasons", [])]
    out = []
    for n, (how, e) in enumerate(expl):
        hops = e["hops"]
        ok = None
        if check:
            links = [source_link(h) for h in hops]
            if any(l and fetch.for_source(l.split("#")[0], cache) == fetch.RETRY for l in links):
                ok = fetch.RETRY                       # a source that cannot be read for now: checked next time
            else:
                ok = all(hop_ok(h, cache) for h in hops)
        out.append({"kind": "path", "gathered": how == "gathered", "text": e.get("text") or " ; ".join(
            f"{h['from']} --{h['via']}--> {h['to']}" for h in hops), "hops": hops, "value": e.get("value"),
            "source_types": [source_type(h.get("source_iri") or h.get("source_link")) for h in hops],
            "auto": fetch.RETRY if ok == fetch.RETRY else ("holds" if ok else ("fails" if ok is False else None)), "shown": True})
    return out


def nami_place(c: dict, rank: int, cache: dict, box=None, check: bool = True) -> dict:
    return {"rank": rank, "name": c.get("label"), "qid": c["id"] if c["id"][:1] == "Q" else None, "id": c["id"],
            "lat": c.get("lat"), "lon": c.get("lon"), "located_via": c.get("located_via"), "score": c.get("score"),
            "in_area": True, "reasons": nami_reasons(c, cache, check)}


def from_nami(rep: dict, cache: dict) -> dict:
    out = {}
    check = rep["condition"] in CHECKED      # the others are judged on their places (coverage, pertinence)
    for r in rep["runs"]:
        if "error" in r:
            out[r["profile_id"]] = {"interests": r["interests"], "error": r["error"], "places": []}
            continue
        out[r["profile_id"]] = {"interests": r["interests"], "avoid": r.get("avoid", []),
                                "frontier_max": r.get("stats", {}).get("frontier_max"), "stats": r.get("stats"),
                                "places": [nami_place(c, k, cache, check=check)
                                           for k, c in enumerate(r.get("candidates", [])[:settings.K], 1)]}
    return out


def from_baseline(rep: dict, labels) -> dict:
    ids = {p["id"] for r in rep["runs"] for p in r.get("places", [])}
    names = labels(ids)
    return {r["profile_id"]: {"interests": r["interests"], "places": [
        {"rank": k, "name": p.get("label") or names.get(p["id"], p["id"]), "qid": p["id"] if p["id"][:1] == "Q" else None,
         "id": p["id"], "lat": p["lat"], "lon": p["lon"], "score": p.get("score"), "in_area": True, "reasons": []}
        for k, p in enumerate(r.get("places", [])[:settings.K], 1)]} for r in rep["runs"]}


def web_reason(r: dict, shown_rule: str) -> dict:
    ok = r.get("check") == "verified"
    auto = fetch.RETRY if r.get("check") == fetch.RETRY else ("holds" if ok else "fails")
    return {"kind": "web", "text": r.get("reason", ""), "url": r.get("url"), "quote": r.get("quote"),
            "source_types": [source_type(r.get("url"))], "check": r.get("check"),
            "auto": auto, "shown": ok if shown_rule == "checked" else True}


def from_llm(rep: dict, checked: bool, c1: dict, cache: dict, box) -> dict:
    out = {}
    cands = {r["profile_id"]: {c["id"]: c for c in r.get("candidates", [])} for r in c1.get("runs", [])}
    for a in rep["answers"]:
        places = []
        for k, p in enumerate(a["places"], 1):
            rs = [web_reason(r, "checked" if checked or rep["setting"] == "hybrid" else "all") for r in p["reasons"]]
            if p.get("origin") == "graph":
                c = cands.get(a["interest_id"], {}).get(p["candidate_id"])
                if not c:
                    continue
                place = nami_place(c, k, cache)
                place["reasons"] += rs
                place["origin"] = "graph"
            else:
                g = p.get("geo") or {}
                if (checked or rep["setting"] == "hybrid") and not any(r["shown"] for r in rs):
                    continue                             # a place the check leaves without a reason is not shown
                place = {"rank": k, "name": p.get("name"), "address": p.get("address"), "qid": g.get("qid"),
                         "id": g.get("qid") or f"geo:{g.get('lat')},{g.get('lon')}", "lat": g.get("lat"), "lon": g.get("lon"),
                         "in_area": in_area(g.get("lat"), g.get("lon"), box), "geocoded": bool(g), "reasons": rs,
                         "origin": p.get("origin")}
            places.append(place)
        for k, p in enumerate(places, 1):
            p["rank"] = k
        out[a["interest_id"]] = {"interests": [a["interest_id"]], "places": places}
    return out


def save(name: str, family: str, profiles: dict, meta: dict | None = None) -> None:
    retry = sum(r.get("auto") == fetch.RETRY for p in profiles.values() for pl in p["places"] for r in pl["reasons"])
    if retry:
        print(f"  !! {name}: {retry} reasons whose source could not be read now: run the same step again later")
    settings.LISTS.mkdir(parents=True, exist_ok=True)
    (settings.LISTS / f"{name}.json").write_text(json.dumps(
        {"condition": name, "family": family, "meta": meta or {}, "profiles": profiles}, indent=1, ensure_ascii=False))
    n = sum(len(p["places"]) for p in profiles.values())
    print(f"  {name:24s} {family:10s} {len(profiles):3d} profiles {n:5d} places", flush=True)


def main() -> None:
    from lib.pipeline import build
    cache = json.loads(PAGES.read_text()) if PAGES.exists() else {}
    labels = None
    try:
        for f in sorted((settings.RUNS / "nami").glob("*.json")):
            rep = json.loads(f.read_text())
            save(f.stem, "city2" if f.stem.startswith("Z2_") else "nami", from_nami(rep, cache), rep.get("meta"))
            PAGES.write_text(json.dumps(cache, ensure_ascii=False))
        for f in sorted((settings.RUNS / "baselines").glob("*.json")):
            labels = labels or build(settings.PARAMS, {}).labeller
            save(f.stem, "baseline", from_baseline(json.loads(f.read_text()), labels))
        c1f = settings.RUNS / "nami" / "C1_nami.json"
        c1 = json.loads(c1f.read_text()) if c1f.exists() else {}
        c2f = settings.RUNS / "city2.json"
        box2 = tuple(json.loads(c2f.read_text())["bbox"]) if c2f.exists() else None
        for f in sorted((settings.RUNS / "llm").glob("*_run*.json")):
            rep = json.loads(f.read_text())
            setting, model, run = rep["setting"], rep["model"], rep["run"]
            box = box2 if setting == "city2" else (WEST, SOUTH, EAST, NORTH)
            if setting == "alone":
                save(f"llm_{model}_{run}", "llm", from_llm(rep, False, c1, cache, box))
            elif setting == "hybrid":
                save(f"hybrid_{model}_{run}", "hybrid", from_llm(rep, True, c1, cache, box))
            else:
                save(f"Z2_llm_{model}_{run}", "city2", from_llm(rep, False, c1, cache, box))
        guides()
        if settings.PAIRS and (settings.LISTS / "C1_nami.json").exists():
            interleave()
    finally:
        PAGES.write_text(json.dumps(cache, ensure_ascii=False))


def guides() -> None:
    """The published guides as a list: for each interest, GUIDES_K of its stops drawn at random (all if fewer),
    those with a point to visit (a stop without coordinates, such as a district, is left out)."""
    import random
    from lib import bench
    rng = random.Random(settings.SEED + 7)
    out = {}
    for it in bench.interests():
        stops = [s for s in it["stops"] if s.get("lat") is not None]
        chosen = rng.sample(stops, min(settings.GUIDES_K, len(stops)))
        out[it["id"]] = {"interests": [it["id"]], "places": [
            {"rank": k, "name": s["name"], "address": s.get("address"), "qid": s.get("qid"), "id": s.get("qid") or s["name"],
             "lat": s.get("lat"), "lon": s.get("lon"), "in_area": True, "reasons": [],
             "sources": [x["url"] for x in s.get("sources", [])]} for k, s in enumerate(chosen, 1)]}
    save("guides", "guides", out, {"rule": guides.__doc__})


def interleave() -> None:
    """The pair profiles without fusion: the first places of each interest's own list (C1), alternated, as a
    reference for M_pairs (does the noisy-or put first the places that serve both interests?)."""
    c1 = json.loads((settings.LISTS / "C1_nami.json").read_text())["profiles"]
    out = {}
    for a, b in settings.PAIRS:
        la, lb = c1.get(a, {}).get("places", []), c1.get(b, {}).get("places", [])
        places, seen = [], set()
        for x in (y for pair in zip(la + [None] * len(lb), lb + [None] * len(la)) for y in pair if y):
            if x["id"] not in seen and len(places) < settings.K:
                seen.add(x["id"])
                places.append(dict(x, rank=len(places) + 1))
        out[f"{a}+{b}"] = {"interests": [a, b], "places": places}
    save("M_interleave", "nami", out, {"rule": interleave.__doc__})

"""Human judging round (evaluation/BENCHMARK_PROTOCOL.md, "Human judging round" and its
addenda): the material three assessors judge, blind, in the questionnaire page.

Six sources per scored interest:
- experts: the 10 benchmark stops cited by most independent sources
  (build_symmetric_pool.expert_list);
- system: the stops of the frozen W+T run with value >= --min-score (0.5), at most 10;
- system+LLM: output/evaluation_best_first.RAG_<model>.json (src/process_rag.py), one per model;
- gemini, gpt: the saved LLM answers (evaluation/llm_baseline/), geocoded as in
  src/evaluate_llm_baseline.py.

A place named by several sources is one item (benchmark matching rule: same item,
inside, or within 50 m). Part A grades every item, shuffled, origin hidden, with ONE
neutral sentence per item (evaluation/human_round/connections.json, keyed
"<interest id>|<place>", <place> being the name shown: the first source's name).
Part B ranks the non-empty lists of an interest, each cut at its first 5, labelled
A-F in random order. Part C checks existence and link of every system, hybrid and LLM
item: one assessor each in rotation, a random 20% by all three. It also checks every source
the language models alone cite when asked for one (output/llm_sources_<model>.json, by
src/process_llm_sources.py): the citation joins the check of the item it names, or is a
check of its own when its place is not among the items; the automatic quote check is kept
in key.json and never shown.

Outputs (evaluation/human_round/):
- key.json: sources and ranks of every item, the labels of the lists (never published);
- claims.json: the raw claims per item, to write the neutral sentences from;
- questionnaire_data.json: what the page shows (no origin), written only when every
  item has its sentence.

Run: python src/build_human_round.py"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import config
from build_symmetric_pool import claim_of_candidate, claim_of_stop, expert_list
from evaluate_ground_truth import GROUND_TRUTH, INSIDE, containers, match, metres
from evaluate_llm_baseline import CACHE, DIR as LLM_DIR, geocode

OUT = config.ROOT_DIR / "evaluation" / "human_round"
WD = "http://www.wikidata.org/entity/"
SOURCES = ("experts", "system", "rag_gemini", "rag_gpt", "gemini", "gpt")
CUT_B = 5


def source_link(h: dict) -> str | None:
    """A page the assessor can open to check a hop."""
    if h.get("source_link"):
        return h["source_link"]
    iri = h.get("source_iri") or ""
    if iri.startswith(WD + "statement/"):
        return "https://www.wikidata.org/wiki/" + iri.rsplit("/", 1)[-1].split("-", 1)[0]
    if iri.startswith("http") and "wikidata.org" not in iri:
        return iri
    subj = (h.get("ids") or [""])[0]
    return "https://www.wikidata.org/wiki/" + subj[len(WD):] if subj.startswith(WD) else None


def explanation(e: dict) -> dict:
    return {"value": round(e["value"], 2),
            "hops": [{"from": h["from"], "via": h["via"], "to": h["to"], "period": h.get("period"),
                      "source": h.get("source"), "link": source_link(h), "evidence": h.get("evidence")}
                     for h in e["hops"]]}


def as_stop(x: dict) -> dict:
    """The stop side of match() for an item (a benchmark stop, or the point/item of a candidate)."""
    if x.get("stop"):
        return x["stop"]
    return {"qid": x.get("qid"), "lat": x.get("lat"), "lon": x.get("lon")}


STOPWORDS = {"the", "of", "and", "der", "die", "das", "zum", "zur", "und", "von", "site", "former", "ehem",
              "zürich", "zurich", "zuerich", "main", "building", "hauptgebäude", "today", "heute", "now"}


SYNONYMS = {"friedhof": "cemetery", "kirche": "church", "brücke": "bridge", "bahnhof": "station",
            "hauptbahnhof": "station", "universität": "university", "museum": "museum", "denkmal": "monument",
            "gedenktafel": "plaque", "landesmuseum": "national", "bibliothek": "library", "zentralbibliothek": "library"}


def tokens(name: str) -> set[str]:
    name = re.sub(r"s?(strasse|straße|str\.)", "strasse", name.lower().replace("ue", "ü"))   # Universität(s)strasse
    return {SYNONYMS.get(t, t) for t in re.findall(r"[a-zäöüß0-9]+", name) if (len(t) > 2 or t.isdigit()) and t not in STOPWORDS}


def best_item(items: list[dict], source: str, probe: dict, name: str, lat, lon, radius: float,
              ignore: set[str] = frozenset()) -> dict | None:
    """The item a new place is: the same Wikidata item first, then the benchmark rule (inside or
    within radius) ranked by name likeness and distance, then a like name within 150 m."""
    best, best_key = None, None
    nt = tokens(name) - ignore          # the interest's own name says nothing about which place it is
    for x in items:
        if source in x["ranks"]:
            continue
        stop = as_stop(x)
        nums_a, nums_b = {t for t in nt if t.isdigit()}, {t for t in tokens(x["place"]) if t.isdigit()}
        if nums_a and nums_b and not nums_a & nums_b:
            continue                    # different house numbers: different places
        ids = {stop.get("qid"), *stop.get("alt_qids", [])} - {None}
        xt = tokens(x["place"]) - ignore
        sim = len(nt & xt) / max(1, min(len(nt), len(xt))) if nt and xt else 0.0
        dist = (metres(x["lat"], x["lon"], lat, lon) if x.get("lat") is not None and lat is not None else None)
        if probe.get("id") in ids:
            k = (3, sim, 0)
        elif x.get("lat") is not None or ids or x.get("stop"):
            m = match(stop, probe, radius) if (probe.get("lat") is not None or probe.get("id") in ids) else None
            if m == "inside":
                k = (2, sim, -(dist or 0))
            elif m == "place" and (sim >= 0.5 or (dist is not None and dist <= 15)):
                k = (1.5, sim, -(dist or 0))
            elif (sim >= 0.6 and (dist is None or dist <= 150)) or (sim == 1.0 and dist is not None and dist <= 600):
                # a like name nearby; the same name within 600 m (geocoders often return the street)
                k = (1, sim, -(dist or 0))
            else:
                continue
        elif sim >= 0.6:
            k = (1, sim, 0)
        else:
            continue
        if best_key is None or k > best_key:
            best, best_key = x, k
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=config.ROOT_DIR / "output" / "evaluation_best_first.WT.json")
    ap.add_argument("--rag", type=Path, default=config.ROOT_DIR / "output", help="dir of evaluation_best_first.RAG_<model>.json")
    ap.add_argument("--min-score", type=float, default=0.5)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--radius", type=float, default=50.0)
    ap.add_argument("--seed", type=int, default=20261002)
    args = ap.parse_args()
    rnd = random.Random(args.seed)
    runs = {r["interests"][0]: r for r in json.loads(args.run.read_text())["runs"] if "error" not in r}
    rag = {m: {r["interests"][0]: r for r in json.loads((args.rag / f"evaluation_best_first.RAG_{m}.json").read_text())["runs"]}
           for m in ("gemini", "gpt")}
    INSIDE.update(containers({c["id"] for r in list(runs.values()) + [x for v in rag.values() for x in v.values()]
                              for c in r.get("candidates", [])}))
    cache = json.loads(CACHE.read_text())
    llm = {f.stem.split("_")[0]: {a["interest_id"]: a["places"] for a in json.loads(f.read_text())}
           for f in sorted(LLM_DIR.glob("*_20*.json"))}
    sourced = {m: {a["interest_id"]: a["places"] for a in json.loads((config.ROOT_DIR / "output" /
                                                                       f"llm_sources_{m}.json").read_text())["answers"]}
               for m in ("gemini", "gpt") if (config.ROOT_DIR / "output" / f"llm_sources_{m}.json").exists()}
    auto = []           # automatic quote checks of the LLM-alone citations (key.json only)
    key, claims, interests, checks = [], {}, [], []
    for it in json.loads(GROUND_TRUTH.read_text())["interests"]:
        if not it.get("scored"):
            continue
        items: list[dict] = []

        def add(source: str, rank: int, cand: dict | None, stop: dict | None, name: str, address, lat, lon,
                qid, claim: str, extra: dict) -> None:
            probe = cand or {"id": qid or name, "lat": lat, "lon": lon}
            same = best_item(items, source, probe, name, lat, lon, args.radius, tokens(it["label"]))
            x = same or {"place": name, "address": address, "lat": lat, "lon": lon, "qid": qid, "stop": stop,
                         "claims": [], "ranks": {}, "explain": {}}
            x["ranks"][source] = rank
            x["claims"].append(f"[{source}] {claim}")
            x["explain"][source] = extra
            if x.get("address") is None and address:
                x["address"] = address
            if same is None:
                items.append(x)

        for r, s in enumerate(expert_list(it, args.k), 1):
            add("experts", r, None, s, s["name"], s.get("address_fixed") or s.get("address"), s.get("lat"),
                s.get("lon"), s.get("qid"), claim_of_stop(it, s), {})
        system = [c for c in runs.get(it["id"], {}).get("candidates", []) if c["score"] >= args.min_score][:args.k]
        lists_in = [("system", system)] + [(f"rag_{m}", rag[m].get(it["id"], {}).get("candidates", [])[:args.k])
                                          for m in ("gemini", "gpt")]
        for src, cands in lists_in:
            for r, c in enumerate(cands, 1):
                reasons = [explanation(e) for e in c.get("explanations", [])[:3]]
                reasons += [dict(explanation(rs["explanation"]), label=rs["label"]) for rs in c.get("reasons", [])[:4]]
                facts = [{"fact": f["fact"], "url": f["url"], "quote": f["quote"]} for f in c.get("llm_facts", [])]
                claim = "; ".join(filter(None, [claim_of_candidate(c) if c.get("explanations") else "",
                                                *[f"{f['fact']} (\"{f['quote'][:160]}\")" for f in facts]]))
                name = c.get("llm_name") or c["label"]
                add(src, r, c, None, name, c.get("located_at") or c.get("llm_address"), c.get("lat"), c.get("lon"),
                    c["id"] if str(c["id"]).startswith("Q") else None, claim, {"reasons": reasons, "facts": facts})
        for model, ans in llm.items():
            for p in ans.get(it["id"], [])[:args.k]:
                g = geocode(p, cache)
                add(model, p.get("rank") or 0, None, None, p["name"], p.get("address"), g and g["lat"],
                    g and g["lon"], g.get("qid") if g else None, f"{p['name']}: {p.get('link', '')}",
                    {"link": p.get("link"), "confidence": p.get("confidence"), "located": g is not None})
        rnd.shuffle(items)
        for n, x in enumerate(items, 1):
            x["item"] = f"{it['id']}:{n:02d}"
        # the LLMs alone, asked for sources: each citation joins the item it names, or is checked alone
        loose = []
        for model, ans in sourced.items():
            for p in ans.get(it["id"], []):
                if not (p.get("url") and p.get("quote")):
                    continue
                g = p.get("geo")
                lat, lon = (g["lat"], g["lon"]) if g else (None, None)
                probe = {"id": (g or {}).get("qid") or p.get("name"), "lat": lat, "lon": lon}
                x = best_item(items, f"src_{model}", probe, p.get("name", ""), lat, lon, args.radius, tokens(it["label"]))
                cite = {"fact": p.get("link") or "", "url": p["url"], "quote": p["quote"]}
                cid = f"{it['id']}:{model}:{len(auto) + 1:03d}"
                auto.append({"citation": cid, "model": model, "interest_id": it["id"], "place": p.get("name"),
                             "url": p["url"], "auto_check": p.get("check"), "item": x["item"] if x else None})
                cite["citation"] = cid
                if x is not None:
                    x.setdefault("src_cited", []).append(cite)
                else:
                    loose.append({"place": p.get("name"), "address": p.get("address"), "lat": lat, "lon": lon,
                                  "cite": cite})
        lists = {}
        for src in SOURCES:
            ids = [x["item"] for x in sorted((x for x in items if src in x["ranks"]), key=lambda x: x["ranks"][src])]
            if ids:
                lists[src] = ids[:CUT_B]
        order = list(lists)
        rnd.shuffle(order)
        labels = {src: "ABCDEF"[i] for i, src in enumerate(order)}
        interests.append({"id": it["id"], "label": it["label"], "kind": it.get("kind"),
                          "items": [{"item": x["item"], "place": x["place"], "address": x["address"],
                                     "lat": x["lat"], "lon": x["lon"]} for x in items],
                          "lists": {labels[s]: lists[s] for s in order} if len(lists) > 1 else None})
        key.append({"interest_id": it["id"], "set": it.get("set"), "kind": it.get("kind"), "list_labels": labels,
                    "empty_lists": [s for s in SOURCES if s not in lists]})
        for x in items:
            claims[x["item"]] = {"key": f"{it['id']}|{x['place']}", "interest": it["label"], "place": x["place"],
                                 "claims": x["claims"]}
            probe = {"id": x.get("qid") or x["place"], "lat": x.get("lat"), "lon": x.get("lon")}
            walks = {src["url"] for st in it["stops"] if st.get("scored")
                     and (x.get("stop") is st or match(st, probe, args.radius)) for src in st["sources"]}
            key.append({"item": x["item"], "interest_id": it["id"], "ranks": x["ranks"], "sources_citing": len(walks)})
            checked = [s for s in ("system", "rag_gemini", "rag_gpt", "gemini", "gpt") if s in x["ranks"]]
            if checked or x.get("src_cited"):
                reasons = next((x["explain"][s]["reasons"] for s in ("system", "rag_gemini", "rag_gpt")
                                if s in x["explain"] and x["explain"][s].get("reasons")), [])
                cited = [f for s in ("rag_gemini", "rag_gpt") if s in x["explain"] for f in x["explain"][s]["facts"]]
                cited = list({(f["fact"], f["url"]): f for f in cited}.values()) + x.get("src_cited", [])
                links = sorted({x["explain"][s]["link"] for s in ("gemini", "gpt") if s in x["explain"]
                                and x["explain"][s].get("link")})
                checks.append({"item": x["item"], "interest_id": it["id"], "interest": it["label"],
                               "place": x["place"], "address": x["address"], "lat": x["lat"], "lon": x["lon"],
                               "reasons": reasons or [], "cited": cited, "stated": links})
        for n, lc in enumerate(loose, 1):
            checks.append({"item": f"{it['id']}:c{n:02d}", "interest_id": it["id"], "interest": it["label"],
                           "place": lc["place"], "address": lc["address"], "lat": lc["lat"], "lon": lc["lon"],
                           "reasons": [], "cited": [lc["cite"]], "stated": []})
    rnd.shuffle(checks)
    for i, c in enumerate(checks):
        c["assessor"] = "all" if rnd.random() < 0.2 else i % 3
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "key.json").write_text(json.dumps({"run": str(args.run), "rag": str(args.rag), "llm": sorted(llm),
                                              "min_score": args.min_score, "k": args.k, "seed": args.seed,
                                              "key": key, "llm_citations": auto}, indent=1, ensure_ascii=False))
    (OUT / "claims.json").write_text(json.dumps(claims, indent=1, ensure_ascii=False))
    conn_file = OUT / "connections.json"
    conn = json.loads(conn_file.read_text()) if conn_file.exists() else {}
    missing = sorted({c["key"] for c in claims.values() if not conn.get(c["key"])})
    n_items = sum(len(i["items"]) for i in interests)
    print(f"{len(interests)} interests, {n_items} places, {sum(1 for i in interests if i['lists'])} rankings, "
          f"{len(checks)} places to check ({sum(c['assessor'] == 'all' for c in checks)} by all three)")
    if missing:
        (OUT / "missing_connections.json").write_text(json.dumps(
            [{"key": c["key"], "claims": c["claims"]} for c in claims.values() if c["key"] in missing],
            indent=1, ensure_ascii=False))
        print(f"{len(missing)} places without a neutral sentence (missing_connections.json): page data not written")
        return
    for i in interests:
        for x in i["items"]:
            x["connection"] = conn[claims[x["item"]]["key"]]
    (OUT / "questionnaire_data.json").write_text(json.dumps(
        {"version": "human-round-2", "interests": interests, "checks": checks}, indent=1, ensure_ascii=False))
    print(f"-> {OUT / 'questionnaire_data.json'}")


if __name__ == "__main__":
    main()

"""Can a visitor trust a list? The same audit for every list, place by place (first ten):

- explained: the list says why the place is proposed;
- sourced: every statement of the explanation gives a source;
- sources open: every source can be opened (HTTP 200); a source that cannot is "dead" (404/410,
  unknown host) or "closed" (other errors, timeouts, bot checks), told apart;
- evidence found: every sentence the explanation quotes or was read from is on its source:
  for a text-extracted fact of the system, its evidence sentence on the page it cites, or in the
  open dataset it was read from; for a fact a model adds, its quoted sentence on the page it cites
  (the rule of process_rag.check). A Wikidata fact quotes no sentence: its reference must open.

Lists: W+T (first ten), system + LLM (Gemini, GPT), the models alone (no source asked: a stated
link only), and the models alone asked for sources. Pages are fetched once, politely, and cached
with their status in output/audit_pages.json.

Run: python src/audit_explanations.py"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlparse

import requests

import config
from build_human_round import source_link
from evaluate_llm_baseline import DIR as LLM_DIR
from process_rag import near_verbatim, norm

OUT = config.ROOT_DIR / "output"
PAGES = OUT / "audit_pages.json"
RAW = config.RAW_DIR
DATASETS = {"data.stadt-zuerich.ch": [RAW / "stadt_zuerich_blaue_tafeln.geojson", RAW / "stadt_zuerich_kunst_im_stadtraum.geojson",
                                      RAW / "stadt_zuerich_widmungstafeln.geojson"]}


def fetch(url: str, cache: dict) -> dict:
    if url not in cache:
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=30)
            text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", r.text)) if r.ok else None
            cache[url] = {"status": r.status_code, "text": text}
        except requests.exceptions.ConnectionError as ex:
            cache[url] = {"status": "unknown host" if "resolve" in str(ex).lower() or "Name or service" in str(ex) else "error", "text": None}
        except requests.RequestException:
            cache[url] = {"status": "error", "text": None}
        time.sleep(1.0)
        if len(cache) % 25 == 0:
            PAGES.write_text(json.dumps(cache, ensure_ascii=False))
    return cache[url]


def state(page: dict) -> str:
    s = page["status"]
    return "open" if s == 200 else "dead" if s in (404, 410, "unknown host") else "closed"


def on_page(sentence: str, page: dict) -> bool:
    if not page.get("text"):
        return False
    q, t = norm(sentence), norm(page["text"])
    return q in t or near_verbatim(q, t)


_dataset_text = {}
_dataset_records = {}


def _values(x) -> list[str]:
    if isinstance(x, dict):
        return [v for y in x.values() for v in _values(y)]
    if isinstance(x, list):
        return [v for y in x for v in _values(y)]
    return [str(x)] if x is not None else []


def in_dataset(sentence: str, url: str) -> bool:
    """The sentence is in the open dataset the fact was read from (its record, not a web page)."""
    host = urlparse(url).netloc
    if host not in _dataset_text:
        files = {"data.stadt-zuerich.ch": DATASETS["data.stadt-zuerich.ch"],
                 "www.openstreetmap.org": [RAW / "osm_zurich_memorials.json"],
                 "www.zuerich.com": sorted(RAW.glob("id_*.json"))}.get(host, [])     # Zurich Tourism's open data
        _dataset_text[host] = " | ".join(norm(" ".join(v.split())) for f in files if f.exists()
                                         for v in _values(json.loads(f.read_text())))
    q = norm(" ".join(sentence.split()))
    if _dataset_text[host] and (q in _dataset_text[host] or near_verbatim(q, _dataset_text[host])):
        return True
    # a statement composed from two fields of one record ("title: author", as extract_city_datasets
    # writes the author of a public artwork): every part in the same record
    parts = [norm(x) for x in sentence.split(": ") if x.strip()]
    if len(parts) < 2:
        return False
    if host not in _dataset_records:
        recs = []
        for f in DATASETS.get(host, []):
            if f.exists():
                for feat in json.loads(f.read_text()).get("features", []):
                    recs.append(norm(" ".join(" ".join(_values(feat.get("properties", {}))).split())))
        _dataset_records[host] = recs
    return any(all(p in r for p in parts) for r in _dataset_records[host])


def audit_graph_place(c: dict, cache: dict) -> dict:
    hops = [h for e in c.get("explanations", []) for h in e["hops"]]
    hops += [h for r in c.get("reasons", []) for h in r["explanation"]["hops"]]
    facts = c.get("llm_facts", [])
    out = {"explained": bool(hops or facts), "sourced": all(source_link(h) for h in hops) and all(f.get("url") for f in facts),
           "sources": [], "evidence": []}
    for h in hops:
        link = source_link(h)
        if not link:
            continue
        page = fetch(link.split("#")[0], cache)
        out["sources"].append(state(page))
        if h.get("evidence"):
            found = on_page(h["evidence"], page) or in_dataset(h["evidence"], link)
            out["evidence"].append("found" if found else ("not_found" if state(page) == "open" else "unchecked"))
    for f in facts:
        page = fetch(f["url"], cache)
        out["sources"].append(state(page))
        out["evidence"].append("found" if on_page(f["quote"], page) else ("not_found" if state(page) == "open" else "unchecked"))
    return out


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    src = [s for r in rows for s in r["sources"]]
    ev = [e for r in rows for e in r["evidence"]]
    return {"places": n,
            "explained": sum(r["explained"] for r in rows),
            "sourced": sum(r["explained"] and r["sourced"] for r in rows),
            "sources": len(src), "open": src.count("open"), "dead": src.count("dead"), "closed": src.count("closed"),
            "places_with_dead_source": sum("dead" in r["sources"] for r in rows),
            "evidence": len(ev), "found": ev.count("found"), "not_found": ev.count("not_found"), "unchecked": ev.count("unchecked"),
            "places_with_evidence_not_found": sum("not_found" in r["evidence"] for r in rows),
            # per place: every source opens and every sentence it quotes or was read from is on its source
            "places_verified": sum(r["explained"] and r["sourced"] and all(x == "open" for x in r["sources"])
                                   and all(e == "found" for e in r["evidence"]) for r in rows),
            "places_failed": sum(r["sourced"] and ("dead" in r["sources"] or "not_found" in r["evidence"]) for r in rows),
            "places_unopened": sum(r["sourced"] and "closed" in r["sources"] and "dead" not in r["sources"]
                                   and "not_found" not in r["evidence"] for r in rows)}


def main() -> None:
    cache = json.loads(PAGES.read_text()) if PAGES.exists() else {}
    runs = lambda f: [r for r in json.loads((OUT / f).read_text())["runs"] if "error" not in r]
    result = {}
    try:
        for name, f in (("W+T, first ten", "evaluation_best_first.WT.json"),
                        ("system+LLM, Gemini", "evaluation_best_first.RAG_gemini.json"),
                        ("system+LLM, GPT", "evaluation_best_first.RAG_gpt.json")):
            rows = [audit_graph_place(c, cache) for r in runs(f) for c in r.get("candidates", [])[:10]]
            result[name] = summary(rows)
            print(name, result[name], flush=True)
        for f in sorted(LLM_DIR.glob("*_20*.json")):          # the models alone: a stated link, no source
            rows = [{"explained": bool((p.get("link") or "").strip()), "sourced": False, "sources": [], "evidence": []}
                    for a in json.loads(f.read_text()) for p in a["places"][:10]]
            name = ("Gemini 3 Pro" if f.stem.startswith("gemini") else "GPT 6 Thinking")
            result[name] = summary(rows)
            print(name, result[name], flush=True)
        for m, name in (("gemini", "Gemini, asked for sources"), ("gpt", "GPT, asked for sources")):
            rows = []
            for a in json.loads((OUT / f"llm_sources_{m}.json").read_text())["answers"]:
                for p in a["places"][:10]:
                    if not p.get("url"):
                        rows.append({"explained": bool(p.get("link")), "sourced": False, "sources": [], "evidence": []})
                        continue
                    page = fetch(p["url"], cache)
                    rows.append({"explained": True, "sourced": True, "sources": [state(page)],
                                 "evidence": ["found" if on_page(p["quote"], page) else
                                              ("not_found" if state(page) == "open" else "unchecked")]})
            result[name] = summary(rows)
            print(name, result[name], flush=True)
    finally:
        PAGES.write_text(json.dumps(cache, ensure_ascii=False))
    (OUT / "audit_explanations.json").write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()

"""Step 3: the language models, by hand in their chats (decision D3), with the frozen prompts of v1.

prompts(): for every run (settings.LLM_RUNS) writes, under llm/,
    alone/run<r>/PROMPT_<b>.txt         the interest only (as in v1, round 5)
    hybrid/run<r>/PROMPT_<b>.txt        the interest and the first ten stops of NAMI (C1) with their reasons
    city2/run<r>/PROMPT_<b>.txt         the second city, interest only
and, next to each prompt, one empty answer file per model: <model>_block<b>.txt. Paste each answer whole,
from a new chat without memory, web search on; do not edit it.

process(): reads every answer, checks every quote on the page it cites (fetched now, cached in
runs/llm/page_cache.json) and geocodes every place (runs/llm/geocode_cache.json); writes
runs/llm/<setting>_<model>_run<r>.json. Nothing of the earlier rounds is read."""

from __future__ import annotations

import json
import re
import time

import requests

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
import settings
from build_rag_reasons_prompts import hop_text
from evaluate_llm_baseline import geocode
from lib import bench, fetch
from process_llm_sources import repair
from process_llm_tour_reasons import read as read_alone
from process_rag import OPEN_DATA_RECORD, check
from process_rag_reasons import read as read_hybrid

OUT = settings.RUNS / "llm"
PAGES = OUT / "page_cache.json"
GEO = OUT / "geocode_cache.json"
PER_STOP = 8

# The prompts of 2026-11-06: the task and the form of the answer only, nothing that points to where to look (no
# Wikidata identifier, no town named, no list of kinds of places). An interest is given by its name, with a neutral
# identifier (I01, I02, ...) that lib/llm.py maps back to the benchmark.
ALONE = """Plan a walking tour in Zurich for a visitor interested in each of the interests below. For each interest, list at most 10 places, and for each place all the reasons that link it to the interest. For each reason, give the URL of a web page that states it and the sentence of that page, quoted exactly.

Interests:
{interests}

Answer only with JSON, one object per interest:
[{{"interest_id": "I01", "places": [{{"rank": 1, "name": "...", "address": "...", "reasons": [{{"reason": "...", "url": "https://...", "quote": "..."}}]}}]}}]"""

HYBRID = """Plan a walking tour in Zurich for a visitor interested in each of the interests below. For each interest you get the places a system retrieved, each with the reasons that link it to the interest and their sources. You may keep, drop or add places, and add reasons. For each interest, list at most 10 places, and for each place all the reasons that link it to the interest. For each reason you add, give the URL of a web page that states it and the sentence of that page, quoted exactly.

Answer only with JSON, one object per interest; refer to a retrieved place by its id:
[{{"interest_id": "I01", "places": [
  {{"rank": 1, "origin": "graph", "id": "S1", "reasons": [{{"reason": "...", "url": "https://...", "quote": "..."}}]}},
  {{"rank": 2, "origin": "new", "name": "...", "address": "...", "reasons": [{{"reason": "...", "url": "https://...", "quote": "..."}}]}}
]}}]

Interests:
{interests}"""


def neutral_ids() -> dict[str, str]:
    """Benchmark id -> neutral id given to the models, in the order of the benchmark."""
    # over all scored interests, so that a batch restricted with NAMI_SUITE_INTERESTS keeps the same ids
    return {i["id"]: f"I{k:02d}" for k, i in enumerate([i for i in bench.load()["interests"] if i.get("scored")], 1)}


def interest_lines() -> list[tuple[str, str]]:
    """(benchmark id, "- I01: Albert Einstein") for every interest: its name only."""
    ids = neutral_ids()
    return [(i["id"], f"- {ids[i['id']]}: {i['label']}") for i in bench.interests()]


def blocks(items: list, n: int) -> list[list]:
    size = -(-len(items) // n)
    return [items[k:k + size] for k in range(0, len(items), size)]


def write_prompts(folder, texts: list[str]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for b, text in enumerate(texts, 1):
        (folder / f"PROMPT_{b}.txt").write_text(text + "\n")
        for m in settings.MODELS:
            f = folder / f"{m}_block{b}.txt"
            if not f.exists():
                f.write_text("")


def hybrid_context(run: dict) -> tuple[list[str], dict]:
    runs = {r["profile_id"]: r for r in run["runs"] if "error" not in r}
    parts, context = [], {}
    for qid, line in interest_lines():
        cands = runs.get(qid, {}).get("candidates", [])[:settings.K]
        out = [f"\n## {line[2:]}"]
        if not cands:
            out.append("No retrieved stops.")
        context[qid] = []
        for n, c in enumerate(cands, 1):
            sid = f"S{n}"
            context[qid].append({"id": sid, "candidate_id": c["id"], "label": c["label"], "score": c["score"]})
            where = f", {c['located_at']}" if c.get("located_at") else ""
            out.append(f"- {sid}: {c['label']}{where}")          # in NAMI's order; no value, nothing to interpret
            reasons = [("because", e) for e in c["explanations"]] + \
                      [(f"also here ({r['label']})", r["explanation"]) for r in c.get("reasons", [])]
            reasons.sort(key=lambda t: -t[1]["value"])
            for tag, e in reasons[:PER_STOP]:
                out.append(f"    {tag}: " + "; ".join(hop_text(h) for h in e["hops"]))
            if len(reasons) > PER_STOP:
                out.append(f"    ... and {len(reasons) - PER_STOP} more reasons of lower value")
        parts.append("\n".join(out))
    return parts, context


def prompts() -> None:
    c1 = settings.RUNS / "nami" / "C1_nami.json"
    lines = [l for _, l in interest_lines()]
    hybrid_text = HYBRID
    city2 = json.loads((settings.RUNS / "city2.json").read_text()) if (settings.RUNS / "city2.json").exists() else None
    for r in range(1, settings.LLM_RUNS + 1):
        write_prompts(settings.LLM / "alone" / f"run{r}",
                      [ALONE.format(interests="\n".join(b)) for b in blocks(lines, settings.LLM_BLOCKS)])
        if c1.exists():
            parts, context = hybrid_context(json.loads(c1.read_text()))
            folder = settings.LLM / "hybrid" / f"run{r}"
            write_prompts(folder, [hybrid_text.format(interests="\n".join(b)) for b in blocks(parts, settings.LLM_BLOCKS)])
            (folder / "context.json").write_text(json.dumps(context, indent=1, ensure_ascii=False))
        if city2:
            text = ALONE.replace(ALONE.splitlines()[0], f"You are planning walking tours in the city of {city2['name']}, "
                                                        f"Switzerland (the city itself, not the canton).")
            l2 = [f"- {i['label']} (id {i['id']})" for i in city2["interests"]]
            write_prompts(settings.LLM / "city2" / f"run{r}", [text.format(interests="\n".join(l2))])
    if not c1.exists():
        print("hybrid prompts need runs/nami/C1_nami.json: run `suite.py run --only C1_nami` first")
    print(f"prompts -> {settings.LLM}")


def process() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    pages = json.loads(PAGES.read_text()) if PAGES.exists() else {}
    geo = json.loads(GEO.read_text()) if GEO.exists() else {}
    try:
        for setting in ("alone", "hybrid", "city2"):
            for run_dir in sorted((settings.LLM / setting).glob("run*")):
                context = json.loads((run_dir / "context.json").read_text()) if setting == "hybrid" else {}
                for model in settings.MODELS:
                    files = [f for f in sorted(run_dir.glob(f"{model}_block*.txt")) if f.stat().st_size]
                    if not files:
                        continue
                    answers, how = [], {}
                    for f in files:
                        a, h = (read_hybrid if setting == "hybrid" else read_alone)(f)
                        back = {v: k for k, v in neutral_ids().items()}
                        b = re.search(r"block(\d+)", f.name).group(1)
                        order = re.findall(r"^#*\s*-?\s*(I\d+):", (run_dir / f"PROMPT_{b}.txt").read_text(), re.M)
                        if any(x.get("interest_id") not in back for x in a) and len(a) == len(order):
                            for x, n in zip(a, order):
                                x["interest_id"] = n
                            h += ", interests by position"
                        for x in a:                             # neutral id -> benchmark id
                            x["interest_id"] = back.get(x.get("interest_id"), x.get("interest_id"))
                        answers += a
                        how[f.name] = h
                    out = [answer(a, setting, context, pages, geo) for a in answers]
                    name = f"{setting}_{model}_{run_dir.name}"
                    (OUT / f"{name}.json").write_text(json.dumps({"setting": setting, "model": model, "run": run_dir.name,
                                                                  "files": how, "answers": out}, indent=1, ensure_ascii=False))
                    retry = sum(r["check"] == fetch.RETRY for a in out for p in a["places"] for r in p["reasons"])
                    print(f"  {name}: {len(out)} interests  {how}" + (f"  -- {retry} reasons not read yet: run llm-process again" if retry else ""), flush=True)
    finally:
        PAGES.write_text(json.dumps(pages, ensure_ascii=False))
        GEO.write_text(json.dumps(geo, ensure_ascii=False))


def geocode_city2(place: dict, cache: dict) -> dict | None:
    """As evaluate_llm_baseline.geocode, in the second city (OpenStreetMap Nominatim, bounded to its box)."""
    c2 = json.loads((settings.RUNS / "city2.json").read_text())
    w, s, e, n = c2["bbox"]
    queries = [q for q in ((place.get("address") or "").strip(), place["name"].split(" (")[0]) if q]
    for q in (q if c2["name"].lower() in q.lower() else f"{q}, {c2['name']}" for q in queries):
        if q not in cache:
            r = requests.get("https://nominatim.openstreetmap.org/search",
                             params={"q": q, "format": "json", "limit": 1, "extratags": 1, "viewbox": f"{w},{n},{e},{s}",
                                     "bounded": 1, "countrycodes": "ch"},
                             headers={"User-Agent": code_config.USER_AGENT}, timeout=30)
            time.sleep(1.1)
            hits = r.json() if r.ok else []
            cache[q] = hits[0] if hits else None
        h = cache[q]
        if h:
            return {"lat": float(h["lat"]), "lon": float(h["lon"]), "query": q,
                    "qid": (h.get("extratags") or {}).get("wikidata"), "osm": h.get("display_name", "")[:100]}
    return None


def answer(a: dict, setting: str, context: dict, pages: dict, geo: dict) -> dict:
    ids = {s["id"]: s for s in context.get(a.get("interest_id"), [])}
    places = []
    for p in sorted(a.get("places", []), key=lambda p: p.get("rank") or 99)[:settings.K]:
        rs = []
        for r in p.get("reasons", []):
            if setting == "hybrid" and (p.get("origin") or "").startswith("graph") and "-->" in (r.get("reason") or ""):
                continue                             # a reason of NAMI copied back from the prompt (its path, at times
                                                     # with NAMI's own source as URL), not one the model adds
            q = repair(r)
            if not (q.get("url") and q.get("quote")):
                q["check"] = "no_source"
            elif not OPEN_DATA_RECORD.match(q["url"]) and fetch.for_quote(q["url"], pages) == fetch.RETRY:
                q["check"] = fetch.RETRY             # fetched again by the next llm-process
            else:
                q["check"] = check({"url": q["url"], "quote": q["quote"]}, pages)
            rs.append(q)
        place = {"rank": p.get("rank"), "name": p.get("name", ""), "address": p.get("address"), "reasons": rs}
        if setting == "hybrid" and (p.get("origin") or "").startswith("graph"):
            s = ids.get(p.get("id"))
            if not s:
                continue                             # an id the prompt did not give: an altered context
            place.update(origin="graph", candidate_id=s["candidate_id"], name=s["label"])
        else:
            where = {"name": p.get("name", ""), "address": p.get("address")}
            place.update(origin="new" if setting == "hybrid" else "model",
                         geo=geocode_city2(where, geo) if setting == "city2" else geocode(where, geo))
        places.append(place)
    return {"interest_id": a.get("interest_id"), "interest": a.get("interest"), "places": places}

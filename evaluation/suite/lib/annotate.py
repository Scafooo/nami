"""Step 5: the workbooks for the annotators, under annotations/to_send/, and their keys under
annotations/keys/ (never sent). Every workbook is blind: no list, system or model is named, rows are
shuffled, and control items with a known answer are mixed in (settings.A*["controls"]).

A1 reasons   is the reason stated by the source shown, and does it link the place to the interest?
             Strata: NAMI (C1), each model alone, what each model adds in NAMI+model and the check shows,
             what the check drops of the models alone, the places provenance keeps out (C3 not in C1).
             Controls: a reason shown with the source of another reason (expected: not stated), a reason
             shown under another interest (expected: does not link).
A2 places    how pertinent is the place for a visitor with the interest (0-3), and does it exist today?
             Pool per profile: the places of every list judged (benchmark interests, the pairs, the second
             city) and the agreed stops of the guides, each place once. Split among settings.A2["annotators"]
             files, each place in settings.A2["per_place"] of them. Controls: a stop of an unrelated interest
             (expected 0-1), a stop four or more guides give (expected 2-3).
A3 facts     facts NAMI read from the city's texts and records: right entities, right relation?
             Controls: the subject replaced by another entity (expected: wrong entity).
A4 stops     benchmark stops against their source: does it name this place for the interest?
             Controls: the source of a stop of another interest (expected: no)."""

from __future__ import annotations

import json
import random
import re
from collections import defaultdict

import pyoxigraph as ox

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
import settings
from lib import bench, sheets
from lib.baselines import sparql

DESC = settings.RUNS / "descriptions.json"
YN = ["yes", "no"]
YPN = ["yes", "partly", "no"]
DATASETS = {"geo_widmungstafel": ("stadt_zuerich_widmungstafeln.geojson", "objectid", ""),
            "geo_kunst_im_stadtraum": ("stadt_zuerich_kunst_im_stadtraum.geojson", "objectid", ""),
            "geo_liste_der_informationstafeln_an_denkmalschutz_objekten": ("stadt_zuerich_blaue_tafeln.geojson", "bt_id", "bt_")}
SKIP = {"geometrie_gdo", "point_x", "point_y", "nr1_sort", "str1_sort", "id1"}


# ------------------------------------------------------------------ helpers
def load_list(name: str) -> dict:
    f = settings.LISTS / f"{name}.json"
    return json.loads(f.read_text())["profiles"] if f.exists() else {}


def labels_of_interests() -> dict:
    out = {i["id"]: i["label"] for i in bench.load()["interests"]}
    c2 = settings.RUNS / "city2.json"
    if c2.exists():
        out.update({i["id"]: i["label"] for i in json.loads(c2.read_text())["interests"]})
    return out


def records() -> dict:
    idx = {}
    for name, (f, key, pre) in DATASETS.items():
        path = code_config.RAW_DIR / f
        if not path.exists():
            continue
        for feat in json.loads(path.read_text())["features"]:
            p = feat["properties"]
            idx[(name, pre + str(p.get(key)))] = "\n".join(f"{a}: {' '.join(str(b).split())}" for a, b in p.items()
                                                         if b not in (None, "") and a not in SKIP)
    return idx


def record_text(url: str, recs: dict) -> str:
    if url and "data.stadt-zuerich.ch/dataset/" in url and "#" in url:
        ds, frag = url.split("/dataset/")[1].split("#", 1)
        return recs.get((ds, frag), "")
    return ""


def describe(qids: set[str]) -> dict:
    cache = json.loads(DESC.read_text()) if DESC.exists() else {}
    todo = sorted(q for q in qids if q and q.startswith("Q") and q not in cache)
    for k in range(0, len(todo), 200):
        chunk = todo[k:k + 200]
        rows = sparql("SELECT ?x ?d WHERE { VALUES ?x { " + " ".join(f"wd:{q}" for q in chunk) +
                      ' } ?x schema:description ?d FILTER(LANG(?d) = "en") }')
        got = {r["x"]["value"].rsplit("/", 1)[-1]: r["d"]["value"] for r in rows}
        for q in chunk:
            cache[q] = got.get(q, "")
        DESC.write_text(json.dumps(cache, ensure_ascii=False))
    return cache


def osm(lat, lon) -> str:
    return f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=18/{lat:.6f}/{lon:.6f}" if lat is not None else ""


def hop_lines(hops: list[dict], recs: dict) -> tuple[str, str, str]:
    """The reason as text, its sources (one per fact) and what to read for each source."""
    text = " ; ".join(f"{h['from']} --{h['via']}--> {h['to']}" for h in hops)
    srcs, read = [], []
    for n, h in enumerate(hops, 1):
        link = h.get("source_link") or ""
        srcs.append(f"[{n}] {h.get('source') or link}  {link}")
        rec = record_text(link, recs)
        if rec:
            read.append(f"[{n}] open data record:\n{rec}")
        if h.get("evidence"):
            read.append(f"[{n}] sentence: {h['evidence']}")
    return text, "\n".join(srcs), "\n".join(read)


def write_key(task: str, key: dict) -> None:
    settings.ANNOT_KEYS.mkdir(parents=True, exist_ok=True)
    (settings.ANNOT_KEYS / f"{task}_key.json").write_text(json.dumps(key, indent=1, ensure_ascii=False))


def write_both(name: str, task: str, ins: list[str], cols: list[dict], rows: list[list], key: dict) -> None:
    """The workbook for the annotators."""
    sheets.write(settings.ANNOT_OUT / name, ins, cols, rows)


# ------------------------------------------------------------------ A1 reasons
def reason_id(place: dict, r: dict) -> tuple:
    """One reason, whichever lists show it: a path by its facts, a model's fact by its page and quote."""
    if r["kind"] == "path":
        return ("path", place["id"], tuple(tuple(h.get("ids") or (h["from"], h["via"], h["to"])) for h in r["hops"]))
    return ("web", place["id"] or place.get("name"), r.get("url"), r.get("quote"))


def a1(rng: random.Random, recs: dict, names: dict) -> tuple:
    """Every reason shown at `places_per_list` places of each list of Table 3, each reason once whatever the
    lists that show it; and `dropped` reasons the check rejects from the models alone."""
    single = {i["id"] for i in bench.interests()}
    items: dict[tuple, dict] = {}
    places_key: dict[str, list] = {}
    for lst in settings.A1["lists"]:
        cands = [(pid, p) for pid, prof in load_list(lst).items() if pid in single
                 for p in prof["places"] if any(r["shown"] for r in p["reasons"])]
        chosen = rng.sample(cands, min(settings.A1["places_per_list"], len(cands)))
        places_key[lst] = []
        for pid, p in chosen:
            ids = []
            for r in p["reasons"]:
                if not r["shown"]:
                    continue
                k = reason_id(p, r)
                it = items.setdefault(k, {"profile": pid, "place": p, "reason": r, "member": []})
                it["member"].append(lst)
                ids.append(k)
            places_key[lst].append({"profile": pid, "place_id": p["id"], "reasons": ids})
    # the reasons the check does not show in NAMI + model: how many of them people find stated all the same
    dropped = [(pid, p, r) for m in settings.MODELS for pid, prof in load_list(f"hybrid_{m}_run1").items() if pid in single
               for p in prof["places"] for r in p["reasons"] if r["kind"] == "web" and not r["shown"]]
    for pid, p, r in rng.sample(dropped, min(settings.A1["dropped"], len(dropped))):
        it = items.setdefault(reason_id(p, r), {"profile": pid, "place": p, "reason": r, "member": []})
        it["member"].append("dropped")
    items_l = list(items.items())
    verified = [it for _, it in items_l if it["reason"]["kind"] == "web" and it["reason"].get("auto") == "holds"]
    controls = []
    for j in range(max(2, int(settings.A1["controls"] * len(items_l))) if items_l and settings.A1["controls"] else 0):
        if j % 2 == 0 and len(verified) >= 2:   # another reason's source: the page does not state this reason
            a, b = rng.sample(verified, 2)
            r = dict(a["reason"], url=b["reason"]["url"], quote=b["reason"]["quote"])
            controls.append({**a, "reason": r, "member": ["control"], "expect": {"Q2 page states the reason": "no"}})
        else:                                    # another interest: the reason does not link the place to it
            _, a = rng.choice(items_l)
            others = [q for q in single if q != a["profile"]]
            if others:
                controls.append({**a, "profile": rng.choice(others), "member": ["control"],
                                 "expect": {"Q3 reason links place to interest": "no"}})
    rows_all = [dict(it, _k=k) for k, it in items_l] + controls
    rng.shuffle(rows_all)
    cols = [{"name": "id", "width": 7}, {"name": "interest", "width": 22}, {"name": "place", "width": 24},
            {"name": "reason", "width": 48}, {"name": "source (open each link)", "width": 50},
            {"name": "what to read (if any)", "width": 50},
            {"name": "Q1 source opens", "width": 12, "answer": True, "options": YN},
            {"name": "Q2 page states the reason", "width": 14, "answer": True, "options": YPN},
            {"name": "Q3 reason links place to interest", "width": 14, "answer": True, "options": YPN},
            {"name": "evidence: sentence of the source that states it (if Q2 yes or partly)", "width": 50, "answer": True},
            {"name": "notes", "width": 30, "answer": True}]
    rows, key, rid_of = [], {}, {}
    for i, it in enumerate(rows_all, 1):
        rid = f"R{i:03d}"
        r = it["reason"]
        if r["kind"] == "path":
            text, srcs, read = hop_lines(r["hops"], recs)
        else:
            text, srcs = r.get("text", ""), r.get("url") or ""
            read = f'quoted sentence: "{r.get("quote", "")}"\n' + record_text(r.get("url"), recs)
        rows.append([rid, names.get(it["profile"], it["profile"]), it["place"]["name"], text, srcs, read.strip(),
                     "", "", "", "", ""])
        key[rid] = {"lists": it["member"], "profile": it["profile"], "place_id": it["place"]["id"],
                    "reason_kind": r["kind"], "auto": r.get("auto"), "source_types": r.get("source_types"),
                    "expect": it.get("expect")}
        if "_k" in it:
            rid_of[it["_k"]] = rid
    ins = [
        "Task A1: reasons for visiting a place. Each row is one reason given for visiting a place for an interest.",
        "Open the source of the reason (column 'source'; for a chain of facts, every numbered source) and read it.",
        "Where 'what to read' gives a sentence, it is the sentence the source is said to contain: find it in the source and check that it is there and says the same. An open data record is shown as the source contains it.",
        "Q1 source opens: 'yes' if every source opens (a page, a record), 'no' if one does not (error, gone, login).",
        "Q2 page states the reason: 'yes' if the source says what the reason says, 'partly' if it says part of it,",
        "    'no' if it does not say it. For a Wikidata source, the statement on the item page counts.",
        "Q3 reason links place to interest: 'yes' if the reason, if true, is a reason to visit this place for this",
        "    interest; 'partly' if the link is weak or indirect; 'no' if it is not a reason for this interest.",
        "Evidence: when Q2 is 'yes' or 'partly', paste the sentence of the source that states it. Required.",
        "Work alone, do not discuss the rows with anyone, do not use any AI tool. Leave a cell empty if unsure and say why in notes.",
    ]
    write_both("A1_reasons.xlsx", "A1", ins, cols, rows, key)
    places = {lst: [dict(pl, reasons=[rid_of[k] for k in pl["reasons"]]) for pl in pls] for lst, pls in places_key.items()}
    write_key("A1", {"rows": key, "places": places})
    print(f"  A1: {len(rows)} reasons ({len(controls)} controls) at "
          + ", ".join(f"{len(v)} places of {k}" for k, v in places.items()))


# ------------------------------------------------------------------ A2 places
A2_LISTS = ["guides", "C1_nami", "B_onehop", "llm_gpt_run1", "llm_gemini_run1",   # Table 3
            "hybrid_gpt_run1", "hybrid_gemini_run1",
            "C2_wikidata", "A1_no_eta", "A2_no_decay", "A3_no_derived", "A4_class_up", "C3_noprov"]     # Table 4


GUIDE_PREFIX = re.compile(r"^\s*((audio ?walk |route )?(station|stop|stopp|nr\.?|no\.?|halt|posten)\s*\d+[a-z]?\s*(\([^)]*\))?\s*('[^']*'|\"[^\"]*\")?\s*[:;.,\-–]\s*)", re.I)
GUIDE_META = re.compile(r"(?i)(\bguide\b|\bguides\b|guidebook|brunnenguide|brunnen-nr|\btour\b|\btours\b|tour page|"
                        r"audio|\bon the walk\b|\bof the walk\b|\bthe route\b|route stop|\bmap point\b|\bnumbered map\b|"
                        r"\bbrochure\b|\bflyer\b|\bleaflet\b|\blisted on\b|\bstadtrundgang\b|\brundgang\b|\bführung\b|"
                        r"^stationen\b|^route\b|^station\b|^walk\b|\bstation (of|at|on)\b|\bcity walk\b|\bthe walk\b|\bder walk\b|\b\w+ walk (ends|endet|startet|starts|continues|leads|strolls)\b|"
                        r"\bend of the \w+ walk\b|\bziid walk\b|\bstationen\b|\b(first|final|erste|zweite|letzte) station\b|"
                        r"\bendstation\b|\broute\b|\blisted among\b|\btwo stations\b|\bkath\.ch\b|reproduced on|\bepisode \d)")


def clean_guide(text: str) -> str:
    """The content of a guide's note on a stop, without what says it comes from a guide (its station number,
    the walk, the brochure): the assessors are blind to the lists."""
    t = GUIDE_PREFIX.sub("", text or "").strip()
    parts = [x.strip() for x in re.split(r"(?<=[a-z\)'\"][.;])\s+(?=[A-ZÄÖÜ])", t) if x.strip()]
    keep = []
    for x in parts:                                  # and, within a sentence, its clauses
        cl = [c.strip() for c in x.split("; ") if not GUIDE_META.search(c) and re.search(r"[A-Za-zÄÖÜäöü]{3}", c)]
        if cl:
            keep.append("; ".join(cl).rstrip(";") + ("" if cl[-1].endswith((".", ")", "'")) else ""))
    return " ".join(keep).strip()


def a2(rng: random.Random, names: dict) -> None:
    items: dict[tuple, dict] = {}

    by_id = {i["id"]: i for i in bench.interests()}

    def guide_text(pid, place):
        """What the guides say of this stop (the 'connection' of each source of the benchmark)."""
        nm = bench.norm_name(place.get("name") or "")
        for s in (by_id.get(pid) or {}).get("stops", []):
            if (place.get("qid") and s.get("qid") == place["qid"]) or bench.norm_name(s.get("name") or "") == nm:
                return [t for t in (clean_guide(x["connection"]) for x in s.get("sources", []) if x.get("connection")) if t]
        return []

    def why(lst, pid, place):
        """The reasons a visitor of this list would read, as plain text, without sources (added 2026-11-09:
        the assessors asked for them, as a pin alone does not say why a place is proposed)."""
        if lst == "guides":
            return guide_text(pid, place)
        out = []
        for r in place.get("reasons", []):
            if not r.get("shown", True):
                continue
            if r.get("hops"):
                out.append(" ; ".join(f"{h['from']} --{h['via']}--> {h['to']}" + (f" [{h['period']}]" if h.get("period") else "")
                                      for h in r["hops"]))
            elif r.get("text"):
                out.append(r["text"])
        return out

    def add(pid, interests, place, source):
        name = bench.norm_name(place.get("name") or "")
        k = (pid, place.get("qid") or name or place["id"])
        it = items.setdefault(k, {"profile": pid, "interests": interests, "place": place, "from": set(), "why": []})
        it["from"].add(source)
        for t in why(source, pid, place):
            if t not in it["why"]:
                it["why"].append(t)

    full = settings.A2.get("full_lists") or A2_LISTS
    cap = settings.A2.get("sample_secondary")
    for lst in A2_LISTS:
        places = [(pid, prof, p) for pid, prof in load_list(lst).items() for p in prof["places"] if p.get("in_area", True)]
        if lst not in full and cap:
            known = [x for x in places if (x[0], x[2].get("qid") or bench.norm_name(x[2].get("name") or "") or x[2]["id"]) in items]
            new = [x for x in places if x not in known]
            places = known + rng.sample(new, min(cap, len(new)))      # a sample of what only this list adds
        for pid, prof, p in places:
            add(pid, prof["interests"], p, lst)
    # controls: an unrelated interest's well-attested stop (low), a stop four or more guides give (high)
    controls = []
    interests = list(by_id)
    nc = max(4, int(settings.A2["controls"] * len(items))) if settings.A2["controls"] else 0
    for j in range(nc):
        pid = rng.choice(interests)
        if j % 2 == 0:
            others = [q for q in interests if q != pid and by_id[q]["kind"] != by_id[pid]["kind"]]
            stops = bench.agreed(by_id[rng.choice(others)]) if others else []
            if stops:
                s = rng.choice(stops)
                controls.append({"profile": pid, "interests": [pid], "from": {"control"}, "expect": [0, 1],
                                 "place": {"name": s["name"], "address": s.get("address"), "qid": s.get("qid"),
                                           "id": "ctl:" + (s.get("qid") or s["name"]), "lat": s.get("lat"), "lon": s.get("lon")}})
        else:
            strong = [s for s in by_id[pid]["stops"] if len({x["url"] for x in s.get("sources", [])}) >= 4]
            if strong:
                s = rng.choice(strong)
                k = (pid, s.get("qid") or bench.norm_name(s["name"]))
                if k in items:
                    items[k]["expect"] = [2, 3]
    # no description column (2026-11-09): taken from Wikidata, it was wrong for some geocoded places and more
    # often present for NAMI's places than for the others; every place is judged on name, address and map
    all_items = list(items.values()) + controls
    rng.shuffle(all_items)
    na, per = settings.A2["annotators"], settings.A2["per_place"]
    files = defaultdict(list)
    key = {}
    for i, it in enumerate(all_items, 1):
        rid = f"P{i:04d}"
        start = i % na
        for a in range(per):
            files[(start + a) % na + 1].append((rid, it))
        p = it["place"]
        key[rid] = {"profile": it["profile"], "interests": it["interests"], "place_id": p.get("id"), "qid": p.get("qid"),
                    "name": p.get("name"), "lat": p.get("lat"), "lon": p.get("lon"), "lists": sorted(it["from"]),
                    "expect": it.get("expect")}
    cols = [{"name": "id", "width": 8}, {"name": "visitor interested in", "width": 30}, {"name": "place", "width": 30},
            {"name": "address", "width": 26}, {"name": "why it is proposed", "width": 60}, {"name": "map", "width": 22},
            {"name": "G pertinence (0-3)", "width": 12, "answer": True, "options": ["0", "1", "2", "3", "?"]},
            {"name": "E exists and can be visited or seen today", "width": 14, "answer": True, "options": ["yes", "no", "unsure"]},
            {"name": "notes", "width": 30, "answer": True}]
    ins = [
        "Task A2: places for a visitor. Each row is a place proposed to a visitor with the interest given (one or two).",
        "'why it is proposed' gives the reasons the tour gives for the place. They help you, but judge the place for the",
        "interest, not how well the reasons are written; you may also use what you know and what you can look up.",
        "G pertinence for a visitor with this interest (with both, when two are given):",
        "    3 = a must-see for this interest;  2 = clearly worth a visit for it;  1 = loosely related;",
        "    0 = not related, or not a place to visit;  ? = you cannot judge (say why in notes).",
        "E: does the place exist and can it be visited or seen today (a building, a plaque, a grave, a square)?",
        "Places appear in random order; the same place may appear for different interests: judge each row on its own.",
        "Work alone, do not discuss the rows with anyone, do not use any AI tool.",
    ]
    order_rng = random.Random(settings.SEED + 22)
    within = {rid: order_rng.random() for rid in key}
    for a, rows in sorted(files.items()):
        out = []
        # 2026-11-10: grouped by interest (in alphabetical order), places in one random order within each,
        # the same for every person, so that an interest is looked up once
        rows = sorted(rows, key=lambda x: (" + ".join(names.get(q, q) for q in x[1]["interests"]).lower(), within[x[0]]))
        for rid, it in rows:
            p = it["place"]
            w = list(it.get("why", []))
            rng.shuffle(w)                                   # no order that tells the lists apart
            out.append([rid, " + ".join(names.get(q, q) for q in it["interests"]), p.get("name") or "", p.get("address") or "",
                        "\n".join("- " + t for t in w[:8]), osm(p.get("lat"), p.get("lon")), "", "", ""])
        write_both(f"A2_places_annotator{a}.xlsx", "A2", ins, cols, out, key)
    write_key("A2", {"rows": key, "files": {a: [rid for rid, _ in rows] for a, rows in files.items()}})
    print(f"  A2: {len(all_items)} places ({len(controls)} low controls), {na} files of ~{len(files[1])} rows")


# ------------------------------------------------------------------ A3 facts
def a3(rng: random.Random, recs: dict, labels) -> None:
    store = ox.Store()
    for f in sorted((code_config.GEO_ONTOLOGY_DIR / "extracted").glob("*.ttl")):
        store.load(f.read_bytes(), format=ox.RdfFormat.TURTLE)
    q = """PREFIX nami: <https://example.org/nami/ontology#>
    SELECT ?s ?p ?o ?src ?ev ?c WHERE { << ?s ?p ?o >> nami:source ?src . OPTIONAL { << ?s ?p ?o >> nami:evidence ?ev }
      OPTIONAL { << ?s ?p ?o >> nami:confidence ?c } }"""
    facts = [{"s": r["s"].value, "p": r["p"].value, "o": r["o"].value, "src": r["src"].value,
              "ev": r["ev"].value if r["ev"] else "", "conf": r["c"].value if r["c"] else ""} for r in store.query(q)]
    facts = [f for f in facts if f["ev"] or record_text(f["src"], recs)]
    sample = rng.sample(facts, min(settings.A3["facts"], len(facts)))
    controls = []
    for j in range(max(2, int(settings.A3["controls"] * len(sample)))):
        a, b = rng.sample(sample, 2)
        controls.append({**a, "s": b["s"], "expect": {"Q1 subject is the entity the text means": "no"}})
    rows_all = [dict(f) for f in sample] + controls
    rng.shuffle(rows_all)
    loc = lambda x: x.rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    names = labels({loc(f[k]) for f in rows_all for k in ("s", "o")})
    link = lambda x: f"https://www.wikidata.org/wiki/{loc(x)}" if "wikidata.org/entity/Q" in x else x
    cols = [{"name": "id", "width": 7}, {"name": "subject", "width": 26}, {"name": "subject link", "width": 30},
            {"name": "relation", "width": 16}, {"name": "object", "width": 26}, {"name": "object link", "width": 30},
            {"name": "text it was read from", "width": 60}, {"name": "source", "width": 40},
            {"name": "Q1 subject is the entity the text means", "width": 14, "answer": True, "options": ["yes", "no", "unsure"]},
            {"name": "Q2 object is the entity the text means", "width": 14, "answer": True, "options": ["yes", "no", "unsure"]},
            {"name": "Q3 text states this relation", "width": 14, "answer": True, "options": YPN},
            {"name": "notes", "width": 30, "answer": True}]
    rows, key = [], {}
    for i, f in enumerate(rows_all, 1):
        rid = f"F{i:03d}"
        text = f["ev"] or record_text(f["src"], recs)
        rows.append([rid, names.get(loc(f["s"]), loc(f["s"])), link(f["s"]), loc(f["p"]), names.get(loc(f["o"]), loc(f["o"])),
                     link(f["o"]), text, f["src"], "", "", "", ""])
        key[rid] = {"fact": {k: f[k] for k in ("s", "p", "o", "src")}, "expect": f.get("expect")}
    ins = [
        "Task A3: facts read from the city's texts and records. Each row is one fact: subject, relation, object.",
        "Read the text it was read from (and the source if needed). Open the subject and object links to see which entity each is.",
        "Q1 / Q2: is the subject (object) the very entity the text means (the same person, building, concept)?",
        "Q3: does the text state this relation between them? 'partly' if it implies it only loosely.",
        "Work alone, do not discuss the rows with anyone, do not use any AI tool. Some rows are checks with a known answer.",
    ]
    write_both("A3_facts.xlsx", "A3", ins, cols, rows, key)
    write_key("A3", {"rows": key})
    print(f"  A3: {len(rows)} facts ({len(controls)} controls)")


# ------------------------------------------------------------------ A4 benchmark stops
def a4(rng: random.Random, names: dict) -> None:
    stops = [(i["id"], s, src) for i in bench.interests() for s in i["stops"] if s.get("scored")
             for src in s.get("sources", [])[:1]]
    sample = rng.sample(stops, min(settings.A4["stops"], len(stops)))
    controls = []
    for j in range(max(2, int(settings.A4["controls"] * len(sample)))):
        (qa, sa, _), (qb, sb, srcb) = rng.sample(stops, 2)
        if qa != qb:
            controls.append((qa, sa, srcb, {"Q1 source names this place for the interest": "no"}))
    rows_all = [(q, s, src, None) for q, s, src in sample] + controls
    rng.shuffle(rows_all)
    cols = [{"name": "id", "width": 7}, {"name": "interest", "width": 24}, {"name": "place", "width": 28},
            {"name": "address", "width": 26}, {"name": "map", "width": 22}, {"name": "source", "width": 50},
            {"name": "Q1 source names this place for the interest", "width": 16, "answer": True,
             "options": ["yes", "no", "does not open"]},
            {"name": "Q2 map shows this place", "width": 12, "answer": True, "options": ["yes", "no", "unsure"]},
            {"name": "notes", "width": 30, "answer": True}]
    rows, key = [], {}
    for i, (q, s, src, expect) in enumerate(rows_all, 1):
        rid = f"S{i:03d}"
        rows.append([rid, names.get(q, q), s["name"], s.get("address") or "", osm(s.get("lat"), s.get("lon")),
                     src["url"], "", "", ""])
        key[rid] = {"interest": q, "stop": s["name"], "url": src["url"], "expect": expect}
    ins = [
        "Task A4: stops of themed walks. Each row is a place and a source (a guide, a page) said to name it for the interest.",
        "Q1: open the source. Does it name this place in connection with the interest? 'does not open' if it fails.",
        "Q2: does the map point show this place (the building or spot named)?",
        "Work alone, do not discuss the rows with anyone, do not use any AI tool. Some rows are checks with a known answer.",
    ]
    write_both("A4_stops.xlsx", "A4", ins, cols, rows, key)
    write_key("A4", {"rows": key})
    print(f"  A4: {len(rows)} stops ({len(controls)} controls)")


def main() -> None:
    from lib.pipeline import build
    recs = records()
    names = labels_of_interests()
    a1(random.Random(settings.SEED + 1), recs, names)
    a2(random.Random(settings.SEED + 2), names)
    if settings.A3:
        a3(random.Random(settings.SEED + 3), recs, build(settings.PARAMS, {}).labeller)
    if settings.A4:
        a4(random.Random(settings.SEED + 4), names)
    print(f"-> da dare agli annotatori: {settings.ANNOT_OUT}")

"""Grounded generation (evaluation/llm_rag/README.md): reads the LLM answers to the
RAG prompts and turns them into one list per model, in the shape of a run report
(output/evaluation_best_first.RAG_<model>.json), for the human round.

- Answers are read verbatim; copy artefacts are handled when reading, never in the
  files: unescaped double quotes inside a string value, URLs written as markdown links
  "[url](url)", and text around the JSON (e.g. the list of links a chat appends).
- A "graph" stop must be one of the ids the prompt gave (context.json), else it counts
  as an altered context and is dropped; it keeps the graph's explanation and gets the
  LLM's added facts (each with URL and quote).
- A new stop needs at least one fact with URL and quote, else it is dropped; it is
  geocoded (as in src/evaluate_llm_baseline.py) and, when the graph has a candidate
  on it (benchmark rule), it also gets the graph's explanation.
- Every added fact's quote is looked up in the page it cites (fetched once, cached in
  evaluation/llm_rag/page_cache.json; no login, paywall or bot check is bypassed):
  verified (the quote is on the page, up to case, punctuation and at most 10% of its words,
  in order), not_on_page, or unreachable. Only verified facts are
  kept: a fact whose quote is not on its page, or whose page cannot be read, is dropped, and a
  new stop with no verified fact is dropped with it (counted in the stats).
- Order: the model's own ranks. Value of a stop (for the threshold-free lists of the
  round): 1 - (rank - 1) / 10.

Run: python src/process_rag.py"""

from __future__ import annotations

import difflib
import html
import io
import json
import re
import time
import unicodedata
from html.parser import HTMLParser

import requests

import config
from evaluate_ground_truth import INSIDE, containers, match, metres
from evaluate_llm_baseline import CACHE, geocode
from build_human_round import tokens

DIR = config.ROOT_DIR / "evaluation" / "llm_rag"
OUT = config.ROOT_DIR / "output"
PAGES = DIR / "page_cache.json"
OPEN_DATA_RECORD = re.compile(r"^https?://data\.stadt-zuerich\.ch/dataset/[^#]+#.+$")
MD_LINK = re.compile(r"^\[(.+?)\]\((.+?)\)$")


def first_json(text: str):
    """The first JSON value of the text: a chat answer may add prose or a list of the links
    it used before or after it."""
    return json.JSONDecoder().raw_decode(text[text.index("["):])[0]


def load_answer(path) -> list[dict]:
    raw = path.read_text()
    try:
        return first_json(raw)
    except json.JSONDecodeError:
        def fix(line: str) -> str:
            m = re.match(r'^(\s*"(?:quote|fact|name|address|interest)":\s*")(.*)("\s*,?\s*)$', line)
            return m.group(1) + m.group(2).replace('\\"', '"').replace('"', '\\"') + m.group(3) if m else line
        print(f"  repaired unescaped quotes in {path.name}")
        return first_json("\n".join(fix(l) for l in raw.split("\n")))


def clean_url(u: str | None) -> str | None:
    if not u:
        return None
    u = u.strip()
    m = MD_LINK.match(u)
    if m:
        return m.group(2)
    # the same link with its opening bracket already gone: "https://x](https://x)"
    m = re.match(r"^\[?(https?://[^\]\s]+)\]\((https?://[^)\s]+)\)$", u)
    return m.group(2) if m else u.lstrip("[")


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = re.sub(r"[‘’‚‛`´]", "'", re.sub(r"[“”„‟«»]", '"', text))
    text = re.sub(r"[‐-―]", "-", text)
    return re.sub(r"\s+", " ", text).strip().lower()


class _Text(HTMLParser):
    """The text of an HTML page: what is between its tags, without scripts and styles (a tag-stripping
    regex breaks on attributes that hold a '>', as Wikipedia's do)."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style") and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def response_text(r: requests.Response) -> str:
    """The text of a fetched page: of a PDF, the text of its pages; of an HTML page, its text, decoded
    with the charset it declares or, if it declares none, the one its bytes show."""
    if "pdf" in r.headers.get("Content-Type", "").lower() or r.content[:5] == b"%PDF-":
        from pypdf import PdfReader
        return " ".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(r.content)).pages)
    if "charset" not in r.headers.get("Content-Type", "").lower():
        r.encoding = r.apparent_encoding
    parser = _Text()
    parser.feed(r.text)
    return " ".join(parser.parts)


def page_text(url: str, cache: dict) -> str | None:
    if url not in cache:
        try:
            r = requests.get(url, headers={"User-Agent": config.USER_AGENT}, timeout=30)
            time.sleep(1.0)
            cache[url] = response_text(r) if r.ok else None
        except Exception:           # a request that fails, or a PDF that cannot be read
            cache[url] = None
    return cache[url]


def check(fact: dict, cache: dict) -> str:
    if OPEN_DATA_RECORD.match(fact["url"]):
        # a record of the city's open data ("...dataset/<name>#<id>"): the quote is looked up in the
        # dataset, as for the facts of the graph read from it (audit_explanations.in_dataset)
        from audit_explanations import in_dataset
        return "verified" if in_dataset(fact["quote"], fact["url"]) else "not_on_page"
    text = page_text(fact["url"], cache)
    if text is None:
        return "unreachable"
    q, t = norm(fact["quote"]), norm(text)
    if q in t:
        return "verified"
    # a quote cut or joined differently: every sentence-length piece of it must be on the page
    pieces = [p for p in re.split(r"(?<=[.!?;:])\s+|\s*\.\.\.\s*|\s*…\s*", q) if len(p) > 25]
    if pieces and all(p.strip(" .") in t for p in pieces):
        return "verified"
    return "verified" if near_verbatim(q, t) else "not_on_page"


WORDS = re.compile(r"\w+", re.U)


def near_verbatim(q: str, t: str, ratio: float = 0.9) -> bool:
    """The quote is on the page up to punctuation and a few words: some passage of the page,
    as long as the quote, has at least `ratio` of its words in the same order (e.g. a comma
    dropped, or a link label "Externer Link:" copied into the quote)."""
    qw, tw = WORDS.findall(q), WORDS.findall(t)
    if len(qw) < 5:
        return False
    anchors = {w for w in qw[:3]}
    for i, w in enumerate(tw):
        if w in anchors:
            window = tw[max(0, i - 3):i + len(qw) + 3]
            m = difflib.SequenceMatcher(None, qw, window, autojunk=False)
            if sum(b.size for b in m.get_matching_blocks()) >= ratio * len(qw):
                return True
    return False


def same_name(a: str, b: str, ignore: set[str] = frozenset()) -> bool:
    from build_human_round import tokens
    ta, tb = tokens(a) - ignore, tokens(b) - ignore
    return bool(ta and tb) and len(ta & tb) / min(len(ta), len(tb)) >= 0.5


def main() -> None:
    ctx = json.loads((DIR / "context.json").read_text())
    full = {r["interests"][0]: r for r in json.loads((OUT / "evaluation_best_first.WT_allcands.json").read_text())["runs"]}
    INSIDE.update(containers({c["id"] for r in full.values() for c in r.get("candidates", [])}))
    geo = json.loads(CACHE.read_text())
    pages = json.loads(PAGES.read_text()) if PAGES.exists() else {}
    summary = {}
    try:
        for model in ("gemini", "gpt"):
            files = sorted(DIR.glob(f"{model}_block*_20*.json"))
            if not files:
                continue
            answers = {a["interest_id"]: a for f in files for a in load_answer(f)}
            stats = {"graph_kept": 0, "graph_repeated": 0, "altered": 0, "enriched_stops": 0, "new_stops": 0, "new_dropped": 0,
                     "new_grounded": 0, "new_merged": 0, "facts": 0, "verified": 0, "not_on_page": 0, "unreachable": 0, "new_unverified": 0}
            runs = []
            for iid, given in ctx["context"].items():
                ans = answers.get(iid, {"stops": []})
                cands = {c["id"]: c for c in full.get(iid, {}).get("candidates", [])}
                by_sid = {g["id"]: g["candidate_id"] for g in given}
                out = []
                for s in sorted(ans["stops"], key=lambda s: s.get("rank") or 99):
                    facts = [dict(f, url=clean_url(f.get("url"))) for f in
                             (s.get("added_facts") if s.get("origin") == "graph" else s.get("facts")) or []
                             if f.get("url") and f.get("quote")]
                    for f in facts:
                        f["check"] = check(f, pages)
                        stats["facts"] += 1
                        stats[f["check"]] += 1
                    proposed = bool(facts)
                    facts = [f for f in facts if f["check"] == "verified"]
                    if s.get("origin") == "graph":
                        cid = by_sid.get(s.get("id"))
                        if cid is None or cid not in cands:
                            stats["altered"] += 1
                            continue
                        again = next((o for o in out if o["id"] == cid), None)
                        if again is not None:            # the same graph stop listed twice: one stop
                            again["llm_facts"] = again.get("llm_facts", []) + facts
                            stats["graph_repeated"] += 1
                            continue
                        stats["graph_kept"] += 1
                        stats["enriched_stops"] += bool(facts)
                        c = dict(cands[cid], origin="graph", llm_facts=facts)
                    else:
                        if not facts:
                            stats["new_unverified" if proposed else "new_dropped"] += 1
                            continue
                        stats["new_stops"] += 1
                        g = geocode({"name": s.get("name", ""), "address": s.get("address")}, geo)
                        probe = {"qid": g.get("qid") if g else None, "lat": g and g["lat"], "lon": g and g["lon"]}
                        hit = max((c for c in cands.values() if g and match(probe, c, 50.0)),
                                  key=lambda c: c["score"], default=None)
                        stats["new_grounded"] += hit is not None
                        c = dict(hit) if hit else {"id": (g or {}).get("qid") or s.get("name"), "label": s.get("name"),
                                                   "lat": g and g["lat"], "lon": g and g["lon"], "explanations": [],
                                                   "reasons": [], "located_at": s.get("address")}
                        c.update(origin="new", llm_name=s.get("name"), llm_address=s.get("address"), llm_facts=facts)
                        # the same place already on the list (a graph stop proposed again as new): one stop
                        def near(o, m):
                            return g and o.get("lat") is not None and metres(o["lat"], o["lon"], g["lat"], g["lon"]) <= m
                        # same graph candidate; or within 30 m; or a like name within 600 m (geocoders often
                        # return the street, not the house)
                        dup = next((o for o in out if (hit is not None and o["id"] == hit["id"]) or near(o, 30)
                                    or (near(o, 600) and same_name(o.get("llm_name") or o["label"], s.get("name", ""),
                                                                  tokens(ans.get("interest", ""))))),
                                   None)
                        if dup is not None:
                            dup["llm_facts"] = dup.get("llm_facts", []) + facts
                            stats["new_merged"] += 1
                            continue
                    c["system_score"] = c.get("score")
                    c["score"] = round(1 - (len(out)) / 10, 2)
                    out.append(c)
                runs.append({"interests": [iid], "candidates": out[:10]})
            (OUT / f"evaluation_best_first.RAG_{model}.json").write_text(json.dumps(
                {"model": model, "files": [f.name for f in files], "stats": stats, "runs": runs}, indent=1, ensure_ascii=False))
            summary[model] = stats
            print(model, stats)
    finally:
        PAGES.write_text(json.dumps(pages, ensure_ascii=False))
        CACHE.write_text(json.dumps(geo, indent=0, ensure_ascii=False))
    (DIR / "summary.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

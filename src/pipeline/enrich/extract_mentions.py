"""Extracts the people and movements that the Zurich Tourism descriptions
associate with a place ("Famous names such as James Joyce, Einstein, Lenin,
and the Dadaists once frequented this locale"), links both sides to
Wikidata, names the relation from the sentence, and writes each as an
annotated fact of G_P that keeps the sentence it comes from:

    wd:Q6882 nami:frequented wd:Q1542522 {| nami:source <https://www.zuerich.com/en/node/1570> ;
        nami:evidence "Famous names such as James Joyce, ... once frequented this locale ..." ;
        nami:confidence 0.7 |} .

Every relation is an rdfs:subPropertyOf nami:mentionedAt, the one used when
the sentence names no relation (mappings/text_relations.ttl).

1. Place. The POI's own Wikidata item: the item within MAX_LINK_M of the
   POI whose label best shares its name, else its OSM owl:sameAs twin if
   that lies within MAX_LINK_M too, else the local POI IRI (located from
   the catalogue).
2. Mentions. Capitalised name spans of the English text. A span is linked
   when it is the title of (or a redirect to) the English Wikipedia article
   of an item of an accepted type: a human, a movement. A one-word span
   ("Einstein") must never occur lowercased in the corpus and must name a
   prominent item. A span inside a link of the description counts more.
3. Relation. The cue (TEXT_RELATIONS) closest to the mention in its
   sentence, else nami:mentionedAt.
4. Confidence by evidence: full name 0.7, anchor-linked +0.1, surname alone 0.5.

Wikidata is read in batches -- entities by English Wikipedia title, 50 per
call, and items near all POIs in one SPARQL query -- and cached in
data/raw/wikidata_mentions_cache.json, so a rerun is offline.

Run: python src/pipeline/enrich/extract_mentions.py [--review output/mentions_review.json]"""

from __future__ import annotations

import argparse
import glob
import html
import json
import re
import sys
import time
import unicodedata
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import pyoxigraph as ox
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
WIKIPEDIA = "https://en.wikipedia.org/w/api.php"
DISAMBIGUATION = "-"      # a title whose page lists several meanings
UA = {"User-Agent": "NAMI-research-prototype/0.1 (evaluation)"}
WD = "http://www.wikidata.org/entity/"
POI = "https://example.org/nami/data/poi/"
OUT_FILE = config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_opendata_mentions.ttl"
CACHE_FILE = config.RAW_DIR / "wikidata_mentions_cache.json"

MAX_LINK_M = 150
MIN_SITELINKS_SURNAME = 40      # a lone surname must name a prominent item
HUMAN = "Q5"
MOVEMENTS = {"Q968159", "Q2198855", "Q3326717", "Q49773", "Q1792644", "Q11042", "Q17517379"}
# art movement, cultural movement, literary movement, social movement,
# art style, culture, religious movement
CONF_FULL, CONF_SURNAME, BONUS_ANCHOR = 0.7, 0.5, 0.1
LANGS = ("en", "de", "fr", "it")    # the languages of the country, and English
CONNECTORS = {"von", "van", "de", "di", "da", "du", "le", "la", "der"}
TEXT_FIELDS = ("disambiguatingDescription", "description", "textTeaser", "detailedInformation")

# Relation named by the sentence: the cue closest to the mention wins.
TEXT_RELATIONS = {
    "livedAt": r"lived|living|resided|residence|home of|(?:his|her|their) home|stayed",
    "buriedAt": r"buried|grave|tomb|resting place|interred",
    "workedAt": r"worked|taught|teaching|studied|professor|lecturer|employed|doctorate",
    "foundedAt": r"founded|established|originated|birthplace|was born|started",
    "frequented": r"frequented|regulars?|habitu[eé]s?|meeting place|haunt|met (?:here|there)|sat|used to (?:sit|meet|drink|eat)",
    "performedAt": r"soir[eé]es?|performed|performances?|exhibited|exhibitions?|premieres?d?|staged",
    "commemoratedAt": r"monument|memorial|plaque|statue|bust|dedicated to|commemorat\w*|in honou?r of",
}
CUE_RX = {rel: re.compile(rf"\b(?:{rx})\b", re.I) for rel, rx in TEXT_RELATIONS.items()}
# A place themed after the entity: its name used for a room or a style ("the Dada
# Bar", "Dada-style design"), or the place named after it, inspired by it.
# A surname that names a firm or a shop, not a person: "Baeckerei Jung", "the Jung
# bakery", "Christ & Gantenbein".
FIRM_AFTER = re.compile(r"^\s*(?:&|\+|brand|label|company|family business|team|staff|shop|store|products?|bakery|butcher|architects?|Architekten|AG|GmbH|und Partner|& Partners?)\b", re.I)
FIRM_BEFORE = re.compile(r"(?:&|\+|Bäckerei|Metzgerei|Architekten)\s*$", re.I)
THEMED_AFTER = re.compile(r"^[\s-]*(?:bar|lounge|room|suite|salon|corner|style|styled|design|décor|decor|themed?|inspired)\b", re.I)
THEMED_BEFORE = re.compile(r"\b(?:inspired by|named (?:after|for)|in the style of|homage to|tribute to|dedicated to the)\s*$", re.I)
DEFAULT_RELATION = "mentionedAt"
NAMI = "https://example.org/nami/ontology#"


def metres(lat1, lon1, lat2, lon2) -> float:
    dlat, dlon = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(a))


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# --- Wikidata, cached --------------------------------------------------------------

class Wikidata:
    """The few Wikidata reads this needs, batched and cached: the item of an
    English Wikipedia title (50 per call, following redirects), entities by
    id (50 per call), and items near a point (one SPARQL query for many points)."""

    def __init__(self, cache_file: Path):
        self.file = cache_file
        self.cache = {"title": {}, "entity": {}, "near": {}}
        if cache_file.exists():
            self.cache.update(json.loads(cache_file.read_text()))
        self.session = requests.Session()
        self.session.headers.update(UA)
        self.calls = 0

    def _request(self, url: str, method: str = "GET", **params) -> dict:
        for attempt in range(30):
            try:
                r = self.session.request(method, url, timeout=120, **params)
                if r.status_code == 429 or r.status_code >= 500:
                    wait = int(r.headers.get("retry-after", 0)) or min(2 ** attempt * 5, 120)
                    print(f"  Wikidata {r.status_code}, retrying in {wait}s", flush=True)
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                body = r.json()
            except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.Timeout, ValueError) as ex:
                print(f"  {ex.__class__.__name__}, retrying in 30s", flush=True)
                time.sleep(30)
                continue
            self.calls += 1
            time.sleep(1)
            return body
        raise RuntimeError(f"Wikidata unavailable: {url}")

    def _keep(self, e: dict) -> str:
        claims = e.get("claims", {})
        p31 = [c["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for c in claims.get("P31", [])]
        coord = next((c["mainsnak"]["datavalue"]["value"] for c in claims.get("P625", [])
                      if "datavalue" in c["mainsnak"]), None)
        labels = e.get("labels", {})
        self.cache["entity"][e["id"]] = {
            "p31": [p for p in p31 if p],
            "coord": [coord["latitude"], coord["longitude"]] if coord else None,
            "label": (labels.get("en") or labels.get("mul") or labels.get("de") or {}).get("value")
                     or e.get("sitelinks", {}).get("enwiki", {}).get("title") or e["id"],
            "enwiki": e.get("sitelinks", {}).get("enwiki", {}).get("title"),
            "sitelinks": len(e.get("sitelinks", {})),
            "links": sorted({c["mainsnak"]["datavalue"]["value"]["id"] for cs in claims.values() for c in cs
                             if c["mainsnak"].get("datavalue", {}).get("type") == "wikibase-entityid"
                             and c["mainsnak"]["datavalue"]["value"].get("id", "").startswith("Q")}),
        }
        return e["id"]

    def _get_entities(self, **params) -> list[dict]:
        res = self._request(API, params={"format": "json", "action": "wbgetentities",
                                         "props": "claims|sitelinks|labels", "languages": "en|mul|de", **params})
        return list(res.get("entities", {}).values())

    def titles(self, titles: set[str], wiki: str = "en") -> dict[str, str | None]:
        """QID of the item whose English Wikipedia article has each title, following
        redirects; DISAMBIGUATION for a disambiguation page, None for no page. 50 titles per call."""
        key = (lambda t: t) if wiki == "en" else (lambda t: f"{wiki}:{t}")
        todo = sorted(t for t in titles if key(t) not in self.cache["title"])
        for k in range(0, len(todo), 50):
            chunk = todo[k:k + 50]
            res = self._request(WIKIPEDIA.replace("//en.", f"//{wiki}."), params={
                "format": "json", "formatversion": 2, "action": "query", "titles": "|".join(chunk),
                "redirects": 1, "prop": "pageprops", "ppprop": "wikibase_item|disambiguation"})["query"]
            step = {m["from"]: m["to"] for m in res.get("normalized", []) + res.get("redirects", [])}
            pages = {pg["title"]: pg.get("pageprops", {}) for pg in res.get("pages", [])}
            for t in chunk:
                final = t
                while final in step and step[final] != final:
                    final = step[final]
                props = pages.get(final, {})
                self.cache["title"][key(t)] = DISAMBIGUATION if "disambiguation" in props else props.get("wikibase_item")
            if (k // 50) % 10 == 9:
                self.save()
            print(f"  titles {min(k + 50, len(todo))}/{len(todo)}", flush=True)
        return {t: self.cache["title"].get(key(t)) for t in titles}

    def entities(self, ids: set[str]) -> dict[str, dict]:
        """P31, coordinates, label, English article, sitelink count and linked items of each item."""
        todo = sorted(i for i in ids if "links" not in self.cache["entity"].get(i, {}))
        for k in range(0, len(todo), 50):
            for e in self._get_entities(ids="|".join(todo[k:k + 50])):
                if "missing" not in e:
                    self._keep(e)
        return {i: self.cache["entity"][i] for i in ids if i in self.cache["entity"]}

    def near(self, points: dict[str, tuple[float, float]], radius_m: float) -> dict[str, list[dict]]:
        """Items with coordinates within radius_m of each point, with their English and German labels."""
        todo = sorted(p for p in points if p not in self.cache["near"])
        for k in range(0, len(todo), 40):
            chunk = todo[k:k + 40]
            values = " ".join(f'("{p}" "Point({points[p][1]} {points[p][0]})"^^geo:wktLiteral)' for p in chunk)
            query = f"""SELECT ?p ?item ?en ?de ?dist WHERE {{
  VALUES (?p ?c) {{ {values} }}
  SERVICE wikibase:around {{ ?item wdt:P625 ?loc . bd:serviceParam wikibase:center ?c ;
    wikibase:radius "{radius_m / 1000}" ; wikibase:distance ?dist . }}
  OPTIONAL {{ ?item rdfs:label ?en FILTER(LANG(?en) = "en") }}
  OPTIONAL {{ ?item rdfs:label ?de FILTER(LANG(?de) = "de") }}
}}"""
            res = self._request(SPARQL, "POST", data={"query": query},
                                headers={"Accept": "application/sparql-results+json"})
            for p in chunk:
                self.cache["near"][p] = []
            for b in res["results"]["bindings"]:
                self.cache["near"][b["p"]["value"]].append({
                    "id": b["item"]["value"].removeprefix(WD),
                    "labels": [b[x]["value"] for x in ("en", "de") if x in b],
                    "m": round(float(b["dist"]["value"]) * 1000)})
            self.save()
        return {p: self.cache["near"][p] for p in points}

    def save(self) -> None:
        self.file.write_text(json.dumps(self.cache, ensure_ascii=False))


# --- the catalogue -----------------------------------------------------------------

def load_pois() -> dict[str, dict]:
    pois = {}
    for f in glob.glob(str(config.RAW_DIR / "id_*.json")):
        try:
            items = json.loads(Path(f).read_text())
        except ValueError:
            continue
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("identifier") is not None:
                pois[str(it["identifier"])] = it
    return pois


def catalogue_facts(store_dir: Path) -> tuple[dict[str, tuple[float, float]], dict[str, str]]:
    """Coordinates and OSM Wikidata twin of every POI, from the built store."""
    store = ox.Store.read_only(str(store_dir))
    coords, twins = {}, {}
    for r in store.query("""SELECT ?u ?wkt WHERE { GRAPH ?g { ?u <http://www.opengis.net/ont/geosparql#hasGeometry> ?geom .
                              ?geom <http://www.opengis.net/ont/geosparql#asWKT> ?wkt } }"""):
        m = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)", r["wkt"].value)
        if m:
            coords[r["u"].value.removeprefix(POI)] = (float(m.group(2)), float(m.group(1)))
    for r in store.query("SELECT ?u ?w WHERE { GRAPH ?g { ?u <http://www.w3.org/2002/07/owl#sameAs> ?w } }"):
        twins[r["u"].value.removeprefix(POI)] = r["w"].value.removeprefix(WD)
    return coords, twins


def name_of(poi: dict, lang: str = "en") -> str:
    n = poi.get("name") or {}
    return (n.get(lang) or n.get("de") or "").strip() if isinstance(n, dict) else str(n)


def texts(poi: dict, lang: str) -> list[str]:
    out = []
    for f in TEXT_FIELDS:
        v = (poi.get(f) or {}).get(lang) if isinstance(poi.get(f), dict) else None
        out += v if isinstance(v, list) else [v] if v else []
    return out


ANCHOR = re.compile(r"<a\b[^>]*>(.*?)</a>", re.S | re.I)


def plain(markup: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup))).strip()


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"«])", text) if s.strip()]


TOKEN = r"(?:[A-Z][\w'’.-]*[\w]|[A-Z]\.)"
SPAN = re.compile(rf"{TOKEN}(?:\s+(?:(?:{'|'.join(CONNECTORS)})\s+)?{TOKEN}){{0,3}}")


def spans(sentence: str) -> set[str]:
    """Capitalised name spans; a sentence-initial word is kept only inside a longer span."""
    out = set()
    for m in SPAN.finditer(sentence):
        s = m.group(0).strip(" .")
        words = s.split()
        if m.start() == 0 or sentence[:m.start()].rstrip().endswith((":", "–", "-")):
            if len(words) == 1:
                continue
        out.add(s)
    return out


def relation(sentence: str, span: str) -> str:
    """themedAfter if the place uses the name as a theme; else the relation whose cue
    lies closest to the mention; else the default."""
    at = sentence.find(span)
    if at >= 0 and (THEMED_AFTER.match(sentence[at + len(span):]) or THEMED_BEFORE.search(sentence[:at])):
        return "themedAfter"
    best, dist = DEFAULT_RELATION, None
    for rel, rx in CUE_RX.items():
        for m in rx.finditer(sentence):
            d = abs(m.start() - at) if at >= 0 else 0
            if dist is None or d < dist:
                best, dist = rel, d
    return best


def turtle_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- linking -----------------------------------------------------------------------

NAME_STOPWORDS = {"zurich", "zuerich", "the", "of", "and", "der", "die", "das", "und", "am", "im", "zum", "zur", "in",
                  "at", "st"}
NAMED_PLACE = re.compile(r"-(?:strasse|straße|platz|weg|gasse|quai|steig|steg|halde|rain|brücke|hof|park|"
                         r"anlage|allee|ring|street|square|bridge)\b", re.I)
ARTICLES = ("The ", "A ", "An ", "Der ", "Die ", "Das ", "Dem ", "Den ", "Des ")


def name_tokens(name: str) -> set[str]:
    return set(norm(re.split(r"[:–(]| - ", name)[0]).split()) - NAME_STOPWORDS


def link_place(pid: str, poi: dict, nearby: list[dict], twin: str | None) -> tuple[str, str]:
    """(node IRI, how) for a POI: the nearby item whose label shares at least half
    of the words of the POI's name and of its own (ties: the OSM twin, then the
    nearest), else its OSM twin if nearby, else the POI itself."""
    names = [name_tokens(name_of(poi, lang)) for lang in ("en", "de")]
    best, score = None, (0.0,)
    for item in nearby:
        for label in item["labels"]:
            lt = name_tokens(label)
            for nt in names:
                if nt and lt:
                    sc = (len(nt & lt) / max(len(nt), len(lt)), item["id"] == twin, -item["m"])
                    if sc > score:
                        best, score = item["id"], sc
    if best and score[0] >= 0.5:
        return WD + best, "osm+name" if best == twin else "name+distance"
    if twin and any(i["id"] == twin for i in nearby):
        return WD + twin, "osm"
    return POI + pid, "local"


def candidates(span: str) -> list[tuple[str, str, list[str]]]:
    """(text, kind, other words) for the span without a leading article, then its
    prefixes and suffixes (hyphens split words), longest first:
    "The Dada Library" -> ("Dada Library", whole), ("Dada", prefix, ["Library"]), ..."""
    for a in ARTICLES:
        if span.startswith(a):
            span = span[len(a):]
    whole = [(span, "whole", [])]
    words = [w for w in re.split(r"[\s-]+", span) if w]
    if len(words) == 1:
        return whole
    parts = [(" ".join(words[:k]), "prefix", words[k:]) for k in range(len(words) - 1, 0, -1)]
    parts += [(" ".join(words[-k:]), "suffix", words[:-k]) for k in range(len(words) - 1, 0, -1)]
    return whole + sorted(parts, key=lambda c: -len(c[0].split()))


def common_noun(word: str, title_qid: dict, ents: dict, lowercase_words: set[str]) -> bool:
    """A word used in lowercase somewhere in the corpus, or naming a concept: an item
    that is neither a person nor a place ("Soirée", but not "Zurich")."""
    if word.lower() in lowercase_words:
        return True
    ent = ents.get(title_qid.get(word) or "")
    return bool(ent) and HUMAN not in ent["p31"] and ent["coord"] is None


def link_span(span, title_qid, ents, lowercase_words, surnames) -> tuple[str, str, dict, float] | None:
    """(matched text, qid, entity, confidence) for the longest part of span that names
    someone. A longer part with an article of its own names something else, and is not
    broken up: "Cabaret Voltaire" is a venue, not Voltaire. A single word cut from a
    longer name counts only as a movement qualifying common nouns ("Dada Soiree"),
    never as a surname ("Zurich-West", "Escher-Wyss-Platz", "Baeckerei Jung")."""
    if NAMED_PLACE.search(span):        # "Richard-Wagner-Strasse" names a street, not the person
        return None
    for text, kind, rest in candidates(span):
        if kind != "whole" and len(text.split()) == 1:
            q = title_qid.get(text)
            ent = ents.get(q) if q and q != DISAMBIGUATION else None
            if not (kind == "prefix" and ent and MOVEMENTS & set(ent["p31"])
                    and all(common_noun(w, title_qid, ents, lowercase_words) for w in rest)):
                continue
        hit = link_mention(text, title_qid, ents, lowercase_words, surnames)
        if hit:
            return (text, *hit)
        if len(text.split()) > 1 and title_qid.get(text):
            return None
    return None


def link_mention(span: str, title_qid: dict, ents: dict, lowercase_words: set[str],
                 surnames: dict | None = None) -> tuple[str, dict, float] | None:
    """The human or movement whose English Wikipedia article is titled `span`, or redirects
    from it; for a lone surname with no article of its own, the only person of that surname
    the catalogue names in full."""
    one_word = len(span.split()) == 1
    if one_word and surnames and span in surnames and title_qid.get(span) in (None, DISAMBIGUATION):
        q = surnames[span]
        return q, ents[q], CONF_SURNAME
    if one_word and span.lower() in lowercase_words:
        return None
    q = title_qid.get(span)
    ent = ents.get(q) if q and q != DISAMBIGUATION else None
    if ent is None:
        return None
    human = HUMAN in ent["p31"]
    if not (human or MOVEMENTS & set(ent["p31"])):
        return None
    if one_word and human and ent["sitelinks"] < MIN_SITELINKS_SURNAME:
        return None
    return q, ent, CONF_SURNAME if one_word and human else CONF_FULL


def surname(label: str) -> str:
    return norm(label).split()[-1] if norm(label) else ""


def resolve_surnames(wd: Wikidata, found_by_poi: dict, places: dict) -> None:
    """A lone surname names the person of that surname whom the same description names
    in full, else the one the place's own Wikidata item links to, else stays as linked."""
    lone = {pid: [m for m in found.values() if len(m["span"].split()) == 1 and m["confidence"] <= CONF_SURNAME + BONUS_ANCHOR]
            for pid, found in found_by_poi.items()}
    place_items = {places[pid][0].removeprefix(WD) for pid, ms in lone.items() if ms and places[pid][0].startswith(WD)}
    linked = set()
    for e in wd.entities(place_items).values():
        linked |= set(e["links"])
    people = {q: e for q, e in wd.entities(linked).items() if HUMAN in e["p31"]}
    for pid, ms in lone.items():
        found = found_by_poi[pid]
        place = places[pid][0].removeprefix(WD)
        in_text = {q for q, m in found.items() if len(m["span"].split()) > 1}
        near = set(wd.cache["entity"].get(place, {}).get("links", [])) & set(people)
        for m in ms:
            s = surname(m["span"])
            for pool, how in ((in_text, "full name in the text"), (near, "linked to the place")):
                match = [q for q in pool if q != m["qid"] and surname(wd.cache["entity"].get(q, {}).get("label", "")) == s
                         and HUMAN in wd.cache["entity"].get(q, {}).get("p31", [])]
                if len(match) == 1:
                    q = match[0]
                    del found[m["qid"]]
                    m.update(qid=q, label=wd.cache["entity"][q]["label"], resolved_by=how)
                    found.setdefault(q, m)
                    break



def mentions(wd: "Wikidata", pois: dict, ids: list[str], lang: str) -> dict[str, dict]:
    """The people and movements each POI's description names in one language, each with its relation (English
    cues; the default relation in the other languages), confidence and sentence."""
    corpus = " ".join(plain(t) for p in ids for t in texts(pois[p], lang))
    lowercase_words = set(re.findall(r"\b[^\W\d_][^\W\d_'-]+\b", corpus))
    lowercase_words = {w for w in lowercase_words if w[0].islower()}
    docs = {}
    for pid in ids:
        raw = texts(pois[pid], lang)
        docs[pid] = ({plain(a) for t in raw for a in ANCHOR.findall(t)},
                     [(sent, spans(sent)) for sent in sentences(". ".join(plain(t).rstrip(".") for t in raw))])
    names = {c for _, sents in docs.values() for _, sps in sents for sp in sps for c, _, _ in candidates(sp)
             if not (len(c.split()) == 1 and (lang != "en" or c.lower() in lowercase_words))}
    print(f"[{lang}] {len(ids)} POIs, {len(names)} candidate names", flush=True)
    title_qid = wd.titles(names, wiki=lang)
    ents = wd.entities({q for q in title_qid.values() if q and q != DISAMBIGUATION})
    surnames = {}
    if lang == "en":            # lone surnames: people the catalogue names in full, when the surname is theirs alone
        by_surname: dict[str, set[str]] = {}
        for c, q in title_qid.items():
            if q and q != DISAMBIGUATION and len(c.split()) > 1 and c.split()[-1].lower() not in lowercase_words \
                    and link_mention(c, title_qid, ents, lowercase_words) and HUMAN in ents[q]["p31"]:
                by_surname.setdefault(c.split()[-1], set()).add(q)
        surnames = {s: next(iter(qs)) for s, qs in by_surname.items() if len(qs) == 1}
    found_by_poi = {}
    for pid, (anchors, sents) in docs.items():
        found: dict[str, dict] = {}
        for sent, sps in sents:
            for sp in sps:
                hit = link_span(sp, title_qid, ents, lowercase_words, surnames)
                if not hit:
                    continue
                text, q, ent, conf = hit
                if lang != "en" and len(text.split()) == 1:
                    continue        # a capitalised single word in German is a noun, not a name
                if lang != "en" and name_tokens(text) <= name_tokens(name_of(pois[pid], lang)) | name_tokens(name_of(pois[pid], "en")):
                    continue        # the place is named after it ("Santa Lucia", "Saint Georges Hotel"): no relation stated
                at = sent.find(text)
                if at >= 0 and sent[at + len(text):].startswith("-"):
                    continue        # part of a compound: "Richard Wagner- und Schulhausstrasse"
                if len(text.split()) == 1 and HUMAN in ent["p31"] and at >= 0 and (
                        FIRM_AFTER.match(sent[at + len(text):]) or FIRM_BEFORE.search(sent[:at])):
                    continue
                if any(text in a or a in text for a in anchors):
                    conf = min(1.0, conf + BONUS_ANCHOR)
                label = ent["label"] if ent["label"] != q else ent.get("enwiki") or q
                m = {"qid": q, "label": label, "span": text, "lang": lang,
                     "relation": relation(sent, text) if lang == "en" else DEFAULT_RELATION,
                     "confidence": round(conf, 2), "evidence": sent}
                old = found.get(q)
                if old is None or (m["confidence"], m["relation"] != DEFAULT_RELATION) > \
                        (old["confidence"], old["relation"] != DEFAULT_RELATION):
                    found[q] = m
        if found:
            found_by_poi[pid] = found
    return found_by_poi

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", type=Path, default=config.ROOT_DIR / "output" / "mentions_review.json")
    ap.add_argument("--only", nargs="*", help="POI identifiers (for testing)")
    args = ap.parse_args()

    wd = Wikidata(CACHE_FILE)
    pois = load_pois()
    coords, twins = catalogue_facts(config.STORE_DIR)
    west, south, east, north = config.ZURICH_BBOX
    in_area = {p for p, (lat, lon) in coords.items() if west <= lon <= east and south <= lat <= north}
    ids = sorted(p for p in (args.only or pois) if p in in_area)
    found_by_poi: dict[str, dict] = {}
    try:
        # the languages of the country, and English: English first (its relation cues, its surnames); a person
        # the other languages name in full, and English does not, is added with the default relation
        for lang in LANGS:
            for pid, found in mentions(wd, pois, ids, lang).items():
                into = found_by_poi.setdefault(pid, {})
                for q, m in found.items():
                    into.setdefault(q, m)
        print(f"{len(found_by_poi)} POIs mention someone", flush=True)

        # 3. the place each of those POIs is
        nearby = wd.near({p: coords[p] for p in found_by_poi}, MAX_LINK_M)
    finally:
        wd.save()

    places = {pid: link_place(pid, pois[pid], nearby[pid], twins.get(pid)) for pid in found_by_poi}
    try:
        resolve_surnames(wd, found_by_poi, places)
    finally:
        wd.save()

    facts, review = [], []
    for pid, found in found_by_poi.items():
        place, how = places[pid]
        for m in found.values():
            if place != WD + m["qid"]:
                facts.append((m["qid"], m["relation"], place, pid, m["confidence"], m["evidence"], m.get("lang", "en")))
        review.append({"poi": pid, "name": name_of(pois[pid]), "place": place.removeprefix(WD), "linked_by": how,
                       "osm_twin": twins.get(pid), "mentions": list(found.values())})
    write(facts, review, args.review, wd.calls)


def write(facts, review, review_file: Path, calls: int) -> None:

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Extracted layer of G_P: people and movements that the Zurich Tourism",
        "# descriptions associate with a place, linked to Wikidata by",
        "# src/pipeline/enrich/extract_mentions.py. Relations: mappings/text_relations.ttl.",
        "# Source: the POI's page; evidence: the sentence; confidence by evidence",
        "# (full name 0.7, anchor-linked +0.1, surname alone 0.5).",
        f"# {config.ATTRIBUTION_TEXT}",
        "@prefix nami: <https://example.org/nami/ontology#> .",
        "@prefix wd:  <http://www.wikidata.org/entity/> .",
        "",
    ]
    for q, rel, place, pid, conf, evidence, lang in sorted(set(facts)):
        obj = f"wd:{place.removeprefix(WD)}" if place.startswith(WD) else f"<{place}>"
        lines.append(f"wd:{q} nami:{rel} {obj} {{| nami:source <https://www.zuerich.com/{lang}/node/{pid}> ;\n"
                     f"    nami:evidence {turtle_string(evidence)} ;\n    nami:confidence {conf} |}} .")
    OUT_FILE.write_text("\n".join(lines) + "\n")
    review_file.write_text(json.dumps(review, indent=1, ensure_ascii=False))
    print(f"{len(set(facts))} facts from {len(review)} POIs -> {OUT_FILE}; review: {review_file}; {calls} API calls")


if __name__ == "__main__":
    main()

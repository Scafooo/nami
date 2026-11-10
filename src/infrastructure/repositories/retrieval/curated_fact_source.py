"""CuratedFactSource: the locally curated RDF-star layers of G_P (hand-curated
facts, and those extracted from the Zurich Tourism descriptions), read with
SPARQL 1.2 annotation patterns, merged with another FactSource (Wikidata)
that answers every other call. A curated fact may end at a catalogue POI
with no Wikidata item; such a node is located from the catalogue.

Concepts named in text. The texts of the sources about each place (zurich_texts.json, by
src/pipeline/enrich/extract_texts.py) also link places to the concepts they name, as they do
to people: a sentence of a place's text that names a concept, by one of its German or English
labels or aliases on Wikidata, is a fact (concept, nami:mentionedAt, place) with that sentence
as evidence. A whole word or phrase counts 0.6, a word a label begins (a derivation or a
compound: "kolonial" for colonialism, "Drogenszene" for drug) 0.5. Aliases are looser than
labels ("Stoff", stuff, is an alias of drug): they count only as whole words or phrases, and
a one-word alias only if at least MIN_ALIAS letters long. A term named by the texts of more
than MAX_PLACES places names nothing in particular and is not used. A place that its own name,
or more than one sentence, links to the concept is linked more strongly: +BONUS_REPEATED."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

import pyoxigraph as ox

from domain.models.retrieval.fact import Fact, Interval
from domain.repositories.retrieval.fact_source import LOCAL_DATA_PREFIX, FactSource, Location
from infrastructure.repositories.retrieval.annotated_store import read_facts

NAMI = "https://example.org/nami/ontology#"
WD = "http://www.wikidata.org/entity/"
GEO = "http://www.opengis.net/ont/geosparql#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
MAX_PLACES = 60                   # about 3% of the places with a text
MIN_TERM = 4                      # shorter labels ("Rom", "art") name too much
MIN_STEM = 6                      # a word may extend a label only if the label is this long
MIN_ALIAS = 7                     # a one-word alias must be this long
BONUS_REPEATED = 0.1              # the concept is in the place's name, or in more than one sentence
SUFFIXES = ("ismus", "ismo", "ism", "ität", "ity")   # kolonial-ismus, colonial-ism
INFLECTIONS = ("", "e", "en", "n", "s", "es", "er", "ern", "i")   # German, English, French; Italian plural in -i
WORD = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*", re.U)


LANGS = "de|fr|it|rm|en|mul"          # the languages of the country, and English
LOCAL_CONCEPT = re.compile(r"^(?P<x>.+?)\s+(?:in|a|à|en)\s+(?:Zürich|Zurich|Zuerich|Zurigo)$", re.I)
LOCAL_CONCEPT_PREFIX = re.compile(r"^(?:Zürcher|Zurich|Zürich)\s+(?P<x>.+)$", re.I)

def _terms(labels: list[str]) -> list[tuple[str, ...]]:
    out = set()
    for label in labels:
        label = re.sub(r"\s*\(.*?\)", "", label).strip().lower()
        words = tuple(WORD.findall(label))
        if words and len("".join(words)) >= MIN_TERM:
            out.add(words)
    return sorted(out)


def _match(term: tuple[str, ...], words: list[str], extend: bool = True) -> float:
    """0.6 if the sentence's words contain the term (inflected), 0.5 if a word extends it
    (only if `extend`), else 0."""
    if len(term) > 1:
        n = len(term)
        for k in range(len(words) - n + 1):
            if all(words[k + j] == term[j] or words[k + j] in {term[j] + i for i in INFLECTIONS} for j in range(n)):
                return 0.6
        return 0.0
    t = term[0]
    stem = next((t[:-len(x)] for x in SUFFIXES if t.endswith(x) and len(t) - len(x) >= MIN_STEM), t)
    best = 0.0
    for w in words:
        if w == t or w in {t + i for i in INFLECTIONS}:
            return 0.6
        if extend and len(stem) >= MIN_STEM and w.startswith(stem):
            best = 0.5
    return best


class CuratedFactSource(FactSource):
    def __init__(self, files: list[Path], base: FactSource, local=None, box=None, rewrite=None,
                 non_places: frozenset[str] = frozenset(), texts: Path | None = None, terms_cache: Path | None = None):
        self.store = ox.Store()
        for f in files:
            data = f.read_bytes()
            self.store.load(rewrite(f, data) if rewrite else data, format=ox.RdfFormat.TURTLE)
        self.base = base
        self.local = local                # LocalPOIRepository, for catalogue-only nodes
        self.box = box                    # (west, south, east, north)
        self.non_places = set(non_places) # classes whose instances are areas, never candidates
        self.texts = json.loads(texts.read_text()) if texts and texts.exists() else []
        for t in self.texts:
            t["words"] = [[w.lower() for w in WORD.findall(s)] for s in t["sentences"]]
        self.terms_cache = terms_cache
        self._terms = json.loads(terms_cache.read_text()) if terms_cache and terms_cache.exists() else {}
        self._text_nodes: set[str] = set()

    def _facts(self, nodes: set[str]) -> dict[str, list[Fact]]:
        return read_facts(self.store, nodes)

    def expand(self, nodes):
        merged = self.base.expand(nodes)
        for n, facts in self._facts(set(nodes)).items():
            merged.setdefault(n, []).extend(facts)
        return merged

    def degree(self, nodes):
        return self.base.degree(nodes)

    def own_points(self, nodes) -> dict[str, tuple[float, float]]:
        """(lon, lat) of local nodes whose geometry the curated layers state themselves."""
        out = {}
        for n in nodes:
            for r in self.store.query(f"""SELECT ?w WHERE {{ <{n}> <http://www.opengis.net/ont/geosparql#hasGeometry>/
                                         <http://www.opengis.net/ont/geosparql#asWKT> ?w }} LIMIT 1"""):
                m = re.search(r"POINT\s*\(\s*([-\d.]+)\s+([-\d.]+)", r["w"].value)
                if m:
                    out[n] = (float(m.group(1)), float(m.group(2)))
        return out

    def labels_of(self, local_ids: set[str]) -> dict[str, str]:
        """Labels the curated layers give their own local nodes, by local id (e.g. bt_57)."""
        out = {}
        for i in local_ids:
            for r in self.store.query(f"""SELECT ?l WHERE {{ <https://example.org/nami/data/plaque/{i}>
                                         <http://www.w3.org/2000/01/rdf-schema#label> ?l }} LIMIT 1"""):
                out[i] = r["l"].value
            if i not in out and hasattr(self.base, "local_label"):
                label = self.base.local_label(f"https://example.org/nami/data/plaque/{i}")
                if label:
                    out[i] = label
        return out

    def locate(self, nodes):
        catalogue = {n for n in nodes if n.startswith(LOCAL_DATA_PREFIX)}
        out = self.base.locate(set(nodes) - catalogue)
        for n, (lon, lat) in self.own_points(catalogue).items():
            out[n] = Location(lat=lat, lon=lon)
        catalogue -= set(out)
        if catalogue:                       # OSM places met as concept members are located by the base cache
            out.update(self.base.locate({n for n in catalogue if "/plaque/osm_" in n}))
            catalogue -= set(out)
        if out and self.non_places:     # a municipality or a village is an area, not a place to visit
            for n in self.base.instances_of(set(out), self.non_places):
                out.pop(n, None)
        if catalogue and self.local is not None:
            west, south, east, north = self.box or (-180, -90, 180, 90)
            for n, (lon, lat) in self.local.find_coordinates(catalogue).items():
                if west <= lon <= east and south <= lat <= north:
                    out[n] = Location(lat=lat, lon=lon)
        return out

    def instances_of(self, nodes, classes):
        return self.base.instances_of(nodes, classes)

    def containers(self, nodes):
        return self.base.containers(nodes)

    def inverse_properties(self, predicates):
        return self.base.inverse_properties(predicates)

    def class_members(self, cls):
        return self.base.class_members(cls)

    def topical(self, node):
        return self.base.topical(node)

    def area_members(self, concept):
        return self.base.area_members(concept) + self.text_members(concept)

    def concept_labels(self, concept: str) -> dict[str, list[str]]:
        """German and English labels and aliases of a Wikidata concept (cached)."""
        q = concept.removeprefix(WD)
        if q not in self._terms or self._terms[q].get("langs") != LANGS:
            body = None
            for attempt in range(6):
                try:
                    r = requests.get("https://www.wikidata.org/w/api.php", timeout=60, headers={
                        "User-Agent": "NAMI-research-prototype/0.1 (evaluation)"}, params={
                        "action": "wbgetentities", "ids": q, "props": "labels|aliases", "languages": LANGS,
                        "format": "json"})
                    if r.ok:
                        body = r.json()
                        break
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(min(5 * 2 ** attempt, 60))
            if body is None:
                raise RuntimeError(f"Wikidata unavailable for the labels of {q}")
            e = body.get("entities", {}).get(q, {})
            labels = {v["value"] for v in e.get("labels", {}).values()}
            self._terms[q] = {"langs": LANGS, "labels": sorted(labels),
                              "aliases": sorted({a["value"] for vs in e.get("aliases", {}).values() for a in vs} - labels)}
            if self.terms_cache:
                self.terms_cache.write_text(json.dumps(self._terms, ensure_ascii=False, indent=0))
        return self._terms[q]

    def text_members(self, concept: str) -> list[Fact]:
        if not self.texts or not concept.startswith(WD + "Q"):
            return []
        best: dict[tuple[tuple[str, ...], str], tuple[float, dict, str]] = {}
        said: dict[tuple[tuple[str, ...], str], set[str]] = {}      # sentences naming the term, per place
        names = self.concept_labels(concept)
        # a concept named for this city ("Reformation in Zürich", "Zürcher Reformation") is, in the city's own texts,
        # the concept itself: "Reformation" names it there
        local = set()
        for lab in names["labels"]:
            m = LOCAL_CONCEPT.match(lab) or LOCAL_CONCEPT_PREFIX.match(lab)
            if m and len(m.group("x")) >= MIN_ALIAS:
                local.add(m.group("x"))
        labels = _terms(sorted(set(names["labels"]) | local))
        aliases = [a for a in _terms(names["aliases"]) if a not in labels and (len(a) > 1 or len(a[0]) >= MIN_ALIAS)]
        for term, extend in [(x, True) for x in labels] + [(x, False) for x in aliases]:
            for t in self.texts:
                for sent, words in zip(t["sentences"], t["words"]):
                    c = _match(term, words, extend)
                    if c:
                        said.setdefault((term, t["place"]), set()).add(sent)
                    if c and c > best.get((term, t["place"]), (0,))[0]:
                        best[(term, t["place"])] = (c, t, sent)
        by_term: dict[tuple[str, ...], set[str]] = {}
        for term, place in best:
            by_term.setdefault(term, set()).add(place)
        facts: dict[str, Fact] = {}
        strong: dict[str, set[str]] = {}
        for (term, place), sents in said.items():
            if len(by_term[term]) <= MAX_PLACES:
                strong.setdefault(place, set()).update(sents)
        for (term, place), (c, t, sent) in best.items():
            if len(by_term[term]) > MAX_PLACES or place == concept:
                continue
            in_name = _match(term, [w.lower() for w in WORD.findall(t["label"] or "")], True) > 0
            if in_name or len(strong[place]) > 1:
                c = round(c + BONUS_REPEATED, 2)
            f = Fact(subject=concept, predicate=NAMI + "mentionedAt", object=place, confidence=c,
                     source=t["source"], evidence=sent)
            if place not in facts or c > facts[place].confidence:
                facts[place] = f
            if place.startswith(LOCAL_DATA_PREFIX) and place not in self._text_nodes:
                self._text_nodes.add(place)       # locate and label a place known only from its text
                g = ox.BlankNode()
                self.store.extend([
                    ox.Quad(ox.NamedNode(place), ox.NamedNode(GEO + "hasGeometry"), g),
                    ox.Quad(g, ox.NamedNode(GEO + "asWKT"), ox.Literal(f"POINT({t['lon']} {t['lat']})")),
                    ox.Quad(ox.NamedNode(place), ox.NamedNode(RDFS_LABEL), ox.Literal(t["label"]))])
        return list(facts.values())

    def describe(self, places):
        return self.base.describe(places)

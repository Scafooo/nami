"""Transform cached raw JSON (data/raw/) into RDF (Turtle): categories.ttl
(a SKOS concept scheme) and pois.ttl (points of interest linked to it via
dct:subject).

Every field the source API provides is kept: a standard vocabulary
(Schema.org, DCTERMS, SKOS, GeoSPARQL) where it fits, a small set of
custom nami: terms for the rest. Structural type (schema:X, rdf:type) is
kept separate from topical classification (SKOS category, dct:subject)
-- different kinds of fact about a POI, not interchangeable.

No per-triple provenance annotation: every triple here comes from one
uniform source, recorded once at load time via a named graph."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, URIRef, XSD
from rdflib.namespace import DCTERMS, SKOS

import config

SCHEMA = Namespace("https://schema.org/")
NAMI_ONT = Namespace(config.ONTOLOGY_IRI)
NAMI = Namespace(config.DATA_IRI)
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

DAY_NAMES = {
    "Monday": SCHEMA.Monday,
    "Tuesday": SCHEMA.Tuesday,
    "Wednesday": SCHEMA.Wednesday,
    "Thursday": SCHEMA.Thursday,
    "Friday": SCHEMA.Friday,
    "Saturday": SCHEMA.Saturday,
    "Sunday": SCHEMA.Sunday,
}


def _bind(g: Graph) -> None:
    g.bind("schema", SCHEMA)
    g.bind("nami", NAMI)
    g.bind("namiont", NAMI_ONT)
    g.bind("skos", SKOS)
    g.bind("dct", DCTERMS)
    g.bind("geo", GEO)


def poi_uri(identifier: str) -> URIRef:
    return NAMI[f"poi/{identifier}"]


def category_uri(category_id: str) -> URIRef:
    return NAMI[f"category/{category_id}"]


def load_raw() -> tuple[list[dict], dict[str, dict]]:
    tree = json.loads(config.CATEGORIES_RAW_FILE.read_text())
    pois: dict[str, dict] = {}
    for f in sorted(config.RAW_DIR.glob("id_*.json")):
        for obj in json.loads(f.read_text()):
            ident = obj.get("identifier")
            if ident and "@type" in obj and ident not in pois:
                pois[ident] = obj
    return tree, pois


# --------------------------------------------------------------------------
# Schema.org is-a closure
# --------------------------------------------------------------------------

class TypeHierarchy:
    """rdfs:subClassOf closure over ontology/schema-org-hierarchy.ttl."""

    def __init__(self, path: Path):
        self.g = Graph()
        self.g.parse(str(path), format="turtle")
        self._cache: dict[URIRef, set[URIRef]] = {}

    def ancestors(self, cls: URIRef) -> set[URIRef]:
        if cls in self._cache:
            return self._cache[cls]
        seen: set[URIRef] = set()
        frontier = [cls]
        while frontier:
            c = frontier.pop()
            for _, _, parent in self.g.triples((c, RDFS.subClassOf, None)):
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        seen.discard(SCHEMA.Thing)
        self._cache[cls] = seen
        return seen


# --------------------------------------------------------------------------
# Category tree -> SKOS, with ambiguous-name resolution
# --------------------------------------------------------------------------

class CategoryIndex:
    """Resolves a POI's name-keyed `category` object back to category ids,
    disambiguating names that appear more than once in the tree by checking
    whether a candidate's ancestor chain overlaps the POI's *other* declared
    category names -- see the worked "Opera"/"Theater" analysis.
    """

    def __init__(self, tree: list[dict]):
        self.by_id: dict[str, dict] = {c["id"]: c for c in tree}
        self.ids_by_name: dict[str, list[str]] = {}
        for c in tree:
            label = c["name"].get("en") or c["name"].get("de")
            self.ids_by_name.setdefault(label, []).append(c["id"])

    def _ancestor_names(self, cid: str) -> set[str]:
        names: set[str] = set()
        cur = self.by_id.get(cid, {}).get("parent")
        while cur and cur != "0":
            c = self.by_id.get(cur)
            if not c:
                break
            names.add(c["name"].get("en") or c["name"].get("de"))
            cur = c.get("parent")
        return names

    def resolve(self, name: str, entry: dict, other_names: set[str]) -> str | None:
        """Returns a category id for this (name, entry) on a POI whose other
        declared category names are `other_names`, or None if unresolvable.
        """
        swiss_id = entry.get("swissId")
        candidates = self.ids_by_name.get(name, [])
        if not candidates:
            return None
        if swiss_id:
            for cid in candidates:
                if self.by_id[cid].get("swissId") == swiss_id:
                    return cid
        if len(candidates) == 1:
            return candidates[0]
        # Ambiguous, no swissId: disambiguate via shared ancestry with the
        # POI's other categories.
        matches = [cid for cid in candidates if self._ancestor_names(cid) & other_names]
        if len(matches) == 1:
            return matches[0]
        return None  # genuinely unresolvable -- skip rather than guess


def build_category_graph(tree: list[dict]) -> Graph:
    g = Graph()
    _bind(g)
    scheme = NAMI["scheme/zurich-tourism-categories"]
    g.add((scheme, RDF.type, SKOS.ConceptScheme))
    g.add((scheme, RDFS.label, Literal("Zurich Tourism category taxonomy", lang="en")))

    for c in tree:
        node = category_uri(c["id"])
        g.add((node, RDF.type, SKOS.Concept))
        g.add((node, SKOS.inScheme, scheme))
        for lang, label in c["name"].items():
            if label:
                g.add((node, SKOS.prefLabel, Literal(label, lang=lang)))
        parent_id = c.get("parent")
        if parent_id and parent_id != "0":
            g.add((node, SKOS.broader, category_uri(parent_id)))
        else:
            g.add((scheme, SKOS.hasTopConcept, node))
            g.add((node, SKOS.topConceptOf, scheme))
    return g


# --------------------------------------------------------------------------
# POIs
# --------------------------------------------------------------------------

def _add_multilingual(g: Graph, subj: URIRef, pred: URIRef, value: Any) -> None:
    if not value:
        return
    if isinstance(value, dict):
        for lang, text in value.items():
            if text:
                g.add((subj, pred, Literal(str(text).strip(), lang=lang)))
    else:
        g.add((subj, pred, Literal(str(value))))


def build_poi_graph(poi: dict, hierarchy: TypeHierarchy, cat_index: CategoryIndex) -> Graph:
    g = Graph()
    _bind(g)
    subj = poi_uri(poi["identifier"])

    # --- structural type: real is-a, closed ---------------------------------
    specific = SCHEMA[poi["@type"]]
    g.add((subj, RDF.type, specific))
    for anc in hierarchy.ancestors(specific):
        g.add((subj, RDF.type, anc))

    # --- name ----------------------------------------------------------------
    _add_multilingual(g, subj, RDFS.label, poi.get("name"))

    # --- topical classification: SKOS, not rdf:type ---------------------------
    cats = poi.get("category") or {}
    if isinstance(cats, dict):
        other_names_by_name = {n: set(cats.keys()) - {n} for n in cats}
        for name, entry in cats.items():
            cid = cat_index.resolve(name, entry, other_names_by_name[name])
            if cid:
                g.add((subj, DCTERMS.subject, category_uri(cid)))

    # --- standard Schema.org / DCTERMS fields --------------------------------
    if poi.get("identifier"):
        g.add((subj, SCHEMA.identifier, Literal(poi["identifier"])))
    _add_multilingual(g, subj, SCHEMA.copyrightHolder, poi.get("copyrightHolder"))
    if poi.get("license"):
        g.add((subj, SCHEMA.license, Literal(poi["license"])))
    _add_multilingual(g, subj, SCHEMA.disambiguatingDescription, poi.get("disambiguatingDescription"))
    _add_multilingual(g, subj, SCHEMA.description, poi.get("description"))
    _add_multilingual(g, subj, SCHEMA.priceRange, poi.get("price"))
    if poi.get("dateModified"):
        g.add((subj, SCHEMA.dateModified, Literal(poi["dateModified"], datatype=XSD.dateTime)))

    if poi.get("image"):
        g.add((subj, SCHEMA.image, _image_object(g, poi["image"])))
    for photo in _as_list(poi.get("photo")):
        if photo:
            g.add((subj, SCHEMA.photo, _image_object(g, photo)))

    for feature in _as_list(poi.get("amenityFeature")):
        if not feature:
            continue
        node = BNode()
        g.add((subj, SCHEMA.amenityFeature, node))
        g.add((node, RDF.type, SCHEMA.LocationFeatureSpecification))
        g.add((node, SCHEMA.name, Literal(feature)))

    for day in _as_list(poi.get("opens")):
        day_uri = DAY_NAMES.get(day)
        if day_uri:
            g.add((subj, SCHEMA.dayOfWeek, day_uri))

    for oh in _as_list(poi.get("openingHours")):
        if oh:
            g.add((subj, SCHEMA.openingHours, Literal(oh)))

    # --- address ---------------------------------------------------------------
    addr = poi.get("address") or {}
    if addr:
        addr_node = BNode()
        g.add((subj, SCHEMA.address, addr_node))
        g.add((addr_node, RDF.type, SCHEMA.PostalAddress))
        for key, pred in (
            ("streetAddress", SCHEMA.streetAddress),
            ("addressLocality", SCHEMA.addressLocality),
            ("postalCode", SCHEMA.postalCode),
            ("addressCountry", SCHEMA.addressCountry),
        ):
            if addr.get(key):
                g.add((addr_node, pred, Literal(addr[key])))
        if addr.get("telephone"):
            g.add((subj, SCHEMA.telephone, Literal(addr["telephone"])))
        if addr.get("email"):
            g.add((subj, SCHEMA.email, Literal(addr["email"])))
        if addr.get("url"):
            g.add((subj, SCHEMA.url, URIRef(addr["url"])))

    # --- geo ---------------------------------------------------------------
    geo = poi.get("geoCoordinates")
    if geo and geo.get("latitude") is not None and geo.get("longitude") is not None:
        geom = BNode()
        g.add((subj, SCHEMA.geo, geom))
        g.add((subj, GEO.hasGeometry, geom))
        wkt = f"POINT({geo['longitude']} {geo['latitude']})"
        g.add((geom, GEO.asWKT, Literal(wkt, datatype=GEO.wktLiteral)))

    # --- nami: custom fields (no standard equivalent) ----------------------------
    if poi.get("@customType"):
        g.add((subj, NAMI_ONT.customType, Literal(poi["@customType"])))
    if poi.get("osm_id"):
        g.add((subj, NAMI_ONT.osmId, Literal(str(poi["osm_id"]))))
    _add_multilingual(g, subj, NAMI_ONT.titleTeaser, poi.get("titleTeaser"))
    _add_multilingual(g, subj, NAMI_ONT.textTeaser, poi.get("textTeaser"))
    for lang, items in (poi.get("detailedInformation") or {}).items():
        for item in _as_list(items):
            if item:
                g.add((subj, NAMI_ONT.detailedInformation, Literal(item, lang=lang)))
    if poi.get("tomasBookingId"):
        g.add((subj, NAMI_ONT.tomasBookingId, Literal(str(poi["tomasBookingId"]))))
    if poi.get("zurichCard") is not None:
        g.add((subj, NAMI_ONT.zurichCard, Literal(bool(poi["zurichCard"]), datatype=XSD.boolean)))
    _add_multilingual(g, subj, NAMI_ONT.zurichCardDescription, poi.get("zurichCardDescription"))
    _add_multilingual(g, subj, NAMI_ONT.specialOpeningHoursNote, poi.get("specialOpeningHoursSpecification"))

    for place_type in _as_list(poi.get("place")):
        if place_type in ("Indoors", "Outdoors"):
            g.add((subj, NAMI_ONT.placeType, NAMI_ONT[place_type]))

    return g


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _image_object(g: Graph, obj: dict) -> BNode:
    """schema:ImageObject for an {url, caption} pair from the source API."""
    node = BNode()
    g.add((node, RDF.type, SCHEMA.ImageObject))
    if obj.get("url"):
        g.add((node, SCHEMA.contentUrl, URIRef(obj["url"])))
    _add_multilingual(g, node, SCHEMA.caption, obj.get("caption"))
    return node


def main() -> None:
    tree, pois = load_raw()
    hierarchy = TypeHierarchy(config.ONTOLOGY_DIR / "schema-org-hierarchy.ttl")
    cat_index = CategoryIndex(tree)

    cat_graph = build_category_graph(tree)
    cat_graph.serialize(destination=str(config.RDF_DIR / "categories.ttl"), format="turtle")
    print(f"categories.ttl: {len(cat_graph)} triples ({len(tree)} concepts)")

    poi_graph = Graph()
    _bind(poi_graph)
    for poi in pois.values():
        poi_graph += build_poi_graph(poi, hierarchy, cat_index)
    poi_graph.serialize(destination=str(config.RDF_DIR / "pois.ttl"), format="turtle")
    print(f"pois.ttl: {len(poi_graph)} triples ({len(pois)} POIs)")


if __name__ == "__main__":
    main()

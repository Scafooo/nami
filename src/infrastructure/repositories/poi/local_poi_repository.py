"""LocalPOIRepository: looks up a local PointOfInterest's own catalogued
facts (label, geometry, description, images) directly from a
KnowledgeGraph -- our own example.org/nami/data/ URIs, from G_P's ABox. Not
itself a POIRepository -- an internal collaborator HybridPOIRepository
composes for local lookups (the "Local" half of the Hybrid pattern;
WikidataClient is the other half). Constructed once with the graph it
searches, rather than taking it on every call."""

from __future__ import annotations

import html
import re
from collections import defaultdict

from domain.models.poi.catalogue_entry import CatalogueEntry
from domain.models.poi.image import Image
from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph
from domain.models.poi.multilingual_text import MultilingualText
from domain.models.knowledge_graph.query import Query
from infrastructure.repositories.sparql_rows import parse_wkt_point, term_value


_UNINFORMATIVE_TYPES = {"Place", "Organization", "LocalBusiness"}
_TAG = re.compile(r"<[^>]+>")


def plain_text(markup: str) -> str:
    """The catalogue stores descriptions as HTML with entities."""
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", markup))).strip()


class LocalPOIRepository:
    def __init__(self, graph: KnowledgeGraph, abox_graphs: list[str]):
        self.graph = graph
        self.abox_graphs = abox_graphs

    def _from_clause(self) -> str:
        return "".join(f"FROM <{g}>\n" for g in self.abox_graphs)

    def find_twins(self, wikidata_ids: set[str]) -> dict[str, str]:
        """Wikidata id -> the catalogue place declared owl:sameAs to it."""
        if not wikidata_ids:
            return {}
        values = " ".join(f"<http://www.wikidata.org/entity/{i}>" for i in wikidata_ids)
        q = Query(sparql=(
            "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
            f"SELECT ?local ?wd\n{self._from_clause()}WHERE {{\n"
            f"  VALUES ?wd {{ {values} }}\n  ?local owl:sameAs ?wd .\n}}"
        ))
        return {term_value(sol, "wd").rsplit("/", 1)[-1]: term_value(sol, "local") for sol in self.graph.query(q)}

    def find_catalogue_entries(self, uris: set[str]) -> dict[str, CatalogueEntry]:
        """The catalogue record of each catalogue uri in `uris` that has
        one: schema types, category labels, postal address, teaser,
        description (as plain text) and images."""
        if not uris:
            return {}
        values = " ".join(f"<{u}>" for u in uris)
        head = f"{self._from_clause()}WHERE {{\n  VALUES ?u {{ {values} }}\n"
        entries = {u: CatalogueEntry(uri=u) for u in self._known(uris)}

        for sol in self.graph.query(Query(sparql=(
            f"SELECT DISTINCT ?u ?t\n{head}  ?u a ?t .\n  FILTER(STRSTARTS(STR(?t), \"https://schema.org/\"))\n}}"
        ))):
            name = term_value(sol, "t").rsplit("/", 1)[-1]
            if name not in _UNINFORMATIVE_TYPES and term_value(sol, "u") in entries:
                entries[term_value(sol, "u")].types.append(name)

        for sol in self.graph.query(Query(sparql=(
            "PREFIX dct: <http://purl.org/dc/terms/>\nPREFIX skos: <http://www.w3.org/2004/02/skos/core#>\n"
            f"SELECT DISTINCT ?u ?l\n{head}  ?u dct:subject ?c .\n  ?c skos:prefLabel ?l .\n"
            "  FILTER(LANG(?l) = \"en\")\n}"
        ))):
            if term_value(sol, "u") in entries:
                entries[term_value(sol, "u")].categories.append(term_value(sol, "l"))

        for sol in self.graph.query(Query(sparql=(
            "PREFIX schema: <https://schema.org/>\n"
            f"SELECT DISTINCT ?u ?street ?zip ?city\n{head}  ?u schema:address ?a .\n"
            "  OPTIONAL { ?a schema:streetAddress ?street }\n  OPTIONAL { ?a schema:postalCode ?zip }\n"
            "  OPTIONAL { ?a schema:addressLocality ?city }\n}"
        ))):
            u = term_value(sol, "u")
            if u in entries:
                town = " ".join(x for x in (term_value(sol, "zip"), term_value(sol, "city")) if x)
                entries[u].address = ", ".join(x for x in (term_value(sol, "street"), town) if x) or None

        teasers: dict[str, dict[str, str]] = defaultdict(dict)
        for sol in self.graph.query(Query(sparql=(
            "PREFIX namiont: <https://example.org/nami/ontology#>\n"
            f"SELECT DISTINCT ?u ?t (LANG(?t) AS ?lang)\n{head}  ?u namiont:textTeaser ?t .\n}}"
        ))):
            if term_value(sol, "lang"):
                teasers[term_value(sol, "u")][term_value(sol, "lang")] = term_value(sol, "t")

        descriptions = self.find_descriptions(set(uris))
        images = self.find_images(set(uris))
        for u, entry in entries.items():
            entry.teaser = MultilingualText(by_language=teasers.get(u, {}))
            if u in descriptions:
                entry.description = MultilingualText(
                    by_language={lang: plain_text(t) for lang, t in descriptions[u].by_language.items()}
                )
            entry.images = images.get(u, [])
        return entries

    def _known(self, uris: set[str]) -> set[str]:
        values = " ".join(f"<{u}>" for u in uris)
        q = Query(sparql=f"SELECT DISTINCT ?u\n{self._from_clause()}WHERE {{\n  VALUES ?u {{ {values} }}\n  ?u ?p ?o .\n}}")
        return {term_value(sol, "u") for sol in self.graph.query(q)}

    def find_places_by_type(self, schema_types: set[str]) -> list[tuple[str, str, float, float, str]]:
        """(uri, label, lon, lat, matched type) for every catalogue place
        typed as one of `schema_types` -- one row per place and type."""
        if not schema_types:
            return []
        values = " ".join(f"schema:{t}" for t in schema_types)
        q = Query(sparql=(
            "PREFIX schema: <https://schema.org/>\nPREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
            f"SELECT DISTINCT ?u ?label ?wkt ?t\n{self._from_clause()}WHERE {{\n"
            f"  VALUES ?t {{ {values} }}\n  ?u a ?t .\n  ?u geo:hasGeometry/geo:asWKT ?wkt .\n"
            "  ?u rdfs:label ?label . FILTER(LANG(?label) = \"en\")\n}"
        ))
        rows = []
        for sol in self.graph.query(q):
            coord = parse_wkt_point(term_value(sol, "wkt"))
            if coord is not None:
                rows.append((term_value(sol, "u"), term_value(sol, "label"), coord[0], coord[1],
                             term_value(sol, "t").rsplit("/", 1)[-1]))
        return rows

    def find_coordinates(self, uris: set[str]) -> dict[str, tuple[float, float]]:
        """Looks up geo:hasGeometry/geo:asWKT for each of `uris`, parsed
        to (lon, lat) -- silently omits a uri with no geometry rather
        than raising, since the caller decides what an unresolved
        coordinate means for it."""
        if not uris:
            return {}
        from_clause = "".join(f"FROM <{g}>\n" for g in self.abox_graphs)
        values = " ".join(f"<{u}>" for u in uris)
        q = Query(sparql=(
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
            f"SELECT ?u ?wkt\n{from_clause}WHERE {{\n"
            f"  VALUES ?u {{ {values} }}\n"
            f"  ?u geo:hasGeometry/geo:asWKT ?wkt .\n}}"
        ))
        result: dict[str, tuple[float, float]] = {}
        for sol in self.graph.query(q):
            coord = parse_wkt_point(term_value(sol, "wkt"))
            if coord is not None:
                result[term_value(sol, "u")] = coord
        return result

    def find_labels(self, uris: set[str]) -> dict[str, str]:
        """Looks up rdfs:label or schema:name (English, or language-
        untagged) for each of `uris`."""
        if not uris:
            return {}
        from_clause = "".join(f"FROM <{g}>\n" for g in self.abox_graphs)
        values = " ".join(f"<{u}>" for u in uris)
        q = Query(sparql=(
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX schema: <https://schema.org/>\n"
            f"SELECT ?u ?label\n{from_clause}WHERE {{\n"
            f"  VALUES ?u {{ {values} }}\n"
            f'  {{ ?u rdfs:label ?label }} UNION {{ ?u schema:name ?label }}\n'
            f'  FILTER(LANG(?label) = "en" || LANG(?label) = "")\n}}'
        ))
        result: dict[str, str] = {}
        for sol in self.graph.query(q):
            result[term_value(sol, "u")] = term_value(sol, "label")
        return result

    def find_descriptions(self, uris: set[str]) -> dict[str, MultilingualText]:
        """Looks up schema:description for each of `uris`, in every
        language present -- a caller decides which one to show."""
        if not uris:
            return {}
        from_clause = "".join(f"FROM <{g}>\n" for g in self.abox_graphs)
        values = " ".join(f"<{u}>" for u in uris)
        q = Query(sparql=(
            "PREFIX schema: <https://schema.org/>\n"
            f"SELECT DISTINCT ?u ?desc (LANG(?desc) AS ?lang)\n{from_clause}WHERE {{\n"
            f"  VALUES ?u {{ {values} }}\n"
            f"  ?u schema:description ?desc .\n}}"
        ))
        by_uri: dict[str, dict[str, str]] = defaultdict(dict)
        for sol in self.graph.query(q):
            lang = term_value(sol, "lang")
            if lang:
                by_uri[term_value(sol, "u")][lang] = term_value(sol, "desc")
        return {uri: MultilingualText(by_language=by_lang) for uri, by_lang in by_uri.items()}

    def find_images(self, uris: set[str]) -> dict[str, list[Image]]:
        """Looks up schema:image/schema:photo -> schema:ImageObject for
        each of `uris`, each with its caption in every language present.
        Both predicates feed the same list, undistinguished."""
        if not uris:
            return {}
        from_clause = "".join(f"FROM <{g}>\n" for g in self.abox_graphs)
        values = " ".join(f"<{u}>" for u in uris)
        q = Query(sparql=(
            "PREFIX schema: <https://schema.org/>\n"
            f"SELECT DISTINCT ?u ?url ?caption (LANG(?caption) AS ?lang)\n{from_clause}WHERE {{\n"
            f"  VALUES ?u {{ {values} }}\n"
            f"  {{ ?u schema:image ?img }} UNION {{ ?u schema:photo ?img }}\n"
            f"  ?img schema:contentUrl ?url .\n"
            f"  OPTIONAL {{ ?img schema:caption ?caption }}\n}}"
        ))
        urls_by_uri: dict[str, list[str]] = defaultdict(list)
        captions: dict[tuple[str, str], dict[str, str]] = defaultdict(dict)
        for sol in self.graph.query(q):
            uri, url = term_value(sol, "u"), term_value(sol, "url")
            if url not in urls_by_uri[uri]:
                urls_by_uri[uri].append(url)
            caption, lang = term_value(sol, "caption"), term_value(sol, "lang")
            if caption and lang:
                captions[(uri, url)][lang] = caption
        return {
            uri: [Image(url=url, caption=MultilingualText(by_language=captions[(uri, url)])) for url in urls]
            for uri, urls in urls_by_uri.items()
        }

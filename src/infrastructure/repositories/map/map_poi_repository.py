"""MapPOIRepositoryImpl: queries the ABox named graph directly for every
POI with geometry, so the map always reflects whatever is currently
loaded in store/."""

from __future__ import annotations

from dataclasses import dataclass

from rdflib import RDFS, Graph, Namespace

import config
from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph
from domain.models.knowledge_graph.query import Query
from domain.models.poi.map_poi import MapPOI
from domain.repositories.map.map_poi_repository import MapPOIRepository

SCHEMA = Namespace("https://schema.org/")


def _leaf_type(hierarchy: Graph, types: list[str]) -> str:
    """The most specific of `types` per the schema.org subclass hierarchy
    -- a POI's rdf:type set includes every ancestor class, not just the
    leaf, so picking the one with no descendant among the others present
    recovers the leaf."""

    def is_ancestor_of(a, b) -> bool:
        frontier, seen = [b], set()
        while frontier:
            c = frontier.pop()
            for _, _, parent in hierarchy.triples((c, RDFS.subClassOf, None)):
                if parent == a:
                    return True
                if parent not in seen:
                    seen.add(parent)
                    frontier.append(parent)
        return False

    uris = [SCHEMA[t] for t in types]
    for i, t in enumerate(uris):
        if not any(is_ancestor_of(t, other) for j, other in enumerate(uris) if j != i):
            return types[i]
    return types[0]


@dataclass
class MapPOIRepositoryImpl(MapPOIRepository):
    kg: KnowledgeGraph

    def find_with_geometry(self, city_only: bool = True) -> list[MapPOI]:
        q = Query(sparql=f"""
        PREFIX schema: <https://schema.org/>
        PREFIX geo: <http://www.opengis.net/ont/geosparql#>
        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
        SELECT ?poi ?label ?type ?wkt ?locality WHERE {{
          GRAPH <{config.PROVENANCE_GRAPH_IRI}> {{
            ?poi geo:hasGeometry/geo:asWKT ?wkt .
            ?poi a ?type .
            FILTER(?type != schema:Place)
            OPTIONAL {{ ?poi rdfs:label ?label . FILTER(LANG(?label) = "en") }}
            OPTIONAL {{ ?poi schema:address/schema:addressLocality ?locality }}
          }}
        }}
        """)
        by_poi: dict[str, dict] = {}
        for row in self.kg.query(q):
            poi, label, ptype, wkt, locality = [x.value if x is not None else None for x in row]
            entry = by_poi.setdefault(
                poi, {"id": poi, "name": label or "", "types": [], "wkt": wkt, "locality": locality or ""}
            )
            entry["types"].append(ptype.rsplit("/", 1)[-1])

        hierarchy = Graph()
        hierarchy.parse(str(config.ONTOLOGY_DIR / "schema-org-hierarchy.ttl"), format="turtle")

        out = []
        for entry in by_poi.values():
            if city_only and entry["locality"] != "Zürich":
                continue
            coords = entry["wkt"][entry["wkt"].index("(") + 1 : entry["wkt"].index(")")].split()
            lon, lat = float(coords[0]), float(coords[1])
            out.append(
                MapPOI(
                    uri=entry["id"],
                    name=entry["name"],
                    category=_leaf_type(hierarchy, entry["types"]),
                    lat=lat,
                    lon=lon,
                    locality=entry["locality"],
                )
            )
        return out

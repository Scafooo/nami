"""UserRepositoryImpl: the only place that knows a G_U graph holds
exactly one nami:User subject, how to find it, and how to map it onto a
plain User. Constructed with the graph it searches, so find() takes no
arguments -- each G_U file loaded is already one user's own graph."""

from __future__ import annotations

from datetime import datetime

import config
from domain.models.coordinates.coordinates import Coordinates
from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph
from domain.models.user.position_record import PositionRecord
from domain.models.knowledge_graph.query import Query
from domain.models.user.user import User
from domain.repositories.user.user_repository import UserRepository


class UserRepositoryImpl(UserRepository):
    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph

    def find(self) -> User:
        """Finds the nami:User subject in this repository's graph and
        returns it as a fully-populated User -- the graph is expected to
        hold exactly one."""
        uri = self._find_uri()
        return User(uri=uri, interests=self._find_interests(uri), position=self._find_position(uri))

    def record_position(self, user: User, lat: float, lon: float, at: datetime) -> None:
        self.graph.update(Query(sparql=(
            f"PREFIX nami: <{config.ONTOLOGY_IRI}>\n"
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
            "INSERT DATA {\n"
            f"  <{user.uri}> nami:hasPosition _:r .\n"
            f'  _:r nami:recordedAt "{at.isoformat()}"^^xsd:dateTime ;\n'
            f'      geo:hasGeometry [ geo:asWKT "Point({lon} {lat})" ] .\n'
            "}"
        )))
        user.position = PositionRecord(coordinates=Coordinates(lat=lat, lon=lon), at=at)

    def _find_uri(self) -> str:
        rows = list(self.graph.query(Query(
            sparql=f"PREFIX nami: <{config.ONTOLOGY_IRI}> SELECT ?u WHERE {{ ?u a nami:User }}"
        )))
        return rows[0][0].value

    def _find_interests(self, uri: str) -> list[str]:
        q = Query(sparql=(
            f"PREFIX nami: <{config.ONTOLOGY_IRI}>\n"
            f"SELECT ?i WHERE {{ <{uri}> nami:interestedIn ?i }}"
        ))
        return [f"<{row[0].value}>" for row in self.graph.query(q)]

    def _find_position(self, uri: str) -> PositionRecord | None:
        """The most recently recorded position, or None if this user's
        position has never been recorded."""
        q = Query(sparql=(
            f"PREFIX nami: <{config.ONTOLOGY_IRI}>\n"
            "PREFIX geo: <http://www.opengis.net/ont/geosparql#>\n"
            f"SELECT ?wkt ?at WHERE {{\n"
            f"  <{uri}> nami:hasPosition ?r .\n"
            "  ?r nami:recordedAt ?at ; geo:hasGeometry/geo:asWKT ?wkt .\n"
            "} ORDER BY DESC(?at) LIMIT 1"
        ))
        rows = list(self.graph.query(q))
        if not rows:
            return None
        wkt, at = rows[0][0].value, rows[0][1].value
        lon_str, lat_str = wkt[wkt.index("(") + 1 : wkt.index(")")].split()
        # Oxigraph normalizes a UTC xsd:dateTime to a trailing "Z", which
        # datetime.fromisoformat() only accepts from Python 3.11 on --
        # verified this project's 3.9 rejects it outright.
        if at.endswith("Z"):
            at = at[:-1] + "+00:00"
        return PositionRecord(coordinates=Coordinates(lat=float(lat_str), lon=float(lon_str)), at=datetime.fromisoformat(at))

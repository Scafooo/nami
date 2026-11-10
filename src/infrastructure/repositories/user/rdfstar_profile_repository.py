"""Reads a UserProfile from a G_U file in Turtle 1.2: :interestedIn edges
annotated with RDF-star (weight, source, linkConfidence, period), plus
plain filters, weights, feedback, and settings."""

from __future__ import annotations

from pathlib import Path

import pyoxigraph as ox

from domain.models.retrieval.fact import Interval
from domain.models.user.profile import Seed, UserProfile
from infrastructure.repositories.retrieval.wikidata_fact_source import _date

HU = "https://example.org/nami/ontology/user#"


def _v(term):
    return None if term is None else term.value


def load_profile(path: Path) -> UserProfile:
    store = ox.Store()
    store.load(path.read_bytes(), format=ox.RdfFormat.TURTLE)
    q = lambda sparql: list(store.query(f"PREFIX hu: <{HU}>\n" + sparql))
    users = {_v(r["u"]) for r in q("SELECT DISTINCT ?u WHERE { ?u hu:interestedIn ?e }")}
    if len(users) != 1:
        raise ValueError(f"{path}: expected exactly one user with interests, found {len(users)}")
    uri = users.pop()
    u = f"<{uri}>"
    seeds = []
    for r in q(f"""SELECT ?e ?w ?s ?l ?from ?to WHERE {{
        {u} hu:interestedIn ?e .
        OPTIONAL {{ {u} hu:interestedIn ?e {{| hu:weight ?w |}} }}
        OPTIONAL {{ {u} hu:interestedIn ?e {{| hu:source ?s |}} }}
        OPTIONAL {{ {u} hu:interestedIn ?e {{| hu:linkConfidence ?l |}} }}
        OPTIONAL {{ {u} hu:interestedIn ?e {{| hu:periodStart ?from |}} }}
        OPTIONAL {{ {u} hu:interestedIn ?e {{| hu:periodEnd ?to |}} }} }}"""):
        seeds.append(Seed(
            iri=_v(r["e"]),
            weight=float(_v(r["w"])) if r["w"] is not None else 1.0,
            source=_v(r["s"]),
            link_confidence=float(_v(r["l"])) if r["l"] is not None else 1.0,
            period=Interval(_date(_v(r["from"])), _date(_v(r["to"]))),
        ))
    objects = lambda p: {_v(r["o"]) for r in q(f"SELECT ?o WHERE {{ {u} hu:{p} ?o }}")}
    depth = objects("maxDepth")
    lang = objects("language")
    feedback = lambda p: [(_v(r["o"]), _v(r["via"])) for r in q(
        f"SELECT ?o ?via WHERE {{ {u} hu:{p} ?o . OPTIONAL {{ {u} hu:{p} ?o {{| hu:via ?via |}} }} }}")]
    return UserProfile(
        uri=uri, seeds=sorted(seeds, key=lambda s: s.iri),
        not_interested_in=objects("notInterestedIn"),
        preferred_relations=objects("prefersRelation"),
        max_depth=int(depth.pop()) if depth else None,
        language=lang.pop() if lang else "en",
        requires=objects("requires"),
        accepted=feedback("accepted"), rejected=feedback("rejected"),
    )


def profile_from_interests(ids: list[str], uri: str = "urn:nami:user:scenario") -> UserProfile:
    """A default profile for a bare list of Wikidata ids: every interest
    declared, with full weight and certain linking."""
    return UserProfile(uri=uri, seeds=[Seed(f"http://www.wikidata.org/entity/{i}") for i in ids])

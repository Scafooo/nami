"""Reading and writing annotated triples in an Oxigraph store, in the
RDF 1.2 form the paper describes: the asserted triple, plus one reifier
per statement (rdf:reifies a triple term) carrying its annotations.
Oxigraph indexes every permutation of a triple, so "all triples
containing x, in either direction" is an index scan."""

from __future__ import annotations

import hashlib
from datetime import date

import pyoxigraph as ox

from domain.models.retrieval.fact import Fact, Interval

NAMI = "https://example.org/nami/ontology#"
RDF_REIFIES = ox.NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#reifies")
XSD = "http://www.w3.org/2001/XMLSchema#"
P_CONF, P_SRC = ox.NamedNode(NAMI + "confidence"), ox.NamedNode(NAMI + "source")
P_START, P_END = ox.NamedNode(NAMI + "startTime"), ox.NamedNode(NAMI + "endTime")
P_ROLE, P_RETRIEVED = ox.NamedNode(NAMI + "role"), ox.NamedNode(NAMI + "retrievedAt")
P_EVIDENCE = ox.NamedNode(NAMI + "evidence")
# The nami: relations of G_P itself, read from the catalogue text
# (mappings/text_relations.ttl); every other nami: predicate is bookkeeping.
TEXT_RELATIONS = frozenset(NAMI + r for r in (
    "mentionedAt", "livedAt", "buriedAt", "workedAt", "foundedAt", "frequented", "performedAt", "commemoratedAt",
    "themedAfter", "bornAt", "createdAt"))


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        y, m, d = value[:10].split("-")
        return date(int(y), max(int(m), 1), max(int(d), 1))
    except ValueError:
        return None


def iri(value: str) -> ox.NamedNode:
    """A NamedNode, percent-encoding what Oxigraph rejects (e.g. a second '#' in a
    reference URL taken from Wikidata)."""
    try:
        return ox.NamedNode(value)
    except ValueError:
        from urllib.parse import quote
        head, sep, frag = value.partition("#")
        return ox.NamedNode(quote(head, safe=":/?&=%@+,;~!$'()*") + (sep + quote(frag, safe="") if sep else ""))


def fact_quads(fact: Fact, graph=None, retrieved: date | None = None) -> list[ox.Quad]:
    g = graph if graph is not None else ox.DefaultGraph()
    s, p, o = ox.NamedNode(fact.subject), ox.NamedNode(fact.predicate), ox.NamedNode(fact.object)
    quads = [ox.Quad(s, p, o, g)]
    key = "|".join(map(str, (fact.subject, fact.predicate, fact.object, fact.confidence, fact.source,
                             fact.validity.start, fact.validity.end, fact.role)))
    r = ox.NamedNode("urn:nami:statement:" + hashlib.sha1(key.encode()).hexdigest())
    quads.append(ox.Quad(r, RDF_REIFIES, ox.Triple(s, p, o), g))
    if fact.confidence is not None:
        quads.append(ox.Quad(r, P_CONF, ox.Literal(repr(float(fact.confidence)), datatype=ox.NamedNode(XSD + "double")), g))
    if fact.source is not None:
        quads.append(ox.Quad(r, P_SRC, iri(fact.source), g))
    if fact.validity.start:
        quads.append(ox.Quad(r, P_START, ox.Literal(fact.validity.start.isoformat(), datatype=ox.NamedNode(XSD + "date")), g))
    if fact.validity.end:
        quads.append(ox.Quad(r, P_END, ox.Literal(fact.validity.end.isoformat(), datatype=ox.NamedNode(XSD + "date")), g))
    if fact.role is not None:
        quads.append(ox.Quad(r, P_ROLE, ox.NamedNode(fact.role), g))
    if fact.evidence is not None:
        quads.append(ox.Quad(r, P_EVIDENCE, ox.Literal(fact.evidence), g))
    if retrieved is not None:
        quads.append(ox.Quad(r, P_RETRIEVED, ox.Literal(retrieved.isoformat(), datatype=ox.NamedNode(XSD + "date")), g))
    return quads


def read_facts(store: ox.Store, nodes: set[str], batch: int = 200) -> dict[str, list[Fact]]:
    """Every asserted triple containing one of `nodes`, one Fact per
    statement (reifier), or one unannotated Fact if it has none. Reads
    the permutation indexes directly: (x, ?, ?), (?, ?, x), and the
    reifiers of each triple through the triple term in object position."""
    out: dict[str, list[Fact]] = {}
    for n in nodes:
        node = ox.NamedNode(n)
        seen: set[Fact] = set()
        facts: list[Fact] = []
        triples = [(q.subject, q.predicate, q.object) for q in store.quads_for_pattern(node, None, None)]
        triples += [(q.subject, q.predicate, q.object) for q in store.quads_for_pattern(None, None, node)]
        for s_, p_, o_ in triples:
            if not isinstance(s_, ox.NamedNode) or not isinstance(o_, ox.NamedNode):
                continue
            if p_ == RDF_REIFIES or (p_.value.startswith(NAMI) and p_.value not in TEXT_RELATIONS) \
                    or s_.value.startswith("urn:nami:statement:"):
                continue
            reifiers = {q.subject for q in store.quads_for_pattern(None, RDF_REIFIES, ox.Triple(s_, p_, o_))}
            if not reifiers:
                variants = [Fact(s_.value, p_.value, o_.value)]
            else:
                variants = []
                for r in reifiers:
                    ann = {q.predicate.value: q.object.value for q in store.quads_for_pattern(r, None, None)
                           if q.predicate != RDF_REIFIES}
                    variants.append(Fact(
                        s_.value, p_.value, o_.value,
                        confidence=float(ann[P_CONF.value]) if P_CONF.value in ann else None,
                        source=ann.get(P_SRC.value),
                        validity=Interval(_parse_date(ann.get(P_START.value)), _parse_date(ann.get(P_END.value))),
                        role=ann.get(P_ROLE.value),
                        evidence=ann.get(P_EVIDENCE.value)))
            for f in variants:
                if f not in seen:
                    seen.add(f)
                    facts.append(f)
        out[n] = facts
    return out

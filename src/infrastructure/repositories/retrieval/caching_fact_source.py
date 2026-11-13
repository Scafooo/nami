"""CachingFactSource: a write-through local copy of G_P. Every answer the
remote source (Wikidata) gives is stored in an on-disk Oxigraph store --
facts as annotated RDF-star statements, plus bookkeeping about which
nodes were expanded, their degree, and their location -- and served
locally from then on. The local graph thus grows into the slice of G_P
that retrieval has actually needed."""

from __future__ import annotations

import re

from pathlib import Path

import pyoxigraph as ox

from datetime import date

from domain.models.retrieval.fact import Interval
from domain.repositories.retrieval.fact_source import FactSource, Location, PlaceInfo
from infrastructure.repositories.retrieval.annotated_store import NAMI, _parse_date, fact_quads, read_facts

FACTS = ox.NamedNode("urn:nami:graph:facts")
META = ox.NamedNode("urn:nami:graph:meta")
XSD = "http://www.w3.org/2001/XMLSchema#"
def N(s):                                        # through iri(): Wikidata may give IRIs Oxigraph rejects
    from infrastructure.repositories.retrieval.annotated_store import iri
    return iri(s)
EXPANDED, DEGREE, CHECKED = N(NAMI + "expanded"), N(NAMI + "degree"), N(NAMI + "locationChecked")
LAT, LON, VIA = N(NAMI + "lat"), N(NAMI + "lon"), N(NAMI + "locatedVia")
WITHIN, WITHIN_CHECKED = N(NAMI + "standsWithin"), N(NAMI + "withinChecked")
INVERSE, INVERSE_CHECKED = N(NAMI + "inverseProperty"), N(NAMI + "inverseChecked")
INSTANCE_CHECKED, INSTANCE_OF = N(NAMI + "instanceChecked"), N(NAMI + "instanceOfExcluded")
MEMBERS_FETCHED, MEMBER_OF = N(NAMI + "membersFetched"), N(NAMI + "memberOf")
LABEL = N("http://www.w3.org/2000/01/rdf-schema#label")
EXIST_FROM, EXIST_TO = N(NAMI + "existsFrom"), N(NAMI + "existsUntil")
DESCRIBED, VISIT, ACCESSIBLE = N(NAMI + "described"), N(NAMI + "visitMinutes"), N(NAMI + "accessible")
TOPICAL_FETCHED, TOPICAL, AREA_FETCHED = N(NAMI + "topicalFetched"), N(NAMI + "topical"), N(NAMI + "areaFetched")
AREA = ox.NamedNode("urn:nami:graph:area-members")
TRUE = ox.Literal("true", datatype=N(XSD + "boolean"))


class _ReadOnlyCache:
    """The cache opened read-only while another run holds it: reads as usual, and what this run
    fetches is not stored (it is fetched again next time)."""
    def __init__(self, store):
        self._store = store

    def extend(self, quads) -> None:
        pass

    def __getattr__(self, name):
        return getattr(self._store, name)


class CachingFactSource(FactSource):
    def __init__(self, remote: FactSource, store_dir: Path):
        store_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.store = ox.Store(str(store_dir))
        except OSError:
            self.store = _ReadOnlyCache(ox.Store.read_only(str(store_dir)))
        self.remote = remote
        self.local_hits = 0
        self.remote_calls = 0

    @property
    def queries(self):
        return getattr(self.remote, "queries", None)

    def _meta(self, s, p) -> list:
        return [q.object for q in self.store.quads_for_pattern(N(s), p, None, META)]

    def _flag(self, s, p) -> bool:
        return bool(self._meta(s, p))

    def expand(self, nodes):
        done = {n for n in nodes if self._flag(n, EXPANDED)}
        todo = set(nodes) - done
        out = read_facts(self.store, done) if done else {}
        self.local_hits += len(done)
        if todo:
            self.remote_calls += 1
            fetched = self.remote.expand(todo)
            today = date.today()
            quads = [q for facts in fetched.values() for f in facts for q in fact_quads(f, FACTS, retrieved=today)]
            quads += [ox.Quad(N(n), EXPANDED, TRUE, META) for n in todo]
            self.store.extend(quads)
            out.update(fetched)
        return out

    def degree(self, nodes):
        out = {}
        todo = set()
        for n in nodes:
            d = self._meta(n, DEGREE)
            if d:
                out[n] = int(d[0].value)
            else:
                todo.add(n)
        todo = sorted(todo)
        for i in range(0, len(todo), 24):      # stored in small chunks: a run cut short keeps what it counted
            fetched = self.remote.degree(set(todo[i:i + 24]))
            self.store.extend([ox.Quad(N(n), DEGREE, ox.Literal(str(c), datatype=N(XSD + "integer")), META)
                               for n, c in fetched.items()])
            out.update(fetched)
        return out

    def locate(self, nodes):
        out, todo = {}, set()
        for n in nodes:
            if self._flag(n, CHECKED):
                lat, lon = self._meta(n, LAT), self._meta(n, LON)
                if lat and lon:
                    via = self._meta(n, VIA)
                    b, e = self._meta(n, EXIST_FROM), self._meta(n, EXIST_TO)
                    existence = Interval(_parse_date(b[0].value) if b else None, _parse_date(e[0].value) if e else None)
                    out[n] = Location(float(lat[0].value), float(lon[0].value), via[0].value if via else None, existence)
            else:
                todo.add(n)
        if todo:
            fetched = self.remote.locate(todo)
            quads = [ox.Quad(N(n), CHECKED, TRUE, META) for n in todo]
            for n, loc in fetched.items():
                quads += [ox.Quad(N(n), LAT, ox.Literal(repr(loc.lat), datatype=N(XSD + "double")), META),
                          ox.Quad(N(n), LON, ox.Literal(repr(loc.lon), datatype=N(XSD + "double")), META)]
                if loc.via:
                    quads.append(ox.Quad(N(n), VIA, N(loc.via), META))
                if loc.existence.start:
                    quads.append(ox.Quad(N(n), EXIST_FROM, ox.Literal(loc.existence.start.isoformat()), META))
                if loc.existence.end:
                    quads.append(ox.Quad(N(n), EXIST_TO, ox.Literal(loc.existence.end.isoformat()), META))
            self.store.extend(quads)
            out.update(fetched)
        return out

    def containers(self, nodes):
        out, todo = {}, set()
        for n in nodes:
            if self._flag(n, WITHIN_CHECKED):
                w = self._meta(n, WITHIN)
                if w:
                    out[n] = w[0].value
            else:
                todo.add(n)
        if todo:
            fetched = self.remote.containers(todo)
            self.store.extend([ox.Quad(N(n), WITHIN_CHECKED, TRUE, META) for n in todo]
                              + [ox.Quad(N(n), WITHIN, N(c), META) for n, c in fetched.items()])
            out.update(fetched)
        return out

    def inverse_properties(self, predicates):
        out, todo = {}, set()
        for p in predicates:
            if self._flag(p, INVERSE_CHECKED):
                q = self._meta(p, INVERSE)
                if q:
                    out[p] = q[0].value
            else:
                todo.add(p)
        if todo:
            fetched = self.remote.inverse_properties(todo)
            self.store.extend([ox.Quad(N(p), INVERSE_CHECKED, TRUE, META) for p in todo]
                              + [ox.Quad(N(p), INVERSE, N(q), META) for p, q in fetched.items()])
            out.update(fetched)
        return out

    def instances_of(self, nodes, classes):
        hits, todo = set(), set()
        for n in nodes:
            checked = {o.value for o in self._meta(n, INSTANCE_CHECKED)}
            if classes <= checked:
                if {o.value for o in self._meta(n, INSTANCE_OF)} & classes:
                    hits.add(n)
            else:
                todo.add(n)
        if todo:
            found = self.remote.instances_of(todo, classes)
            quads = [ox.Quad(N(n), INSTANCE_CHECKED, N(c), META) for n in todo for c in classes]
            # instances_of does not say which class matched; record the whole set.
            quads += [ox.Quad(N(n), INSTANCE_OF, N(c), META) for n in found for c in classes]
            self.store.extend(quads)
            hits |= found
        return hits

    def describe(self, places):
        out, todo = {}, set()
        for n in places:
            if self._flag(n, DESCRIBED):
                v, a = self._meta(n, VISIT), self._meta(n, ACCESSIBLE)
                if v or a:
                    out[n] = PlaceInfo(float(v[0].value) if v else None, (a[0].value == "true") if a else None)
            else:
                todo.add(n)
        if todo:
            fetched = self.remote.describe(todo)
            quads = [ox.Quad(N(n), DESCRIBED, TRUE, META) for n in todo]
            for n, info in fetched.items():
                if info.visit_min is not None:
                    quads.append(ox.Quad(N(n), VISIT, ox.Literal(repr(float(info.visit_min)), datatype=N(XSD + "double")), META))
                if info.accessible is not None:
                    quads.append(ox.Quad(N(n), ACCESSIBLE, ox.Literal("true" if info.accessible else "false",
                                                                     datatype=N(XSD + "boolean")), META))
            self.store.extend(quads)
            out.update(fetched)
        return out

    def topical(self, node):
        if self._flag(node, TOPICAL_FETCHED):
            return [o.value for o in self._meta(node, TOPICAL)]
        found = self.remote.topical(node)
        self.store.extend([ox.Quad(N(node), TOPICAL_FETCHED, TRUE, META)] +
                          [ox.Quad(N(node), TOPICAL, N(c), META) for c in found])
        return found

    def area_members(self, concept):
        if self._flag(concept, AREA_FETCHED):
            from domain.models.retrieval.fact import Fact
            def osm(node: str) -> str | None:     # a map feature keeps its OpenStreetMap object as source
                m = re.search(r"/osm_(node|way|relation)_(\d+)$", node)
                return f"https://www.openstreetmap.org/{m.group(1)}/{m.group(2)}" if m else None
            return [Fact(q.subject.value, q.predicate.value, concept,
                         confidence=0.9 if osm(q.subject.value) else 1.0, source=osm(q.subject.value))
                    for q in self.store.quads_for_pattern(None, None, N(concept), AREA)]
        found = self.remote.area_members(concept)
        quads = [ox.Quad(N(concept), AREA_FETCHED, TRUE, META)]
        quads += [ox.Quad(N(f.subject), N(f.predicate), N(concept), AREA) for f in found]
        for node, (lat, lon, name) in getattr(self.remote, "osm_places", {}).items():
            if node.startswith("https://example.org/nami/"):       # OSM places with no Wikidata item: locate and name them
                quads += [ox.Quad(N(node), CHECKED, TRUE, META),
                          ox.Quad(N(node), LAT, ox.Literal(repr(lat), datatype=N(XSD + "double")), META),
                          ox.Quad(N(node), LON, ox.Literal(repr(lon), datatype=N(XSD + "double")), META),
                          ox.Quad(N(node), LABEL, ox.Literal(name), META)]
        self.store.extend(quads)
        return found

    def class_members(self, cls):
        if self._flag(cls, MEMBERS_FETCHED):
            from domain.models.retrieval.fact import Fact
            return [Fact(q.subject.value, "http://www.wikidata.org/prop/direct/P31", cls, confidence=1.0)
                    for q in self.store.quads_for_pattern(None, MEMBER_OF, N(cls), META)]
        members = self.remote.class_members(cls)
        quads = [ox.Quad(N(cls), MEMBERS_FETCHED, TRUE, META)]
        quads += [ox.Quad(N(f.subject), MEMBER_OF, N(cls), META) for f in members]
        self.store.extend(quads)
        return members

    def local_label(self, iri: str) -> str | None:
        found = self._meta(iri, LABEL)
        return found[0].value if found else None

    def labels(self, ids: set[str], fetch) -> dict[str, str]:
        """Labels of bare Wikidata ids, cached like everything else;
        `fetch` resolves the ones not seen yet."""
        wd = lambda i: N(("http://www.wikidata.org/entity/" if i.startswith("Q") else
                          "http://www.wikidata.org/prop/direct/") + i)
        out, todo = {}, set()
        for i in ids:
            found = self._meta(wd(i).value, LABEL)
            if found:
                out[i] = found[0].value
            else:
                todo.add(i)
        if todo:
            fetched = fetch(todo)
            self.store.extend([ox.Quad(wd(i), LABEL, ox.Literal(l), META) for i, l in fetched.items()])
            out.update(fetched)
        return out

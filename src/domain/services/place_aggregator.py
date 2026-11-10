"""PlaceAggregator: one candidate per physical place. Two candidates
merge when they share coordinates (~1 m), resolve to the same venue, or
are the same place in the Zurich Tourism catalogue (a Wikidata item and
its catalogue twin) -- e.g. fifteen tournament editions and the arena
they were held in become that arena, even though an event's own
coordinate can be a few metres off the venue's (a real Wikidata data
gap, not a rounding issue). A pure Domain Service."""

from __future__ import annotations

from collections import Counter

from domain.models.poi.candidate import Candidate, Tier
from domain.models.poi.poi import PointOfInterest
from domain.models.poi.reference import Reference, local_name
from domain.models.poi.venue import Venue

COORDINATE_DECIMALS = 5


class _UnionFind:
    def __init__(self, items):
        self._parent = {x: x for x in items}

    def find(self, x):
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


class PlaceAggregator:
    def aggregate(
        self, candidates: list[Candidate], venues: dict[str, Venue], same_as: dict[str, str] | None = None,
    ) -> list[Candidate]:
        """`venues` maps a candidate's uri to the venue it is located at,
        for those that have one. The merged place is named, in order of
        preference, by a member that is itself that venue, else the venue
        most members point to, else a member that has a catalogue twin,
        else the first member. `same_as` maps a candidate's uri to the
        catalogue uri of the place it is."""
        same_as = same_as or {}
        uf = _UnionFind(c.poi.uri for c in candidates)
        by_coordinate: dict[tuple[float, float], str] = {}
        by_venue: dict[str, str] = {}
        by_entity: dict[str, str] = {}
        for c in candidates:
            coord_key = (round(c.poi.coordinates.lat, COORDINATE_DECIMALS), round(c.poi.coordinates.lon, COORDINATE_DECIMALS))
            if coord_key in by_coordinate:
                uf.union(c.poi.uri, by_coordinate[coord_key])
            by_coordinate[coord_key] = c.poi.uri

            venue_uri = venues[c.poi.uri].uri if c.poi.uri in venues else c.poi.uri
            if venue_uri in by_venue:
                uf.union(c.poi.uri, by_venue[venue_uri])
            by_venue[venue_uri] = c.poi.uri

            entity_uri = same_as.get(c.poi.uri, c.poi.uri)
            if entity_uri in by_entity:
                uf.union(c.poi.uri, by_entity[entity_uri])
            by_entity[entity_uri] = c.poi.uri

        groups: dict[str, list[Candidate]] = {}
        for c in candidates:
            groups.setdefault(uf.find(c.poi.uri), []).append(c)

        merged = [members[0] if len(members) == 1 else self._merge(members, venues, same_as) for members in groups.values()]
        merged.sort(key=lambda c: (c.tier != Tier.PRIMARY, c.poi.uri))
        return merged

    def _merge(self, members: list[Candidate], venues: dict[str, Venue], same_as: dict[str, str]) -> Candidate:
        venue_uris = [venues[m.poi.uri].uri for m in members if m.poi.uri in venues]
        member_by_uri = {m.poi.uri: m for m in members}
        wanted = next((u for u in venue_uris if u in member_by_uri), None)
        if wanted is None and venue_uris:
            wanted = Counter(venue_uris).most_common(1)[0][0]

        if wanted in member_by_uri:
            poi = member_by_uri[wanted].poi
        elif wanted is not None:
            venue = next(v for m in members if (v := venues.get(m.poi.uri)) and v.uri == wanted)
            poi = PointOfInterest(
                uri=venue.uri, label=venue.label, coordinates=members[0].poi.coordinates,
                description=venue.description, images=venue.images,
            )
        else:
            twinned = next((m for m in members if same_as.get(m.poi.uri, m.poi.uri) != m.poi.uri), members[0])
            poi = twinned.poi

        absorbed = [m for m in members if m.poi.uri != poi.uri]
        return Candidate(
            poi=poi,
            explanations=[e for m in members for e in m.explanations],
            tier=Tier.PRIMARY if any(m.tier == Tier.PRIMARY for m in members) else Tier.SECONDARY,
            includes=self._includes(poi.uri, absorbed, members),
        )

    @staticmethod
    def _includes(place_uri: str, absorbed: list[Candidate], members: list[Candidate]) -> list[Reference]:
        """Every candidate folded into this place -- the members merged now
        plus whatever each member had already absorbed earlier."""
        seen: dict[str, Reference] = {}
        for m in absorbed:
            seen.setdefault(local_name(m.poi.uri), Reference(id=local_name(m.poi.uri), label=m.poi.label))
        for m in members:
            for ref in m.includes:
                seen.setdefault(ref.id, ref)
        seen.pop(local_name(place_uri), None)
        return list(seen.values())

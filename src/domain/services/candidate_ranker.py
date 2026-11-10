"""CandidateRanker: Sect. 4.3 of the paper, stops, their value and their explanation.

A POI reached by admissible paths (a candidate) is not always a place to visit. A *stop* y is a POI with a
geometry of its own that does not stand on or within another; it gathers the POIs at it: itself, those located
through it (a work held in a museum, an event staged in a theatre) and those that stand on or within it (the
plaque on a house, the grave in its cemetery). The paths of y, P_y, are the paths to the POIs it gathers,
except those that pass through y. Its value is a noisy-or over the seeds of the user of the best path from
each, a path from a derived seed counting as one from the seed it derives from:

    S_u(y) = 1 - prod_i (1 - max_{pi in P_y from i} s_u(pi)).

Its explanation is the best path of each class of equivalent paths: two paths from the same seed are
equivalent when they pass through the same nodes by the same predicates, a predicate and its inverse
(doctoral advisor, doctoral student) counting as one."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import asin, cos, radians, sin, sqrt

from domain.models.retrieval.path import RetrievalPath
from domain.repositories.retrieval.fact_source import Location


@dataclass
class RankedCandidate:
    place: str
    score: float
    per_seed: dict[str, float]            # value for each seed of the user (derived seeds counted with theirs)
    best_paths: dict[str, RetrievalPath]  # best path from each seed of the user
    location: Location
    path_count: int
    visit_min: float | None = None
    accessible: bool | None = None
    reasons: list["RankedCandidate"] = field(default_factory=list)   # kept for compatibility: always empty
    paths: dict[str, list[RetrievalPath]] = field(default_factory=dict)  # the explanation, per seed, best first
    gathered: list[str] = field(default_factory=list)                # the POIs this stop gathers, itself included

    def dated_interval(self):
        bounds = [b for path in self.best_paths.values() for f in path.facts
                  for b in (f.validity.start, f.validity.end) if b is not None]
        return min(bounds) if bounds else None


def _key(path: RetrievalPath, inverse: dict[str, str]):
    """Equivalent paths share this key: the same nodes, in order, by the same predicates, a predicate and its
    inverse being one."""
    preds = tuple(min(f.predicate, inverse.get(f.predicate, f.predicate)) for f in path.facts)
    return (path.nodes, preds)


def distinct(paths, inverse: dict[str, str] | None = None) -> list[RetrievalPath]:
    """The best path of each class of equivalent paths, best first."""
    best: dict = {}
    for p in paths:
        k = _key(p, inverse or {})
        if k not in best or p.value > best[k].value:
            best[k] = p
    return sorted(best.values(), key=lambda p: -p.value)


def _metres(a: Location, b: Location) -> float:
    dlat, dlon = radians(b.lat - a.lat), radians(b.lon - a.lon)
    h = sin(dlat / 2) ** 2 + cos(radians(a.lat)) * cos(radians(b.lat)) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


class CandidateRanker:
    def __init__(self, merge_m: float = 15.0, min_score: float = 0.0):
        self.merge_m = merge_m        # two POIs with their own geometry this close are one object recorded twice
        self.min_score = min_score    # stops below this value are left out

    def rank(self, candidates: dict[str, list[RetrievalPath]], locations: dict[str, Location],
             origin: dict[str, str] | None = None, within: dict[str, str] | None = None,
             inverse: dict[str, str] | None = None) -> list[RankedCandidate]:
        """candidates: the paths found to each POI; origin: the seed each seed counts as (a derived seed, the
        seed it derives from); within: for POIs with a geometry of their own, the place each stands on or
        within; inverse: the inverse of each predicate, where one is stated."""
        origin, within, inverse = origin or {}, within or {}, inverse or {}
        host = self._hosts(candidates, locations, within)
        groups: dict[str, list[str]] = {}
        for poi in candidates:
            groups.setdefault(host[poi], []).append(poi)
        stops = []
        for y, pois in groups.items():
            paths: dict[str, list[RetrievalPath]] = {}
            for poi in pois:
                for p in candidates[poi]:
                    if y in p.nodes[:-1]:
                        continue                       # a path through the stop adds nothing to it
                    paths.setdefault(origin.get(p.seed, p.seed), []).append(p)
            if not paths:
                continue
            best = {s: max(ps, key=lambda p: p.value) for s, ps in paths.items()}
            per_seed = {s: p.value for s, p in best.items()}
            miss = 1.0
            for v in per_seed.values():
                miss *= 1.0 - v
            loc = locations.get(y) or next(locations[p] for p in pois if p in locations)
            if loc.inherited and y not in locations:
                loc = Location(loc.lat, loc.lon, None, loc.existence)
            stop = RankedCandidate(y, 1.0 - miss, per_seed, best, loc, sum(len(candidates[p]) for p in pois),
                                   paths={s: distinct(ps, inverse) for s, ps in paths.items()}, gathered=sorted(pois))
            if stop.score >= self.min_score:
                stops.append(stop)
        stops.sort(key=lambda c: (-c.score, c.place))
        return stops

    def _hosts(self, candidates, locations, within) -> dict[str, str]:
        """The stop that gathers each POI: the container it is located through; else the place it stands on or
        within (followed up to the outermost); else itself, or the first of two records of one object."""
        host: dict[str, str] = {}
        own = [p for p in candidates if p in locations and not locations[p].inherited]
        for poi in candidates:
            loc = locations.get(poi)
            h = loc.via if loc is not None and loc.inherited else poi
            seen = {h}
            while h in within and within[h] not in seen:     # up to the outermost place it stands within
                h = within[h]
                seen.add(h)
            host[poi] = h
        # two POIs with their own geometry within merge_m: one object recorded twice, one stop
        by_place = sorted(own, key=lambda p: p)
        for i, a in enumerate(by_place):
            for b in by_place[:i]:
                if host[b] == b and host[a] == a and _metres(locations[a], locations[b]) <= self.merge_m:
                    for poi, h in host.items():
                        if h == a:
                            host[poi] = b
                    break
        # a host located through another place is itself gathered there
        for poi, h in list(host.items()):
            hl = locations.get(h)
            if hl is not None and hl.inherited:
                host[poi] = hl.via
        return host

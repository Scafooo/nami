"""TourComposer: Sec. 4.4 of the paper. The value of a tour lifts the
candidate value to the whole tour, per interest,

    V_u(T) = sum_e (1 - prod_{p in T} (1 - s_{u,e}(p))),

which is monotone and submodular (s_{u,e} already carries the seed
weight w_e, so it is not applied twice). Stops are chosen greedily by gain in
V_u per minute added (visit plus detour, by cheapest insertion) until the
time budget is spent -- one tour per spatial cluster of candidates, the
best few offered as alternatives. Stops are ordered spatially, or
chronologically when that costs little extra travel."""

from __future__ import annotations

import math
from datetime import date
from dataclasses import dataclass, field

from domain.models.user.profile import UserProfile
from domain.services.candidate_ranker import RankedCandidate


@dataclass
class TourParams:
    budget_min: float = 240.0
    visit_min: float = 30.0          # default visit duration when a place states none
    memorial_min: float = 5.0        # a place that no longer exists: a stop at the site
    walk_kmh: float = 4.5
    detour_factor: float = 1.3
    cluster_km: float = 3.0
    chrono_slack: float = 0.2
    max_alternatives: int = 3
    min_alternative_ratio: float = 0.3   # an alternative must be worth this share of the best tour
    min_gain: float = 0.0                # a stop must add at least this much tour value
    # A stop must be worth visiting on its own. The tour value saturates per interest (noisy-or),
    # so a threshold on the marginal gain ended single-interest tours after one strong stop.
    min_stop_value: float = 0.2


@dataclass
class ComposedTour:
    stops: list[RankedCandidate]
    value: float
    order: str                      # "spatial" | "chronological"
    travel_min: float
    total_min: float
    transitions: list[list[str]] = field(default_factory=list)  # shared nodes between consecutive stops


def haversine_km(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


class TourComposer:
    def __init__(self, params: TourParams | None = None):
        self.p = params or TourParams()

    def _minutes(self, a, b) -> float:
        return haversine_km(a, b) * self.p.detour_factor / self.p.walk_kmh * 60.0

    def visit(self, c: RankedCandidate) -> float:
        end = c.location.existence.end
        if end is not None and end <= date.today():
            return self.p.memorial_min
        return c.visit_min if c.visit_min is not None else self.p.visit_min

    def _travel(self, route: list[RankedCandidate], start=None) -> float:
        points = ([start] if start else []) + [c.location for c in route]
        return sum(self._minutes(x, y) for x, y in zip(points, points[1:]))

    @staticmethod
    def value(stops: list[RankedCandidate], profile: UserProfile) -> float:
        total = 0.0
        for seed in profile.seeds:
            miss = 1.0
            for c in stops:
                miss *= 1.0 - c.per_seed.get(seed.iri, 0.0)
            total += 1.0 - miss
        return total

    def clusters(self, candidates: list[RankedCandidate]) -> list[list[RankedCandidate]]:
        parent = list(range(len(candidates)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, j in ((i, j) for i in range(len(candidates)) for j in range(i + 1, len(candidates))):
            if haversine_km(candidates[i].location, candidates[j].location) <= self.p.cluster_km:
                parent[find(i)] = find(j)
        groups: dict[int, list[RankedCandidate]] = {}
        for i, c in enumerate(candidates):
            groups.setdefault(find(i), []).append(c)
        return list(groups.values())

    def _greedy(self, pool: list[RankedCandidate], profile: UserProfile, start=None) -> list[RankedCandidate]:
        route: list[RankedCandidate] = []
        current_value = 0.0
        remaining = list(pool)
        while remaining:
            best = None
            for c in remaining:
                if c.score < self.p.min_stop_value:
                    continue
                gain = self.value(route + [c], profile) - current_value
                if gain < self.p.min_gain:
                    continue
                pos, extra = self._cheapest_insertion(route, c, start)
                trial = route[:pos] + [c] + route[pos:]
                total = self._travel(trial, start) + sum(self.visit(x) for x in trial)
                if total > self.p.budget_min:
                    continue
                ratio = gain / (extra + self.visit(c))
                if best is None or ratio > best[0]:
                    best = (ratio, c, pos, gain)
            if best is None:
                break
            _, c, pos, gain = best
            route.insert(pos, c)
            current_value += gain
            remaining.remove(c)
        return route

    def _cheapest_insertion(self, route, c, start) -> tuple[int, float]:
        base = self._travel(route, start)
        best = (len(route), float("inf"))
        for pos in range(len(route) + 1):
            extra = self._travel(route[:pos] + [c] + route[pos:], start) - base
            if extra < best[1]:
                best = (pos, extra)
        return best

    @staticmethod
    def _shared(a: RankedCandidate, b: RankedCandidate) -> list[str]:
        """Nodes, other than seeds and the two places themselves, that lie
        on the best paths of both stops."""
        seeds = set(a.best_paths) | set(b.best_paths)
        na = {n for p in a.best_paths.values() for n in p.nodes}
        nb = {n for p in b.best_paths.values() for n in p.nodes}
        return sorted((na & nb) - seeds - {a.place, b.place})

    def compose(self, candidates: list[RankedCandidate], profile: UserProfile, start=None) -> list[ComposedTour]:
        if any(r.endswith("WheelchairAccess") for r in profile.requires):
            candidates = [c for c in candidates if c.accessible is not False]
        tours = []
        for cluster in self.clusters(candidates):
            route = self._greedy(cluster, profile, start)
            if not route:
                continue
            order = "spatial"
            spatial_travel = self._travel(route, start)
            if len(route) > 1 and all(c.dated_interval() is not None for c in route):
                chrono = sorted(route, key=lambda c: c.dated_interval())
                chrono_travel = self._travel(chrono, start)
                if chrono != route and chrono_travel <= (1 + self.p.chrono_slack) * spatial_travel + 1e-9:
                    route, order = chrono, "chronological"
            travel = self._travel(route, start)
            tours.append(ComposedTour(
                stops=route, value=self.value(route, profile), order=order, travel_min=travel,
                total_min=travel + sum(self.visit(c) for c in route),
                transitions=[self._shared(a, b) for a, b in zip(route, route[1:])],
            ))
        tours.sort(key=lambda t: -t.value)
        if tours:
            tours = [t for t in tours if t.value >= self.p.min_alternative_ratio * tours[0].value]
        return tours[: self.p.max_alternatives]

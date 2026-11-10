"""BestFirstRetriever: Algorithm 1 of the paper. Expands the most valuable
partial paths first, B distinct end nodes per round, and prunes any path
whose value falls below tau. Since no extension of a path is worth more
than the path itself, pruning loses no path worth at least tau; the
optional expansion budget trades that guarantee for bounded cost, and
the result reports whether it was hit."""

from __future__ import annotations

import heapq
from collections import Counter
import itertools
import logging
from dataclasses import dataclass, field

from domain.models.retrieval.path import RetrievalPath
from domain.models.user.profile import Seed, UserProfile
from domain.repositories.retrieval.fact_source import FactSource, Location
from domain.services.path_valuation import PathValuation, ValuationParams

logger = logging.getLogger(__name__)
HUMAN = "http://www.wikidata.org/entity/Q5"


@dataclass
class SearchParams:
    beam: int = 50                     # B: distinct end nodes expanded per round
    max_expansions: int | None = 400   # budget on expanded nodes; None = exhaustive above tau
    topical_weight: float = 0.8        # weight of a concept an interest stands for, relative to it


@dataclass
class RetrievalResult:
    candidates: dict[str, list[RetrievalPath]]
    locations: dict[str, Location]
    stats: dict = field(default_factory=dict)


class BestFirstRetriever:
    def __init__(self, source: FactSource, valuation: ValuationParams, search: SearchParams | None = None):
        self.source = source
        self.valuation_params = valuation
        self.search = search or SearchParams()

    def retrieve(self, profile: UserProfile) -> RetrievalResult:
        val = PathValuation(self.valuation_params, profile)
        tau = self.valuation_params.tau
        counter = itertools.count()
        frontier: list[tuple[float, int, RetrievalPath]] = []
        candidates: dict[str, list[RetrievalPath]] = {}
        locations: dict[str, Location] = {}
        located_checked: set[str] = set()
        excluded_nodes: set[str] = set()
        expanded: set[str] = set()
        degrees: dict[str, int] = {}
        fact_cache: dict[str, list] = {}
        stats = {"rounds": 0, "expanded_nodes": 0, "paths_generated": 0, "pruned_below_tau": 0,
                 "pruned_eta": 0, "budget_exhausted": False}

        def push(path: RetrievalPath) -> None:
            heapq.heappush(frontier, (-path.value, next(counter), path))

        def register(paths: list[RetrievalPath]) -> None:
            """Locates the end nodes of `paths` not seen yet and records
            every path ending at a located, non-excluded node."""
            new_nodes = {p.end for p in paths} - located_checked
            if new_nodes:
                located_checked.update(new_nodes)
                found = self.source.locate(new_nodes)
                if found and profile.not_interested_in:
                    excluded_nodes.update(self.source.instances_of(set(found), profile.not_interested_in))
                locations.update({n: loc for n, loc in found.items() if n not in excluded_nodes})
            for p in paths:
                if p.end in locations and p.length > 0:
                    candidates.setdefault(p.end, []).append(val.at_candidate(p, locations[p.end]))

        # An interest in a theme is also one in the concepts it stands for (LGBTQ history -> LGBTQ),
        # with a lower weight; theme and concepts reach the places of the area that instantiate them
        # or refer to them. An entity such as a person stands for no concept: nothing changes for it.
        concepts: set[str] = set()
        for seed in list(profile.seeds):
            topics = self.source.topical(seed.iri)
            if topics:
                concepts.add(seed.iri)
            for c in topics:
                if all(s.iri != c for s in profile.seeds):
                    profile.seeds.append(Seed(c, weight=seed.weight * self.search.topical_weight, source=seed.iri,
                                              link_confidence=seed.link_confidence, period=seed.period))
                    concepts.add(c)
        seeds = {s.iri for s in profile.seeds}
        # Any interest that is not a person is a concept too: places refer to it, in data or in text.
        concepts |= seeds - self.source.instances_of(seeds, {HUMAN})
        for seed in profile.seeds:
            start = val.start(seed)
            push(start)
            # Class seeds: instances are reached backwards through rdf:type,
            # restricted to the area -- a class can have millions of them.
            members = []
            facts = self.source.class_members(seed.iri)
            if seed.iri in concepts:
                facts = facts + self.source.area_members(seed.iri)
            for fact in facts:
                if val.admissible(start, fact):
                    path = val.extend(start, fact)
                    if path.value >= tau:
                        members.append(path)
            stats["paths_generated"] += len(members)
            register(members)
        if seeds:
            register([val.start(s) for s in profile.seeds])  # a seed can itself be a place

        while frontier:
            if self.search.max_expansions is not None and len(expanded) >= self.search.max_expansions:
                stats["budget_exhausted"] = True
                break
            # Pop the best paths until B distinct, not yet expanded end nodes are covered.
            batch: list[RetrievalPath] = []
            nodes: set[str] = set()
            while frontier and len(nodes) < self.search.beam:
                _, _, path = heapq.heappop(frontier)
                batch.append(path)
                nodes.add(path.end)
            # Paths ending at the same nodes, still in the frontier, join this round.
            same = [e for e in frontier if e[2].end in nodes]
            if same:
                frontier[:] = [e for e in frontier if e[2].end not in nodes]
                heapq.heapify(frontier)
                batch.extend(e[2] for e in same)
            stats["rounds"] += 1

            unknown = {n for n in nodes if n not in degrees and n not in seeds}
            if unknown:
                degrees.update(self.source.degree(unknown))
            to_filter = nodes - seeds - excluded_nodes
            if profile.not_interested_in and to_filter:
                excluded_nodes.update(self.source.instances_of(to_filter, profile.not_interested_in))

            live: list[RetrievalPath] = []
            for path in batch:
                if path.end in excluded_nodes:
                    continue
                if path.end not in seeds:
                    eta = val.eta(degrees.get(path.end))
                    # No extension is worth more than value * eta * lambda: every
                    # other factor of the next hop is at most 1.
                    if path.value * eta * self.valuation_params.lam < tau:
                        stats["pruned_eta"] += 1
                        continue
                    path = RetrievalPath(path.seed, path.nodes, path.facts, path.value * eta)
                live.append(path)

            to_expand = {p.end for p in live} - expanded
            facts_by_node = self.source.expand(to_expand) if to_expand else {}
            expanded.update(to_expand)
            stats["expanded_nodes"] = len(expanded)
            fact_cache.update(facts_by_node)

            new_paths: list[RetrievalPath] = []
            fanouts: dict[str, Counter] = {}
            for path in live:
                x = path.end
                if x not in fanouts:
                    fanouts[x] = Counter((f.predicate, f.subject == x) for f in fact_cache.get(x, []))
                for fact in fact_cache.get(x, []):
                    if not val.admissible(path, fact):
                        continue
                    nxt = val.extend(path, fact, fanouts[x][(fact.predicate, fact.subject == x)])
                    if nxt.value < tau:
                        stats["pruned_below_tau"] += 1
                        continue
                    new_paths.append(nxt)
            stats["paths_generated"] += len(new_paths)
            register(new_paths)
            for p in new_paths:
                if p.end not in excluded_nodes:
                    push(p)
            logger.info("round %d: expanded %d node(s), %d new path(s), frontier %d, candidates %d",
                        stats["rounds"], len(to_expand), len(new_paths), len(frontier), len(candidates))

        stats["candidates"] = len(candidates)
        # The best value still waiting per seed bounds every path the search did not reach (no extension
        # is worth more than its prefix): with it, the ranking can be certified or its error bounded.
        frontier_max: dict[str, float] = {}
        for _, _, path in frontier:
            frontier_max[path.seed] = max(frontier_max.get(path.seed, 0.0), path.value)
        stats["frontier_max"] = frontier_max
        return RetrievalResult(candidates=candidates, locations=locations, stats=stats)

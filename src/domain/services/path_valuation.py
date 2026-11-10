"""PathValuation: the value of facts and paths (paper, Sec. 4.2).

    sigma_u(t)  = c(t) * trust(src(t)) * omega_u(r)          fact value
    eta(n)      = min(1, sqrt(d0 / deg(n)))                  specificity of an intermediate node
    eta_p(n, p) = min(1, sqrt(d_pred / fanout_p(n)))         specificity of leaving n through p
    s_u(pi)     = w_e * l_e * lambda^(h-1) * prod sigma * prod eta * prod eta_p * gamma^m

Every factor is at most 1, so extending a path never increases its value
-- the property best-first pruning relies on. eta is applied lazily, when
a path is about to continue through a node (see BestFirstRetriever): it
discounts passing *through* a hub, never arriving at one."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from domain.models.retrieval.fact import Fact
from domain.models.retrieval.path import RetrievalPath
from domain.models.user.profile import Seed, UserProfile


@dataclass
class ValuationParams:
    theta: float = 0.5          # minimum confidence of a fact
    lam: float = 0.5            # discount per hop beyond the first
    tau: float = 0.15           # pruning threshold on path value
    gamma: float = 0.7          # penalty for temporally incoherent consecutive facts
    d0: int = 2000              # reference degree: nodes up to d0 count as fully specific
    d_pred: int = 10            # reference fan-out of a node through one predicate
    relation_weights: dict[str, float] = field(default_factory=dict)  # type-level default weights
    source_trust: dict[str, float] = field(default_factory=dict)      # type-level trust per source
    source_trust_prefixes: dict[str, float] = field(default_factory=dict)  # trust for a family of sources
    default_trust: float = 1.0
    excluded_relations: frozenset[str] = frozenset()                  # R_X
    role_weights: dict[str, float] = field(default_factory=dict)      # type-level weight per role
    phi_inherited: float = 0.8   # a candidate located only through a containing node
    unannotated_confidence: float = 1.0   # confidence of a fact that states none
    ignore_provenance: bool = False       # ablation: every fact has confidence 1 and every source trust 1
    # Classification: followed only from a class seed to its instances, never
    # through a class ("Einstein -> human -> ..." relates nothing).
    type_relations: frozenset[str] = frozenset({
        "type", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
        "http://www.wikidata.org/prop/direct/P31", "http://www.wikidata.org/prop/direct/P279"})


class PathValuation:
    def __init__(self, params: ValuationParams, profile: UserProfile):
        self.p = params
        self.profile = profile

    def omega(self, relation: str) -> float:
        if relation in self.profile.preferred_relations:
            return 1.0
        return self.p.relation_weights.get(relation, 1.0)

    def trust(self, source: str | None) -> float:
        if source is None:
            return self.p.default_trust
        if source in self.p.source_trust:
            return self.p.source_trust[source]
        for prefix, value in self.p.source_trust_prefixes.items():
            if source.startswith(prefix):
                return value
        return self.p.default_trust

    def rho(self, role: str | None) -> float:
        return 1.0 if role is None else self.p.role_weights.get(role, 1.0)

    def sigma(self, fact: Fact) -> float:
        if self.p.ignore_provenance:
            return self.omega(fact.predicate) * self.rho(fact.role)
        c = self.p.unannotated_confidence if fact.confidence is None else fact.confidence
        return c * self.trust(fact.source) * self.omega(fact.predicate) * self.rho(fact.role)

    def at_candidate(self, path: RetrievalPath, location) -> RetrievalPath:
        """The value of `path` as evidence for the place it ends at: phi
        discounts a location inherited from a containing node, and a last
        fact that does not overlap the existence of the place counts as one
        more temporal mismatch. Neither affects paths that continue further."""
        v = path.value
        if location.inherited:
            v *= self.p.phi_inherited
        last = path.facts[-1] if path.facts else None
        if last is not None and last.validity.known and location.existence.known \
                and not last.validity.overlaps(location.existence):
            v *= self.p.gamma
        return RetrievalPath(path.seed, path.nodes, path.facts, v)

    def eta(self, degree: int | None) -> float:
        if not degree or degree <= self.p.d0:
            return 1.0
        return math.sqrt(self.p.d0 / degree)

    def eta_pred(self, fanout: int | None) -> float:
        """Specificity of continuing from a node through one predicate, in
        one direction: 1 up to d_pred neighbours of that kind, then the
        square root of d_pred over their number. Einstein -> archives at ->
        ETH Archives <- archives at <- (hundreds of fonds) is a weak link;
        Einstein -> spouse -> Mileva Maric -> place of burial -> (one
        cemetery) is not."""
        if not fanout or fanout <= self.p.d_pred:
            return 1.0
        return math.sqrt(self.p.d_pred / fanout)

    def kappa(self, previous: Fact | None, fact: Fact, seed: Seed) -> float:
        k = 1.0
        if previous is not None and previous.validity.known and fact.validity.known:
            if not previous.validity.overlaps(fact.validity):
                k *= self.p.gamma
        if seed.period.known and fact.validity.known and not fact.validity.overlaps(seed.period):
            k *= self.p.gamma
        return k

    def admissible(self, path: RetrievalPath, fact: Fact) -> bool:
        if fact.predicate in self.p.excluded_relations:
            return False
        if not self.p.ignore_provenance and fact.confidence is not None and fact.confidence < self.p.theta:
            return False
        nxt = fact.other_end(path.end)
        if path.visits(nxt) or nxt in self.profile.not_interested_in:
            return False
        if fact.predicate in self.p.type_relations and not (
                path.length == 0 and fact.object == path.seed and fact.subject == nxt):
            return False
        if nxt != path.seed and any(s.iri == nxt for s in self.profile.seeds):
            return False    # another interest: its own paths account for it
        if self.profile.max_depth is not None and path.length + 1 > self.profile.max_depth:
            return False
        return True

    def start(self, seed: Seed) -> RetrievalPath:
        return RetrievalPath(seed.iri, (seed.iri,), (), seed.weight * seed.link_confidence)

    def extend(self, path: RetrievalPath, fact: Fact, fanout: int | None = None) -> RetrievalPath:
        """`fanout`: how many facts leave the current end node through the
        same predicate in the same direction (only used past the seed)."""
        seed = self.profile.seed(path.seed)
        previous = path.facts[-1] if path.facts else None
        hop = self.p.lam * self.eta_pred(fanout) if path.length >= 1 else 1.0
        return path.extend(fact, path.value * hop * self.sigma(fact) * self.kappa(previous, fact, seed))

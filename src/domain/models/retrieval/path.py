"""RetrievalPath: a path from a seed through facts of G_P, with its value
s_u(pi) maintained incrementally as it grows (see PathValuation).
Immutable: extending a path returns a new one, so paths that share a
prefix share it safely in the best-first frontier."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.retrieval.fact import Fact


@dataclass(frozen=True)
class RetrievalPath:
    seed: str
    nodes: tuple[str, ...]
    facts: tuple[Fact, ...]
    value: float

    @property
    def end(self) -> str:
        return self.nodes[-1]

    @property
    def length(self) -> int:
        return len(self.facts)

    def visits(self, node: str) -> bool:
        return node in self.nodes

    def extend(self, fact: Fact, value: float) -> "RetrievalPath":
        return RetrievalPath(self.seed, self.nodes + (fact.other_end(self.end),), self.facts + (fact,), value)

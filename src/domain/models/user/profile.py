"""UserProfile: the statements of G_U retrieval uses, grouped as in the
paper -- seeds, filters, weights, feedback, settings. Built from a
Turtle 1.2 file with RDF-star annotations on :interestedIn."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.retrieval.fact import Interval


@dataclass(frozen=True)
class Seed:
    iri: str
    weight: float = 1.0
    source: str | None = None
    link_confidence: float = 1.0
    period: Interval = Interval()


@dataclass
class UserProfile:
    uri: str
    seeds: list[Seed]
    not_interested_in: set[str] = field(default_factory=set)   # entities or classes
    preferred_relations: set[str] = field(default_factory=set)
    max_depth: int | None = None
    language: str = "en"
    requires: set[str] = field(default_factory=set)            # stable constraints, e.g. wheelchair access
    accepted: list[tuple[str, str | None]] = field(default_factory=list)  # (place, via seed)
    rejected: list[tuple[str, str | None]] = field(default_factory=list)

    def seed(self, iri: str) -> Seed:
        return next(s for s in self.seeds if s.iri == iri)

"""Candidate: a PointOfInterest reached from the interests, with the
Explanation(s) that justify it and the Tier it was classified into.
Explanations carry pre-rendered text and labelled references, not a
Path/Hop graph -- label resolution happens once, in infrastructure,
before a Candidate exists. includes names the other candidates merged
into this one because they share its place (see PlaceAggregator)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from domain.models.poi.explanation import Explanation
from domain.models.poi.poi import PointOfInterest
from domain.models.poi.reference import Reference


class Tier(str, Enum):
    """PRIMARY if at least one explanation is specific to the interest,
    SECONDARY if every one is merely structural. A str mixin so an
    f-string renders the plain value, not the qualified enum name."""

    PRIMARY = "primary"
    SECONDARY = "secondary"


@dataclass
class Candidate:
    poi: PointOfInterest
    explanations: list[Explanation]
    tier: Tier
    includes: list[Reference] = field(default_factory=list)

    @property
    def reached_from(self) -> list[Reference]:
        """The distinct interests this candidate was reached from, in
        the order its explanations first name them."""
        seen: dict[str, Reference] = {}
        for explanation in self.explanations:
            if explanation.interest is not None:
                seen.setdefault(explanation.interest.id, explanation.interest)
        return list(seen.values())

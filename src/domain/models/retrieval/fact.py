"""Fact: one statement of G_P as retrieval sees it -- the base triple plus
its RDF-star annotations (confidence, source, validity interval, role, and
for a fact read from text the sentence it comes from). A
missing annotation is None, and every consumer treats None as "not
stated" rather than as a value."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Interval:
    """A validity interval, open on either side when that bound is
    unknown. Compared at day granularity."""

    start: date | None = None
    end: date | None = None

    def overlaps(self, other: "Interval") -> bool:
        if self.start and other.end and self.start > other.end:
            return False
        if other.start and self.end and other.start > self.end:
            return False
        return True

    @property
    def known(self) -> bool:
        return self.start is not None or self.end is not None


@dataclass(frozen=True)
class Fact:
    subject: str
    predicate: str
    object: str
    confidence: float | None = None
    source: str | None = None
    validity: Interval = Interval()
    role: str | None = None        # the role the subject or object plays, e.g. student or professor
    evidence: str | None = None    # the sentence a text-extracted fact was read from

    def other_end(self, node: str) -> str:
        return self.object if node == self.subject else self.subject

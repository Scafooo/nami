"""Explanation: why a POI was reached from one interest -- the readable
text plus the same chain as labelled steps."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.reference import Reference


@dataclass
class ExplanationStep:
    source: Reference
    predicate: Reference
    target: Reference
    note: str | None = None


@dataclass
class Explanation:
    text: str
    interest: Reference | None = None
    steps: list[ExplanationStep] = field(default_factory=list)

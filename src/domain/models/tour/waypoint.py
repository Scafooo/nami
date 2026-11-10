"""Waypoint: one PointOfInterest in the context of a Tour, with the
explanation(s) that justify including it -- kept off PointOfInterest
itself, which stays a plain fact regardless of which tour it's on."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.poi import PointOfInterest


@dataclass
class Waypoint:
    poi: PointOfInterest
    explanations: list[str] = field(default_factory=list)

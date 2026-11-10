"""Tour: the set of Waypoint recommended for one User. A Value Object,
not an Entity, and no id -- two tours with the same stops are the same
tour. user_uri references the owner by identity, not a live User
object. interests/k are kept as provenance: how this tour came to be.
No walking order or distance/duration: stops are not currently
sequenced into a route."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models.tour.waypoint import Waypoint


@dataclass
class Tour:
    user_uri: str
    stops: list[Waypoint]
    interests: list[str]
    k: int
    generated_at: datetime

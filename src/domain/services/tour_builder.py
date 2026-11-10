"""TourBuilder: turns a tier-sorted list of Candidate into a Tour. A
pure Domain Service -- operates only on domain models, nothing
external. No walking-route ordering: stops keep the order they arrive
in (tier-sorted, primary first).

k (the search depth BuildQuery/ToQuery used) and max_stops (how many of
the resulting candidates go into the tour) are different numbers --
k=2 can turn up far more than 2 candidates. max_stops defaults to no
cap at all: every candidate goes into the tour, unfiltered, until a
cap is explicitly asked for."""

from __future__ import annotations

from datetime import datetime, timezone

from domain.models.poi.candidate import Candidate
from domain.models.tour.tour import Tour
from domain.models.user.user import User
from domain.models.tour.waypoint import Waypoint


class TourBuilder:
    def build(
        self,
        candidates: list[Candidate],
        user: User,
        interests: list[str],
        k: int,
        max_stops: int | None = None,
        generated_at: datetime | None = None,
    ) -> Tour:
        selected = candidates[:max_stops] if max_stops is not None else candidates
        stops = [Waypoint(poi=c.poi, explanations=[e.text for e in c.explanations]) for c in selected]
        return Tour(
            user_uri=user.uri,
            stops=stops,
            interests=interests,
            k=k,
            generated_at=generated_at or datetime.now(timezone.utc),
        )

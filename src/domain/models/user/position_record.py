"""PositionRecord: one timestamped observation of where a User was --
matches the ontology's own nami:PositionRecord. A user accumulates many;
"current position" is the one with the latest `at`, never overwritten."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from domain.models.coordinates.coordinates import Coordinates


@dataclass
class PositionRecord:
    coordinates: Coordinates
    at: datetime

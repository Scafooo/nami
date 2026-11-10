"""One POI as the map report needs it -- a read-model projection, not
PointOfInterest itself: category/locality are report-only groupings that
have no place on the tour-recommendation entity."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MapPOI:
    uri: str
    name: str
    category: str
    lat: float
    lon: float
    locality: str

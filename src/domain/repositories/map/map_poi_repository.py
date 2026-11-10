"""MapPOIRepository: the interface for fetching every POI in the store
that has geometry, for reporting -- unscoped, unlike POIRepository which
retrieves candidates scoped to a user's interests."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.models.poi.map_poi import MapPOI


@dataclass
class MapPOIRepository(ABC):
    @abstractmethod
    def find_with_geometry(self, city_only: bool = True) -> list[MapPOI]: ...

"""GenerateMapUseCase: coordinates fetching every mapped POI and handing
them to a renderer -- no calculation rule of its own."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from application.services.map_renderer import MapRenderer
from domain.repositories.map.map_poi_repository import MapPOIRepository


@dataclass
class GenerateMapUseCase:
    map_poi_repository: MapPOIRepository
    renderer: MapRenderer

    def execute(self, out_file: Path, city_only: bool = True) -> int:
        pois = self.map_poi_repository.find_with_geometry(city_only=city_only)
        self.renderer.render(pois, out_file)
        return len(pois)

"""MapRenderer: the interface for turning a set of MapPOIs into a
viewable map. What the map is drawn with (folium, another library, a
different format entirely) is an infrastructure choice."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from domain.models.poi.map_poi import MapPOI


@dataclass
class MapRenderer(ABC):
    @abstractmethod
    def render(self, pois: list[MapPOI], out_file: Path) -> None: ...

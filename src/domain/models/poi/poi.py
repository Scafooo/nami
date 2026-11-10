"""PointOfInterest: one real place a tour could visit. label/coordinates
are required -- a place with no known location or name isn't usable, so
this is only ever built once both are known. description/images default
to empty: a real item can genuinely lack either, on Wikidata or locally.
catalogue is the Zurich Tourism record for the same place, when one exists."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.coordinates.coordinates import Coordinates
from domain.models.poi.catalogue_entry import CatalogueEntry
from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText


@dataclass
class PointOfInterest:
    uri: str
    label: str
    coordinates: Coordinates
    description: MultilingualText = field(default_factory=MultilingualText)
    images: list[Image] = field(default_factory=list)
    catalogue: CatalogueEntry | None = None

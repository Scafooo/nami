"""CatalogueEntry: what the Zurich Tourism catalogue itself records
about a place -- kept apart from the Wikidata-derived fields on
PointOfInterest, so the two sources stay distinguishable."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText


@dataclass
class CatalogueEntry:
    uri: str
    types: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    address: str | None = None
    teaser: MultilingualText = field(default_factory=MultilingualText)
    description: MultilingualText = field(default_factory=MultilingualText)
    images: list[Image] = field(default_factory=list)

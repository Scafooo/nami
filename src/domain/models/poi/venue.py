"""Venue: the place an item (typically an event) is located at -- its
own identity and description, no coordinates: a venue is only ever used
at the coordinates of the candidates that point to it."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText


@dataclass
class Venue:
    uri: str
    label: str
    description: MultilingualText = field(default_factory=MultilingualText)
    images: list[Image] = field(default_factory=list)

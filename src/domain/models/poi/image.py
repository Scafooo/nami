"""Image: one photo of a PointOfInterest, with an optional multilingual
caption. The source's "hero" image and its photo gallery aren't
distinguished -- both are just entries in one images list."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.multilingual_text import MultilingualText


@dataclass
class Image:
    url: str
    caption: MultilingualText = field(default_factory=MultilingualText)

"""CatalogueEnricher: attaches each place's Zurich Tourism catalogue
record to its PointOfInterest. A pure Domain Service -- adds, never
removes or reorders anything."""

from __future__ import annotations

from dataclasses import replace

from domain.models.poi.candidate import Candidate
from domain.models.poi.catalogue_entry import CatalogueEntry


class CatalogueEnricher:
    def attach(self, candidates: list[Candidate], entries: dict[str, CatalogueEntry]) -> list[Candidate]:
        return [
            replace(c, poi=replace(c.poi, catalogue=entries[c.poi.uri])) if c.poi.uri in entries else c
            for c in candidates
        ]

"""PlaceTypeRepository: a second source of candidates, independent of
the Wikidata graph -- places whose catalogued TYPE serves an interest
(a music venue for "music"), for interests too generic to reach a place
through Wikidata's own links."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.models.poi.candidate import Candidate


@dataclass
class PlaceTypeRepository(ABC):
    @abstractmethod
    def find_by_interests(self, interests: list[str]) -> list[Candidate]: ...

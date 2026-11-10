"""POIRepository: the one interface the domain sees for finding
PointOfInterest data, regardless of how many sources answer it.
find_by_interests(E, k) is BuildQuery/ToQuery from the paper (Algorithm
1+2): candidates within k hops that are geolocated in Zurich, plus
diagnostic counts of a real, occasionally-imperfect live run.
find_coordinates/find_labels/find_descriptions/find_images are narrower
lookups for a uri a caller already has -- returned only for uris the
implementation could actually resolve, not a value per uri regardless."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.models.poi.candidate import Candidate
from domain.models.poi.catalogue_entry import CatalogueEntry
from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText
from domain.models.poi.venue import Venue


@dataclass
class POIRepository(ABC):
    @abstractmethod
    def find_by_interests(self, interests: list[str], k: int) -> tuple[list[Candidate], int, int, int]:
        """Returns (candidates, failed_queries, hub_filtered, duplicates_flattened)."""
        ...

    @abstractmethod
    def find_coordinates(self, uris: set[str]) -> dict[str, tuple[float, float]]: ...

    @abstractmethod
    def find_labels(self, uris: set[str]) -> dict[str, str]: ...

    @abstractmethod
    def find_descriptions(self, uris: set[str]) -> dict[str, MultilingualText]: ...

    @abstractmethod
    def find_images(self, uris: set[str]) -> dict[str, list[Image]]: ...

    @abstractmethod
    def find_venues(self, uris: set[str]) -> dict[str, Venue]:
        """The place each of `uris` is located at, for those that have one
        (an event held in an arena, say) -- keyed by the uri asked about."""
        ...

    @abstractmethod
    def find_catalogue_entries(self, uris: set[str]) -> dict[str, CatalogueEntry]:
        """The Zurich Tourism catalogue record for each of `uris` that has
        one -- keyed by the uri asked about, whether it is a catalogue uri
        itself or a Wikidata one linked to a catalogue place."""
        ...

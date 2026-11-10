"""TourRepository: the interface for saving/finding generated Tours.
Nothing here says anything about JSON Lines, a knowledge graph, or any
other concrete storage -- that's the implementation's choice."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.models.tour.tour import Tour


@dataclass
class TourRepository(ABC):
    @abstractmethod
    def save(self, tour: Tour) -> None: ...

    @abstractmethod
    def find_by_user(self, user_uri: str) -> list[Tour]:
        """Every tour previously saved for `user_uri`, oldest first."""
        ...

"""UserRepository: the interface for reading and writing the User in a
KnowledgeGraph. User itself stays persistence-ignorant -- finding it and
persisting a new position are this interface's job, not User's."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from domain.models.user.user import User


@dataclass
class UserRepository(ABC):
    @abstractmethod
    def find(self) -> User: ...

    @abstractmethod
    def record_position(self, user: User, lat: float, lon: float, at: datetime) -> None:
        """Persists a new position record for `user` and updates
        `user.position` to match -- always adds, never replaces."""
        ...

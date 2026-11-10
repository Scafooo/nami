"""StaticUserRepository: a UserRepository over a User built in code
rather than read from a knowledge graph -- for running scenarios."""

from __future__ import annotations

from datetime import datetime

from domain.models.coordinates.coordinates import Coordinates
from domain.models.user.position_record import PositionRecord
from domain.models.user.user import User
from domain.repositories.user.user_repository import UserRepository


class StaticUserRepository(UserRepository):
    def __init__(self, user: User):
        self.user = user

    def find(self) -> User:
        return self.user

    def record_position(self, user: User, lat: float, lon: float, at: datetime) -> None:
        user.position = PositionRecord(coordinates=Coordinates(lat=lat, lon=lon), at=at)

"""User: plain data -- uri, interests (E in the paper's notation), and
the last recorded position, if any. No KnowledgeGraph, no SPARQL:
reading/writing a User is UserRepositoryImpl's job. position is Optional
so "never recorded" and "recorded at (0, 0)" stay distinguishable."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.user.position_record import PositionRecord


@dataclass
class User:
    uri: str
    interests: list[str] = field(default_factory=list)
    position: PositionRecord | None = None

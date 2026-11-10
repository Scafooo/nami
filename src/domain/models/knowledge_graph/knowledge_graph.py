"""KnowledgeGraph: the queryable/updatable capability itself, regardless
of engine. An ABC since the behavior is irreducibly engine-specific."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable

from domain.models.knowledge_graph.query import Query


@dataclass
class KnowledgeGraph(ABC):
    @abstractmethod
    def query(self, query: Query) -> Iterable[Any]: ...

    @abstractmethod
    def update(self, query: Query) -> None: ...

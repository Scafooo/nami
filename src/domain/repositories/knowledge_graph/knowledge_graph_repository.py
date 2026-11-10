"""KnowledgeGraphRepository: the interface for OBTAINING a KnowledgeGraph
-- opening an already-built store, opening one as writable, or
(re)building one from TBox/ABox files. Mirrors UserRepository.find() ->
User: "how do you get one", not the graph's own query()/update()
behavior. Three methods instead of one find() since a KnowledgeGraph
needs distinguishing those three cases."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph


@dataclass
class KnowledgeGraphRepository(ABC):
    @abstractmethod
    def open(self, store_dir: Path) -> KnowledgeGraph: ...

    @abstractmethod
    def open_writable(self, store_dir: Path) -> KnowledgeGraph: ...

    @abstractmethod
    def load(
        self,
        tbox_files: list[Path],
        abox_files: list[tuple[Path, str | None]],
        store_dir: Path | None = None,
        fresh: bool = True,
    ) -> KnowledgeGraph: ...

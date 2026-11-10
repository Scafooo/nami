"""OxigraphKnowledgeGraph: the Oxigraph-backed concrete KnowledgeGraph --
one of the few places that imports pyoxigraph directly. Never
constructed by domain/application code directly: everything else asks
KnowledgeGraphRepositoryImpl, below, for a ready-to-query graph and gets
back the interface type. KnowledgeGraphRepositoryImpl is the only place
that knows about Turtle files, store_dir, or how TBox/ABox differ."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Iterable

import pyoxigraph as ox

import config
from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph
from domain.models.knowledge_graph.query import Query
from domain.repositories.knowledge_graph.knowledge_graph_repository import KnowledgeGraphRepository

logger = logging.getLogger(__name__)


class OxigraphKnowledgeGraph(KnowledgeGraph):
    def __init__(self, store: ox.Store):
        self._store = store

    def query(self, query: Query) -> Iterable[Any]:
        return self._store.query(query.sparql)

    def update(self, query: Query) -> None:
        self._store.update(query.sparql)


class KnowledgeGraphRepositoryImpl(KnowledgeGraphRepository):
    def open(self, store_dir: Path) -> KnowledgeGraph:
        """Opens an already-built disk-persisted store -- see load to
        (re)build one from source files first. For something large,
        reused across many later queries (the Zurich search space). If another
        process holds it, it is opened read-only, so that runs can go in parallel."""
        try:
            return OxigraphKnowledgeGraph(ox.Store(str(store_dir)))
        except OSError:
            return OxigraphKnowledgeGraph(ox.Store.read_only(str(store_dir)))

    def open_writable(self, store_dir: Path) -> KnowledgeGraph:
        """Opens a disk-persisted store, creating it empty the first time
        -- unlike load(fresh=True), never wipes what's already there. For
        something meant to accumulate facts across many separate runs."""
        store_dir.mkdir(parents=True, exist_ok=True)
        return OxigraphKnowledgeGraph(ox.Store(str(store_dir)))

    def load(
        self,
        tbox_files: list[Path],
        abox_files: list[tuple[Path, str | None]],
        store_dir: Path | None = None,
        fresh: bool = True,
    ) -> KnowledgeGraph:
        """Loads TBox/ABox files into a store and wraps it as a
        KnowledgeGraph. `store_dir=None` (default) loads into a throwaway
        in-memory store (a user's own graph). A given `store_dir`
        (re)builds a disk-persisted one, meant to be reopened with open()."""
        if store_dir is not None:
            if fresh and store_dir.exists():
                shutil.rmtree(store_dir)
            store_dir.mkdir(parents=True, exist_ok=True)
            store = ox.Store(str(store_dir))
        else:
            store = ox.Store()

        for f in tbox_files:
            if not f.exists():
                logger.warning("skipping missing TBox file: %s", f)
                continue
            store.bulk_load(path=str(f), format=ox.RdfFormat.TURTLE, base_iri=config.DATA_IRI)
            logger.info("loaded %s -> default graph (TBox)", f.name)

        for f, graph_iri in abox_files:
            if not f.exists():
                logger.warning("skipping missing ABox file: %s", f)
                continue
            to_graph = ox.NamedNode(graph_iri) if graph_iri else None
            store.bulk_load(path=str(f), format=ox.RdfFormat.TURTLE, base_iri=config.DATA_IRI, to_graph=to_graph)
            dest = f"<{graph_iri}>" if graph_iri else "default graph"
            logger.info("loaded %s -> %s (ABox)", f.name, dest)

        if store_dir is not None:
            store.optimize()

        return OxigraphKnowledgeGraph(store)

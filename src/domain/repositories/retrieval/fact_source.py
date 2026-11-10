"""FactSource: everything best-first retrieval needs from G_P, batched per
round so an implementation can answer each call with one query."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from domain.models.retrieval.fact import Fact, Interval

LOCAL_DATA_PREFIX = "https://example.org/nami/"


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    via: str | None = None           # where the geometry came from, if not the node itself
    existence: Interval = Interval()  # when the place existed: inception, dissolution or demolition

    @property
    def inherited(self) -> bool:
        """True if the geometry belongs to a node containing this one; a
        catalogue twin of the same place is the place itself."""
        return self.via is not None and not self.via.startswith(LOCAL_DATA_PREFIX)


@dataclass(frozen=True)
class PlaceInfo:
    visit_min: float | None = None   # typical visit duration
    accessible: bool | None = None   # wheelchair accessible, if stated


class FactSource(ABC):
    @abstractmethod
    def expand(self, nodes: set[str]) -> dict[str, list[Fact]]:
        """Every fact touching each node, in either direction, with its
        annotations. Instance-of edges into a class are not followed
        backwards here: instances(...) serves class seeds."""

    @abstractmethod
    def degree(self, nodes: set[str]) -> dict[str, int]:
        """The (possibly capped) number of facts touching each node."""

    @abstractmethod
    def locate(self, nodes: set[str]) -> dict[str, Location]:
        """The location of each node that lies in the area served, own or
        inherited from a containing node. Nodes outside the area, and the
        area itself and its administrative subdivisions, are omitted."""

    @abstractmethod
    def instances_of(self, nodes: set[str], classes: set[str]) -> set[str]:
        """The subset of `nodes` that are one of `classes`, or an instance
        of one of them or of a subclass."""

    def topical(self, node: str) -> list[str]:
        """Concepts an interest stands for (facet of, has part, main subject, studies, product):
        an interest in LGBTQ history is also one in LGBTQ. None for an entity such as a person."""
        return []

    def area_members(self, concept: str) -> list[Fact]:
        """Facts linking places of the area to a concept: its instances (subclasses included) and
        the places that refer to it by any relation (a synagogue whose religion is Judaism)."""
        return []

    @abstractmethod
    def class_members(self, cls: str) -> list[Fact]:
        """Instance-of facts (x, type, cls) for instances of cls, or of a
        subclass, located in the area served."""

    def containers(self, nodes: set[str]) -> dict[str, str]:
        """For places with a geometry of their own, the place each stands on or within, when the graph
        states it (location, part of) and that place has a geometry of its own nearby, an area excluded:
        the plaque on a house, the grave in its cemetery (paper, Sect. 4.3, stops)."""
        return {}

    def inverse_properties(self, predicates: set[str]) -> dict[str, str]:
        """For each predicate, its inverse when the graph states one (doctoral advisor, doctoral student):
        two paths through the same nodes by a predicate and its inverse give the same reason."""
        return {}

    def describe(self, places: set[str]) -> dict[str, PlaceInfo]:
        """Visit duration and accessibility of candidate places, where known."""
        return {}

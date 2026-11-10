"""Query: a container for a SPARQL query, nothing more."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Query:
    sparql: str

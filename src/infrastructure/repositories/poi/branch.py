"""Branch: one UNION arm of BuildQuery(E, k) -- a Query plus the metadata
needed to turn one of its result rows into a concrete Path afterward
(see path.py's Path.from_branch_result). QueryBuilder produces many per
call."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.knowledge_graph.query import Query


@dataclass(frozen=True)
class Branch:
    """hop_nodes has length h+1: [e, <internal chain vars...>, terminal],
    where terminal is either a fresh "?..._mid" variable (mechanism
    "bridge") or "?poi" itself / e (mechanism "geolocated"). hop_preds/
    hop_dirs describe hop j as connecting hop_nodes[j] and hop_nodes[j+1]:
    real direction is (hop_nodes[j] -> hop_nodes[j+1]) if hop_dirs[j] ==
    "fwd", else reversed."""

    query: Query
    e: str
    h: int
    mechanism: str  # "bridge" | "geolocated"
    hop_preds: list[str]
    hop_dirs: list[str]
    hop_nodes: list[str]
    contain_pred: str | None = None  # e.g. "wdt:P361", only for a containment-inherited geolocation
    contain_var: str | None = None   # the SPARQL var bound to the containing entity
    coord_var: str | None = None     # bare var (no "?") bound to the WKT point -- None for mechanism
                                      # "bridge", which never fetches a coordinate of its own

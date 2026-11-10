"""Reads a value out of a SPARQL query result row -- handles both a
native Oxigraph term and a plain dict (once a row has crossed a thread
boundary)."""

from __future__ import annotations


def term_value(sol, var_name: str) -> str | None:
    # `sol` is a plain dict for rows that crossed a thread boundary and a
    # native pyoxigraph QuerySolution otherwise -- both are read the same
    # way from the caller's point of view.
    if isinstance(sol, dict):
        return sol.get(var_name)
    term = sol[var_name]
    return term.value if term is not None else None


def parse_wkt_point(wkt: str | None) -> tuple[float, float] | None:
    """Parses a "Point(lon lat)" wktLiteral (or a "<...>Point(lon lat)"
    one carrying a CRS URI prefix, e.g. from a local pois.ttl geometry) --
    returns (lon, lat), or None if wkt is None or not point-shaped."""
    if wkt is None or "(" not in wkt or ")" not in wkt:
        return None
    inner = wkt[wkt.index("(") + 1 : wkt.index(")")]
    parts = inner.split()
    if len(parts) != 2:
        return None
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return None

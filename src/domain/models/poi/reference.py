"""Reference: an entity or property named the way a reader needs it --
its short id plus a human-readable label. label is None when none could
be resolved; it is never replaced by the id."""

from __future__ import annotations

from dataclasses import dataclass


def local_name(uri: str) -> str:
    """The last path/fragment segment of an IRI ("Q937", "P69", "12345")."""
    return uri.rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]


@dataclass
class Reference:
    id: str
    label: str | None = None

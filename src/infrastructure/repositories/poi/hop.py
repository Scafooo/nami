"""Hop/GeolocatedMarker: the two kinds of Step a Path is made of -- one
relatedness step (subject --predicate--> object) and the terminal
geolocation evidence that closes a Path off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Union

from infrastructure.repositories.poi.vocabulary import WikidataProperty

STRUCTURAL_PREDICATES = {
    WikidataProperty.PART_OF,
    WikidataProperty.HAS_PART,
    WikidataProperty.CHILD_ORG,
    WikidataProperty.PARENT_ORG,
    WikidataProperty.HEADQUARTERS_LOCATION,
    WikidataProperty.LOCATED_IN_ADMIN_ENTITY,
    WikidataProperty.LOCATION,
    WikidataProperty.OWNED_BY,
    WikidataProperty.OWNER_OF,
}


def extract_wikidata_id(value: str) -> str | None:
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1]
    for prefix in ("http://www.wikidata.org/entity/", "http://www.wikidata.org/prop/direct/", "wd:", "wdt:"):
        if value.startswith(prefix):
            return value[len(prefix):]
    return None


Describe = Callable[[str], str]


@dataclass
class Hop:
    """One relatedness step: subject --predicate--> object. `note`, when
    set, marks this as geolocation-resolution evidence (a containment
    hop, or the owl:sameAs identity bridge) rather than a step in the
    interest's own relatedness chain."""

    subject: str
    predicate: str
    object: str
    note: str | None = None

    def is_structural(self) -> bool:
        pid = extract_wikidata_id(self.predicate)
        return bool(pid and pid in STRUCTURAL_PREDICATES)

    def render(self, describe: Describe) -> str:
        text = f"{describe(self.subject)} --{describe(self.predicate)}--> {describe(self.object)}"
        if self.note:
            text += f" ({self.note})"
        return text


@dataclass
class GeolocatedMarker:
    """Terminal evidence: `node` is geolocated inside Zurich."""

    node: str

    def render(self, describe: Describe) -> str:
        return f"{describe(self.node)} is geolocated in Zurich"


Step = Union[Hop, GeolocatedMarker]

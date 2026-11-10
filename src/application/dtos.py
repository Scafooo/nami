"""DTOs for use case output -- the shape a caller (a CLI, later an API)
actually wants: plain, serializable data. Kept separate from the domain
model so a transport concern never has to touch it."""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.models.poi.candidate import Candidate
from domain.models.poi.catalogue_entry import CatalogueEntry
from domain.models.poi.explanation import Explanation
from domain.models.poi.reference import Reference, local_name
from domain.models.tour.tour import Tour


def _reference(ref: Reference) -> dict:
    return {"id": ref.id, "label": ref.label}


def _explanation(explanation: Explanation) -> dict:
    return {
        "text": explanation.text,
        "hops": [
            {
                "from": _reference(s.source),
                "via": _reference(s.predicate),
                "to": _reference(s.target),
                **({"note": s.note} if s.note else {}),
            }
            for s in explanation.steps
        ],
    }


def _catalogue(entry: CatalogueEntry) -> dict:
    return {
        "id": local_name(entry.uri),
        "types": entry.types,
        "categories": entry.categories,
        "address": entry.address,
        "teaser": dict(entry.teaser.by_language),
        "description": dict(entry.description.by_language),
        "images": [i.url for i in entry.images],
    }


@dataclass
class WaypointDTO:
    uri: str
    label: str
    lat: float
    lon: float
    explanations: list[str] = field(default_factory=list)
    description: str | None = None
    image_url: str | None = None


@dataclass
class TourDTO:
    user_uri: str
    interests: list[str]
    k: int
    generated_at: str
    stops: list[WaypointDTO] = field(default_factory=list)

    @classmethod
    def from_tour(cls, tour: Tour) -> "TourDTO":
        return cls(
            user_uri=tour.user_uri,
            interests=tour.interests,
            k=tour.k,
            generated_at=tour.generated_at.isoformat(),
            stops=[
                WaypointDTO(
                    uri=w.poi.uri, label=w.poi.label,
                    lat=w.poi.coordinates.lat, lon=w.poi.coordinates.lon,
                    explanations=w.explanations,
                    description=w.poi.description.get("en", "de"),
                    image_url=w.poi.images[0].url if w.poi.images else None,
                )
                for w in tour.stops
            ],
        )

    def to_dict(self) -> dict:
        return {"stops": [local_name(s.uri) for s in self.stops]}


@dataclass
class CandidateDTO:
    """A candidate as retrieved and place-aggregated, before any tour
    selection (max_stops, when one is asked for)."""

    candidate: Candidate

    def to_dict(self) -> dict:
        c = self.candidate
        return {
            "id": local_name(c.poi.uri),
            "uri": c.poi.uri,
            "name": c.poi.label,
            "lat": c.poi.coordinates.lat,
            "lon": c.poi.coordinates.lon,
            "tier": c.tier.value,
            "reached_from": [_reference(r) for r in c.reached_from],
            "why": [_explanation(e) for e in c.explanations],
            "description": dict(c.poi.description.by_language),
            "images": [i.url for i in c.poi.images],
            **({"includes": [_reference(r) for r in c.includes]} if c.includes else {}),
            **({"catalogue": _catalogue(c.poi.catalogue)} if c.poi.catalogue else {}),
        }


@dataclass
class RecommendationDTO:
    tour: TourDTO
    candidates: list[CandidateDTO]
    failed_queries: int
    hub_filtered: int
    duplicates_flattened: int

"""LocalPlaceTypeRepository: candidates from the Zurich Tourism
catalogue by place TYPE (within the same Zurich bounding box the
Wikidata search is restricted to -- the catalogue covers the whole region), driven by a curated table from interest
(a Wikidata id) to the schema.org types of place that serve it. The
table is data (mappings/interest_place_types.json), not code: adding an
interest is an edit to that file."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import config
from domain.models.coordinates.coordinates import Coordinates
from domain.models.poi.candidate import Candidate, Tier
from domain.models.poi.explanation import Explanation, ExplanationStep
from domain.models.poi.multilingual_text import MultilingualText
from domain.models.poi.poi import PointOfInterest
from domain.models.poi.reference import Reference
from domain.repositories.poi.place_type_repository import PlaceTypeRepository
from infrastructure.repositories.poi.hop import extract_wikidata_id
from infrastructure.repositories.poi.local_poi_repository import LocalPOIRepository

_MATCHES = Reference(id="placeType", label="matches place type")


def load_interest_place_types(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text())


@dataclass
class LocalPlaceTypeRepository(PlaceTypeRepository):
    local_repository: LocalPOIRepository
    interest_place_types: dict[str, dict]
    bbox: tuple[float, float, float, float] = config.ZURICH_BBOX

    def find_by_interests(self, interests: list[str]) -> list[Candidate]:
        wanted: dict[str, list[tuple[Reference, str]]] = {}  # schema type -> [(interest, type)]
        for interest in interests:
            qid = extract_wikidata_id(interest)
            entry = self.interest_place_types.get(qid or "")
            if entry:
                ref = Reference(id=qid, label=entry["label"])
                for schema_type in entry["types"]:
                    wanted.setdefault(schema_type, []).append((ref, schema_type))
        west, south, east, north = self.bbox
        rows = [
            r for r in self.local_repository.find_places_by_type(set(wanted))
            if west <= r[2] <= east and south <= r[3] <= north
        ]
        entries = self.local_repository.find_catalogue_entries({r[0] for r in rows})

        by_place: dict[str, Candidate] = {}
        for uri, label, lon, lat, schema_type in sorted(rows):
            entry = entries.get(uri)
            candidate = by_place.get(uri) or Candidate(
                poi=PointOfInterest(
                    uri=uri, label=label, coordinates=Coordinates(lat=lat, lon=lon),
                    description=entry.description if entry else MultilingualText(),
                    images=list(entry.images) if entry else [],
                    catalogue=entry,
                ),
                explanations=[], tier=Tier.PRIMARY,
            )
            by_place[uri] = candidate
            for interest_ref, _ in wanted[schema_type]:
                type_ref = Reference(id=schema_type, label=schema_type)
                candidate.explanations.append(Explanation(
                    text=(f"{interest_ref.label} ({interest_ref.id}) --{_MATCHES.label}--> {schema_type} ; "
                          f"{label} is catalogued as {schema_type} in Zurich"),
                    interest=interest_ref,
                    steps=[ExplanationStep(
                        source=interest_ref, predicate=_MATCHES, target=type_ref,
                        note="Zurich Tourism catalogue type, not a Wikidata link",
                    )],
                ))
        return list(by_place.values())

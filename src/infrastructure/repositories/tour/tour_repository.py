"""TourRepositoryImpl: saves/finds generated tours as plain JSON Lines
records, not a knowledge graph -- Tour is an ordered sequence, and RDF
has no clean way to say "first this, then this". One line per Tour,
append-only."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import config
from domain.models.coordinates.coordinates import Coordinates
from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText
from domain.models.poi.poi import PointOfInterest
from domain.models.tour.tour import Tour
from domain.models.tour.waypoint import Waypoint
from domain.repositories.tour.tour_repository import TourRepository

DEFAULT_PATH = config.ROOT_DIR / "output" / "tours.jsonl"


class TourRepositoryImpl(TourRepository):
    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = path

    def save(self, tour: Tour) -> None:
        record = {
            "user_uri": tour.user_uri,
            "interests": tour.interests,
            "k": tour.k,
            "generated_at": tour.generated_at.isoformat(),
            "stops": [
                {
                    "uri": w.poi.uri,
                    "label": w.poi.label,
                    "lat": w.poi.coordinates.lat,
                    "lon": w.poi.coordinates.lon,
                    "explanations": w.explanations,
                    "description": w.poi.description.by_language,
                    "images": [{"url": img.url, "caption": img.caption.by_language} for img in w.poi.images],
                }
                for w in tour.stops
            ],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def find_by_user(self, user_uri: str) -> list[Tour]:
        """Every tour previously saved for `user_uri`, oldest first."""
        if not self.path.exists():
            return []
        tours = []
        with open(self.path) as f:
            for line in f:
                record = json.loads(line)
                if record["user_uri"] != user_uri:
                    continue
                tours.append(Tour(
                    user_uri=record["user_uri"],
                    stops=[
                        Waypoint(
                            poi=PointOfInterest(
                                uri=s["uri"], label=s["label"],
                                coordinates=Coordinates(lat=s["lat"], lon=s["lon"]),
                                # .get() with a default: older records have neither key.
                                description=MultilingualText(by_language=s.get("description", {})),
                                images=[
                                    Image(url=img["url"], caption=MultilingualText(by_language=img["caption"]))
                                    for img in s.get("images", [])
                                ],
                            ),
                            explanations=s["explanations"],
                        )
                        for s in record["stops"]
                    ],
                    interests=record["interests"],
                    k=record["k"],
                    generated_at=datetime.fromisoformat(record["generated_at"]),
                ))
        return tours

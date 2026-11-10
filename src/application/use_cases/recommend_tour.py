"""RecommendTourUseCase: orchestrates one full BuildQuery/ToQuery run into
a saved, ordered Tour. Coordinates data retrieval and hands off to the
pure TourBuilder domain service for ordering -- no calculation rule of
its own, only loading, delegating, and saving. Depends on the
repository interfaces, never their concrete implementations."""

from __future__ import annotations

from application.dtos import CandidateDTO, RecommendationDTO, TourDTO
from domain.repositories.poi.place_type_repository import PlaceTypeRepository
from domain.repositories.poi.poi_repository import POIRepository
from domain.repositories.tour.tour_repository import TourRepository
from domain.repositories.user.user_repository import UserRepository
from domain.services.catalogue_enricher import CatalogueEnricher
from domain.services.place_aggregator import PlaceAggregator
from domain.services.tour_builder import TourBuilder


class RecommendTourUseCase:
    def __init__(
        self,
        user_repository: UserRepository,
        poi_repository: POIRepository,
        tour_repository: TourRepository,
        tour_builder: TourBuilder,
        place_aggregator: PlaceAggregator,
        catalogue_enricher: CatalogueEnricher,
        place_type_repository: PlaceTypeRepository | None = None,
    ):
        self.user_repository = user_repository
        self.poi_repository = poi_repository
        self.tour_repository = tour_repository
        self.tour_builder = tour_builder
        self.place_aggregator = place_aggregator
        self.catalogue_enricher = catalogue_enricher
        self.place_type_repository = place_type_repository

    def execute(self, k: int, max_stops: int | None = None) -> RecommendationDTO:
        user = self.user_repository.find()
        candidates, failed_queries, hub_filtered, duplicates_flattened = self.poi_repository.find_by_interests(
            user.interests, k
        )
        if self.place_type_repository is not None:
            candidates = candidates + self.place_type_repository.find_by_interests(user.interests)
        venues = self.poi_repository.find_venues({c.poi.uri for c in candidates})
        wanted = {c.poi.uri for c in candidates} | {v.uri for v in venues.values()}
        entries = self.poi_repository.find_catalogue_entries(wanted)
        same_as = {uri: entry.uri for uri, entry in entries.items()}
        candidates = self.place_aggregator.aggregate(candidates, venues, same_as)
        candidates = self.catalogue_enricher.attach(candidates, entries)
        tour = self.tour_builder.build(candidates, user, user.interests, k, max_stops=max_stops)
        self.tour_repository.save(tour)
        return RecommendationDTO(
            tour=TourDTO.from_tour(tour),
            candidates=[CandidateDTO(c) for c in candidates],
            failed_queries=failed_queries,
            hub_filtered=hub_filtered,
            duplicates_flattened=duplicates_flattened,
        )

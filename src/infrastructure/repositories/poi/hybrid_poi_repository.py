"""HybridPOIRepository: the concrete POIRepository. Composes QueryBuilder
(the algorithm) with WikidataClient (live-endpoint resilience) and
LocalPOIRepository (our own catalogue) to execute every Branch,
reconstructs each result into a Path of Hops, applies the hub-noise
filter, resolves labels/descriptions/images, and ranks into
primary/secondary tiers.

The "Hybrid": a candidate's coordinate can come from Wikidata directly,
a Wikidata-side containment hop, or a local owl:sameAs bridge -- three
sources, one contract. A candidate reachable only through a hub node is
dropped, and near-duplicate secondary candidates sharing a structural
parent are capped -- both display filters, each reported as a count
rather than silently shrinking the result. A nonzero failed-query count
means some deep hops timed out even after retrying: honest but not
necessarily complete.

Run standalone: python src/infrastructure/repositories/poi/hybrid_poi_repository.py
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

import requests

import config
from domain.models.poi.candidate import Candidate, Tier
from domain.models.poi.catalogue_entry import CatalogueEntry
from domain.models.poi.explanation import Explanation, ExplanationStep
from domain.models.coordinates.coordinates import Coordinates
from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText
from domain.models.poi.poi import PointOfInterest
from domain.models.poi.reference import Reference, local_name
from domain.models.poi.venue import Venue
from domain.repositories.poi.poi_repository import POIRepository
from infrastructure.clients.wikidata_client import WikidataClient
from infrastructure.repositories.poi.hop import Hop, extract_wikidata_id
from infrastructure.repositories.poi.local_poi_repository import LocalPOIRepository
from infrastructure.repositories.poi.path import Path, describe
from infrastructure.repositories.poi.query_builder import QueryBuilder
from infrastructure.repositories.sparql_rows import parse_wkt_point, term_value

logger = logging.getLogger(__name__)

_NON_WIKIDATA_LABELS = {"http://www.w3.org/2002/07/owl#sameAs": "same as"}
_WIKIDATA_ENTITY = "http://www.wikidata.org/entity/"


class HybridPOIRepository(POIRepository):
    def __init__(
        self,
        query_builder: QueryBuilder,
        client: WikidataClient,
        local_repository: LocalPOIRepository | None = None,
    ):
        self.query_builder = query_builder
        self.client = client
        self.kg = query_builder.kg  # the KnowledgeGraph queries are run against
        self.local_repository = local_repository or LocalPOIRepository(self.kg, query_builder.abox_graphs)
        self._failed_lookups = 0

    def find_by_interests(self, interests: list[str], k: int) -> tuple[list[Candidate], int, int, int]:
        branches = self.query_builder.build(interests, k)
        self._failed_lookups = 0

        paths_by_poi: dict[str, list[Path]] = defaultdict(list)
        coords_by_poi: dict[str, tuple[float, float]] = {}
        failed_queries = 0
        for branch in branches:
            try:
                rows = self.client.query_with_retry(self.kg, branch.query)
            except OSError as ex:
                # A handful of queries (deep, unbound-predicate hops
                # through a generic hub) are expensive enough to hit
                # Wikidata's own server-side timeout on every retry; one
                # such query must not lose the results every other query
                # already found.
                failed_queries += 1
                logger.warning("query h=%s mechanism=%s gave up: %s", branch.h, branch.mechanism, ex)
                continue
            for sol in rows:
                poi_uri = term_value(sol, "poi")
                if poi_uri is None:
                    continue
                paths_by_poi[poi_uri].append(Path.from_branch_result(branch, sol))
                if poi_uri not in coords_by_poi and branch.coord_var is not None:
                    coord = parse_wkt_point(term_value(sol, branch.coord_var))
                    if coord is not None:
                        coords_by_poi[poi_uri] = coord

        hub_filtered = self._drop_hub_only_candidates(paths_by_poi, interests)
        duplicates_flattened = self._flatten_structural_duplicates(paths_by_poi, interests)
        self._resolve_local_coords(paths_by_poi, coords_by_poi)
        labels, local_labels = self._resolve_labels(paths_by_poi)
        descriptions, local_descriptions = self._resolve_descriptions(paths_by_poi.keys())
        images, local_images = self._resolve_images(paths_by_poi.keys())

        candidates: list[Candidate] = []
        no_coordinate = 0
        for poi_uri, paths in paths_by_poi.items():
            if poi_uri not in coords_by_poi:
                # Should be near-impossible -- QueryBuilder only ever
                # admits a geolocated node -- but a bridge candidate's
                # local geometry lookup can still come up empty on a real
                # data gap, and a PointOfInterest with no coordinate isn't
                # a usable one.
                no_coordinate += 1
                continue
            lon, lat = coords_by_poi[poi_uri]
            wikidata_id = extract_wikidata_id(poi_uri)
            label = local_labels.get(poi_uri) or labels.get(wikidata_id or "") or wikidata_id or poi_uri
            description = local_descriptions.get(poi_uri) or descriptions.get(wikidata_id or "") or MultilingualText()
            poi_images = local_images.get(poi_uri) or images.get(wikidata_id or "") or []
            poi = PointOfInterest(
                uri=poi_uri, label=label, coordinates=Coordinates(lat=lat, lon=lon),
                description=description, images=poi_images,
            )
            tier = Tier.PRIMARY if any(p.is_personal for p in paths) else Tier.SECONDARY
            explanations = [self._explanation(p, labels, local_labels) for p in paths]
            candidates.append(Candidate(poi=poi, explanations=explanations, tier=tier))
        if no_coordinate:
            logger.warning("dropped %d candidate(s) with no resolvable coordinate", no_coordinate)

        # Primary first, then by uri -- not just a stable sort on discovery
        # order, since that order comes from Wikidata's live SPARQL
        # endpoint, which makes no row-order guarantee absent ORDER BY (not
        # a Python hash-randomization issue -- dict insertion order is
        # already deterministic; the rows feeding it aren't).
        candidates.sort(key=lambda c: (c.tier != Tier.PRIMARY, c.poi.uri))
        failed_queries += self._failed_lookups
        if failed_queries:
            logger.warning(
                "%d live request(s) gave up (%d of them label/description/image/hub lookups) -- "
                "results below are honest but incomplete",
                failed_queries, self._failed_lookups,
            )
        return candidates, failed_queries, hub_filtered, duplicates_flattened

    def find_coordinates(self, uris: set[str]) -> dict[str, tuple[float, float]]:
        return self.local_repository.find_coordinates(uris)

    def find_labels(self, uris: set[str]) -> dict[str, str]:
        return self.local_repository.find_labels(uris)

    def find_descriptions(self, uris: set[str]) -> dict[str, MultilingualText]:
        return self.local_repository.find_descriptions(uris)

    def find_images(self, uris: set[str]) -> dict[str, list[Image]]:
        return self.local_repository.find_images(uris)

    def _live(self, what: str, default, call, *args):
        """A secondary Wikidata lookup (labels, descriptions, images, hub
        check): if it fails, carry on without it and count it as a failed
        request -- the result is incomplete, not lost."""
        try:
            return call(*args)
        except (requests.RequestException, ValueError) as ex:
            self._failed_lookups += 1
            logger.warning("%s failed (%s); continuing without it", what, type(ex).__name__)
            return default

    def find_catalogue_entries(self, uris: set[str]) -> dict[str, CatalogueEntry]:
        wikidata_ids = {uri: wid for uri in uris if (wid := extract_wikidata_id(uri))}
        twins = self.local_repository.find_twins(set(wikidata_ids.values()))
        local_uris = {uri for uri in uris if uri.startswith(config.DATA_IRI)}
        entries = self.local_repository.find_catalogue_entries(local_uris | set(twins.values()))
        found = {uri: entries[uri] for uri in local_uris if uri in entries}
        for uri, wid in wikidata_ids.items():
            if wid in twins and twins[wid] in entries:
                found[uri] = entries[twins[wid]]
        return found

    def find_venues(self, uris: set[str]) -> dict[str, Venue]:
        """Best effort: if Wikidata can't be reached, no venue is named and
        the caller falls back to the candidates' own names."""
        wikidata_ids = {uri: wid for uri in uris if (wid := extract_wikidata_id(uri))}
        try:
            location_of = self.client.resolve_locations(set(wikidata_ids.values()))
            # A P276 "location" can be the whole city, not a real venue
            # (e.g. a legal case whose "location" is just Zurich) --
            # exactly what QueryBuilder already excludes as a candidate
            # in its own right, so it can't become one by the back door.
            location_of = {wid: loc for wid, loc in location_of.items() if loc != self.query_builder.excluded_node}
            location_ids = set(location_of.values())
            labels = self.client.resolve_labels(location_ids)
            descriptions = self.client.resolve_descriptions(location_ids)
            images = self.client.resolve_images(location_ids)
        except (requests.RequestException, ValueError) as ex:
            logger.warning("venue lookup failed (%s); places keep their own names", type(ex).__name__)
            return {}
        venues: dict[str, Venue] = {}
        for uri, wid in wikidata_ids.items():
            location = location_of.get(wid)
            if location is not None:
                venues[uri] = Venue(
                    uri=_WIKIDATA_ENTITY + location,
                    label=labels.get(location) or location,
                    description=descriptions.get(location) or MultilingualText(),
                    images=images.get(location, []),
                )
        return venues

    def _reference(self, value: str, labels: dict[str, str], local_labels: dict[str, str]) -> Reference:
        wid = extract_wikidata_id(value)
        if wid:
            return Reference(id=wid, label=labels.get(wid))
        bare = value[1:-1] if value.startswith("<") and value.endswith(">") else value
        label = local_labels.get(bare) or _NON_WIKIDATA_LABELS.get(bare)
        return Reference(id=local_name(bare), label=label)

    def _explanation(self, path: Path, labels: dict[str, str], local_labels: dict[str, str]) -> Explanation:
        steps = [
            ExplanationStep(
                source=self._reference(s.subject, labels, local_labels),
                predicate=self._reference(s.predicate, labels, local_labels),
                target=self._reference(s.object, labels, local_labels),
                note=s.note,
            )
            for s in path.steps
            if isinstance(s, Hop)
        ]
        return Explanation(
            text=path.render(lambda v: describe(v, labels, local_labels)),
            interest=self._reference(path.interest, labels, local_labels) if path.interest else None,
            steps=steps,
        )

    def _drop_hub_only_candidates(self, paths_by_poi: dict[str, list[Path]], interests: list[str]) -> int:
        """Drops a candidate only if EVERY path to it routes through a hub
        node -- one clean, specific path is enough to keep it, even if
        other paths to the same candidate are hub-noise."""
        interest_ids = {extract_wikidata_id(e) for e in interests}
        interest_ids.discard(None)
        intermediate_ids: set[str] = set()
        for poi, paths in paths_by_poi.items():
            exclude = interest_ids | {extract_wikidata_id(poi)}
            for path in paths:
                intermediate_ids |= path.intermediate_wikidata_ids(exclude)
        hub_ids = {qid for qid in intermediate_ids if self._live("hub check", False, self.client.is_hub, qid)}
        hub_filtered = 0
        if hub_ids:
            for poi in list(paths_by_poi):
                clean_paths = [p for p in paths_by_poi[poi] if not p.uses_any(hub_ids)]
                if clean_paths:
                    paths_by_poi[poi] = clean_paths
                else:
                    del paths_by_poi[poi]
                    hub_filtered += 1
            logger.info("dropped %d candidate(s) reachable only via a hub node %s", hub_filtered, sorted(hub_ids))
        return hub_filtered

    def _flatten_structural_duplicates(
        self, paths_by_poi: dict[str, list[Path]], interests: list[str], keep: int = 2,
    ) -> int:
        """Caps how many secondary-tier candidates sharing the same
        intermediate parent are kept -- e.g. a dozen ETH Zurich
        departments reached the same organizational way become just two.
        Scoped to secondary only: a primary candidate's hop is already
        specific to the interest, so sharing a parent doesn't make it a
        duplicate."""
        interest_ids = {extract_wikidata_id(e) for e in interests}
        interest_ids.discard(None)
        groups: dict[frozenset[str], list[str]] = defaultdict(list)
        for poi, paths in paths_by_poi.items():
            if any(p.is_personal for p in paths):
                continue  # primary tier -- not in scope for this cap
            exclude = interest_ids | {extract_wikidata_id(poi)}
            signature: frozenset[str] = frozenset()
            for path in paths:
                signature |= path.intermediate_wikidata_ids(exclude)
            if signature:
                groups[signature].append(poi)
        flattened = 0
        for pois in groups.values():
            for poi in pois[keep:]:
                del paths_by_poi[poi]
                flattened += 1
        if flattened:
            logger.info("flattened %d near-duplicate secondary candidate(s) sharing a parent", flattened)
        return flattened

    def _resolve_local_coords(
        self, paths_by_poi: dict[str, list[Path]], coords_by_poi: dict[str, tuple[float, float]],
    ) -> None:
        """Fills in a coordinate for a bridge-mechanism candidate from our
        own local catalogue -- admitted via owl:sameAs, not a Wikidata
        coordinate, so its geometry is looked up separately."""
        missing = {poi for poi in paths_by_poi if poi not in coords_by_poi and poi.startswith(config.DATA_IRI)}
        coords_by_poi.update(self.local_repository.find_coordinates(missing))

    def _resolve_labels(self, paths_by_poi: dict[str, list[Path]]) -> tuple[dict[str, str], dict[str, str]]:
        """Collects every Wikidata id and local POI URI appearing anywhere
        in any surviving path for one batched label lookup each."""
        wd_ids: set[str] = set()
        local_uris: set[str] = set()
        for paths in paths_by_poi.values():
            for path in paths:
                wd_ids |= path.wikidata_ids()
                local_uris |= path.local_uris(config.DATA_IRI)
        labels = self._live("label lookup", {}, self.client.resolve_labels, wd_ids)
        local_labels = self.local_repository.find_labels(local_uris)
        return labels, local_labels

    def _resolve_descriptions(self, poi_uris: Iterable[str]) -> tuple[dict[str, MultilingualText], dict[str, MultilingualText]]:
        """Description for the candidates themselves, not every uri in a
        path the way _resolve_labels covers -- a description only makes
        sense for the POI being described, not an intermediate hop."""
        wd_ids = {wid for uri in poi_uris if (wid := extract_wikidata_id(uri))}
        local_uris = {uri for uri in poi_uris if uri.startswith(config.DATA_IRI)}
        descriptions = self._live("description lookup", {}, self.client.resolve_descriptions, wd_ids)
        local_descriptions = self.local_repository.find_descriptions(local_uris)
        return descriptions, local_descriptions

    def _resolve_images(self, poi_uris: Iterable[str]) -> tuple[dict[str, list[Image]], dict[str, list[Image]]]:
        """Images for the candidates themselves -- same scoping as
        _resolve_descriptions, for the same reason."""
        wd_ids = {wid for uri in poi_uris if (wid := extract_wikidata_id(uri))}
        local_uris = {uri for uri in poi_uris if uri.startswith(config.DATA_IRI)}
        images = self._live("image lookup", {}, self.client.resolve_images, wd_ids)
        local_images = self.local_repository.find_images(local_uris)
        return images, local_images


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    repository = HybridPOIRepository(
        query_builder=QueryBuilder(),
        client=WikidataClient(),
    )
    candidates, failed, hub_filtered, flattened = repository.find_by_interests(["wd:Q937"], 2)
    for c in candidates:
        print(f"- [{c.tier}] {c.poi.label or c.poi.uri}")
        for explanation in c.explanations:
            print(f"    {explanation.text}")
        if description := c.poi.description.get("en", "de"):
            print(f"    {description}")
        for image in c.poi.images:
            print(f"    image: {image.url}")

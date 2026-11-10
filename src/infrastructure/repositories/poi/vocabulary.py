"""The small, fixed subset of Wikidata's own property/entity vocabulary
QueryBuilder and hop.py are built around -- Wikidata's schema, not our
own ontology, curated down to exactly the terms this algorithm needs.
Members hold the bare id ("P361", not "wdt:P361"); callers embedding one
into SPARQL prepend wdt:/wd: themselves, which an f-string renders as
the plain id via the str mixin."""

from __future__ import annotations

from enum import Enum


class WikidataProperty(str, Enum):
    # -- containment: resolves geolocation for a node with none of its own
    # (see QueryBuilder's containment_predicates)
    LOCATED_IN_ADMIN_ENTITY = "P131"
    LOCATION = "P276"
    PART_OF = "P361"

    # -- structural/organizational: a real relation, but "same institution"
    # rather than anything specific to the interest (see hop.py's
    # STRUCTURAL_PREDICATES)
    HAS_PART = "P527"
    CHILD_ORG = "P355"
    PARENT_ORG = "P749"
    HEADQUARTERS_LOCATION = "P159"
    OWNED_BY = "P127"
    OWNER_OF = "P1830"

    # -- excluded from the open traversal entirely (QueryBuilder's
    # excluded_predicates): external-identifier properties, whose literal
    # values can coincidentally collide across unrelated catalogues.
    FIND_A_GRAVE_ID = "P535"
    STREET_KEY = "P1945"
    CATALOG_CODE = "P528"

    # -- also excluded: generic shared-attribute properties (a
    # given/family name) -- matching through one only proves "same name".
    GIVEN_NAME = "P735"
    FAMILY_NAME = "P734"


class WikidataEntity(str, Enum):
    # Trivially "geolocated inside Zurich" -- excluded from QueryBuilder's
    # traversal as a hop or candidate.
    ZURICH = "Q72"


class WikidataClass(str, Enum):
    # instance-of value shared by all 12 of Zurich's own administrative
    # districts (Kreis 1-12) -- as generic a "place" as the city itself
    # (verified: District 1 and District 8, reached in unrelated runs,
    # both carry exactly this P31). Excluded from QueryBuilder's
    # traversal as a candidate for the same reason as ZURICH.
    ZURICH_DISTRICT = "Q19644409"

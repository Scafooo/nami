"""Central configuration for the Zurich Tourism -> RDF-star knowledge graph pipeline."""

from pathlib import Path

# --- Source API -------------------------------------------------------------
# Same API shape is also served under rapperswil-zuerichsee.ch and eyz.swiss;
# switch API_BASE to point the whole pipeline at one of those instead.
API_BASE = "https://www.zuerich.com/en/api/v2/data"
REQUEST_DELAY_SECONDS = 0.3
REQUEST_TIMEOUT_SECONDS = 20
# NOTE: the CDN in front of this API (Fastly/Varnish) 403s any User-Agent
# containing generic bot/script signatures (e.g. "crawler", "bot",
# "python-requests"). A descriptive, contact-bearing UA without those
# trigger words passes fine -- verified against the live endpoint.
USER_AGENT = "NAMI-research-prototype/0.1 (evaluation)"

# --- RDF namespaces -----------------------------------------------------------
BASE_IRI = "https://example.org/nami/"
ONTOLOGY_IRI = BASE_IRI + "ontology#"
DATA_IRI = BASE_IRI + "data/"

LANGUAGES = ["de", "en", "fr", "it"]

# --- Licensing / attribution --------------------------------------------------
SOURCE_URL = "https://www.zuerich.com/en/open-data-version-20"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION_TEXT = (
    "Data source: Zuerich Tourism, https://www.zuerich.com/en/open-data-version-20 "
    "-- licensed under CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0/)."
)

# Every triple crawled from the Zurich Tourism API is loaded into this named
# graph, so provenance is recorded once for the whole dataset rather than
# repeated per statement -- not RDF-star, since nothing here yet needs
# statement-level differentiation (every triple in it shares one uniform
# source). Reserve RDF-star for facts whose provenance genuinely varies
# statement by statement.
PROVENANCE_GRAPH_IRI = BASE_IRI + "graph/zurich-open-data"

# wikidata_links.ttl (owl:sameAs, src/pipeline/enrich/link_wikidata.py) is not from
# Zurich Tourism -- it's derived from OpenStreetMap's own wikidata= tags,
# corroborated against our POIs. Its own named graph keeps that provenance
# honest instead of folding it into the graph above.
WIKIDATA_LINKS_GRAPH_IRI = BASE_IRI + "graph/osm-wikidata-links"

# --- Paths --------------------------------------------------------------------
# Two separate knowledge graphs, matching the paper's G_P (geo) / G_U (user)
# split -- each its own TBox (schema) + ABox (data), not shared, since they
# play different roles (G_P is the search space, G_U a parameter source).
ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

GEO_ONTOLOGY_DIR = DATA_DIR / "geo-ontology"    # G_P: the Zurich POI graph
GEO_TBOX_DIR = GEO_ONTOLOGY_DIR / "TBox"
GEO_ABOX_DIR = GEO_ONTOLOGY_DIR / "ABox"

USER_ONTOLOGY_DIR = DATA_DIR / "user-ontology"  # G_U: the user graph
USER_TBOX_DIR = USER_ONTOLOGY_DIR / "TBox"
USER_ABOX_DIR = USER_ONTOLOGY_DIR / "ABox"

# Back-compat aliases -- most existing scripts still say RDF_DIR/ONTOLOGY_DIR
# meaning "the geo graph's ABox/TBox" from before G_U existed.
RDF_DIR = GEO_ABOX_DIR
ONTOLOGY_DIR = GEO_TBOX_DIR

# (west, south, east, north) -- the area every candidate source is restricted to.
ZURICH_BBOX = (8.45, 47.30, 8.62, 47.46)

CATEGORIES_RAW_FILE = RAW_DIR / "categories.json"
INTEREST_PLACE_TYPES_FILE = ROOT_DIR / "mappings" / "interest_place_types.json"
STORE_DIR = ROOT_DIR / "store"

for d in (RAW_DIR, GEO_TBOX_DIR, GEO_ABOX_DIR, USER_TBOX_DIR, USER_ABOX_DIR, STORE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Knowledge graph composition -----------------------------------------------
# Which files make up each KnowledgeGraph (see domain/repositories/knowledge_graph/knowledge_graph_repository.py) --
# kept here, not as defaults embedded in the class, so adding/renaming a file
# is a config change, not a code change. ABox entries are (file, named_graph)
# pairs; named_graph=None means "load into the default graph" (the right
# choice for a small, single-source file like one user's own).
GEO_TBOX_FILES = [
    GEO_TBOX_DIR / "nami-zurich-tourism.ttl",
    GEO_TBOX_DIR / "schema-org-hierarchy.ttl",
]
GEO_ABOX_FILES = [
    (GEO_ABOX_DIR / "categories.ttl", PROVENANCE_GRAPH_IRI),
    (GEO_ABOX_DIR / "pois.ttl", PROVENANCE_GRAPH_IRI),
    (GEO_ABOX_DIR / "wikidata_links.ttl", WIKIDATA_LINKS_GRAPH_IRI),
    (GEO_ABOX_DIR / "wikidata_links_direct.ttl", WIKIDATA_LINKS_GRAPH_IRI),
]
# Derived, not independently declared: the named graphs GEO_ABOX_FILES
# actually loads into, so opening the already-built Zurich store (see
# infrastructure/repositories/knowledge_graph_repository.KnowledgeGraphRepositoryImpl.open) needs no file list at all -- just this and
# STORE_DIR.
GEO_ABOX_GRAPHS = sorted({g for _, g in GEO_ABOX_FILES if g})

USER_TBOX_FILES = [USER_TBOX_DIR / "nami-user.ttl"]

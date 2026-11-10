"""Composition root for the best-first pipeline (paper, Sec. 4).

Run:  python src/main_best_first.py data/user-ontology/ABox/einstein_oppenheimer.ttl
      python src/main_best_first.py --interests Q937 Q41532 [--out output/x.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

import pyoxigraph as ox

import config
from application.use_cases.recommend_tour_best_first import RecommendTourBestFirst
from domain.services.best_first_retriever import SearchParams
from domain.services.path_valuation import ValuationParams
from domain.services.tour_composer import TourParams
from infrastructure.clients.wikidata_client import WikidataClient
from infrastructure.repositories.knowledge_graph.knowledge_graph_repository import KnowledgeGraphRepositoryImpl
from infrastructure.repositories.poi.local_poi_repository import LocalPOIRepository
from infrastructure.repositories.retrieval.caching_fact_source import CachingFactSource
from infrastructure.repositories.retrieval.curated_fact_source import CuratedFactSource
from infrastructure.repositories.retrieval.wikidata_fact_source import WikidataFactSource
from infrastructure.repositories.user.rdfstar_profile_repository import load_profile, profile_from_interests

NAMI = "https://example.org/nami/ontology#"
CURATED = sorted((config.GEO_ONTOLOGY_DIR / "curated").glob("*.ttl"))
EXTRACTED = sorted((config.GEO_ONTOLOGY_DIR / "extracted").glob("*.ttl"))   # extract_mentions.py
POI = "https://example.org/nami/data/poi/"
WD = "http://www.wikidata.org/entity/"
# Areas, never candidates: human settlements and municipalities (the served area and its
# districts are excluded by the Wikidata source itself).
NON_PLACES = frozenset(WD + q for q in ("Q486972", "Q15284", "Q70208"))


def type_level_annotations() -> tuple[dict, dict, frozenset, dict, dict]:
    store = ox.Store()
    for f in ("relation_weights.ttl", "source_trust.ttl", "visit_durations.ttl"):
        store.load((config.ROOT_DIR / "mappings" / f).read_bytes(), format=ox.RdfFormat.TURTLE)
    q = lambda s: list(store.query(f"PREFIX nami: <{NAMI}>\n" + s))
    weights = {r["p"].value: float(r["w"].value) for r in q("SELECT ?p ?w WHERE { ?p nami:defaultWeight ?w }")}
    trust = {r["s"].value: float(r["t"].value)
             for r in q("SELECT ?s ?t WHERE { ?s nami:trust ?t FILTER(isIRI(?s)) }")}
    trust_prefixes = {r["x"].value: float(r["t"].value)
                      for r in q("SELECT ?x ?t WHERE { ?s nami:trustPrefix ?x ; nami:trust ?t }")}
    excluded = frozenset(r["p"].value for r in q("SELECT ?p WHERE { ?p a nami:ExcludedRelation }"))
    roles = {r["r"].value: float(r["w"].value) for r in q("SELECT ?r ?w WHERE { ?r nami:roleWeight ?w }")}
    durations = {r["c"].value: float(r["m"].value) for r in q("SELECT ?c ?m WHERE { ?c nami:visitMinutes ?m }")}
    return weights, (trust, trust_prefixes), excluded, roles, durations


def generic_text_relations(path: Path, data: bytes) -> bytes:
    """Ablation: every relation read from the catalogue text becomes the generic nami:mentionedAt."""
    if path not in EXTRACTED:
        return data
    return re.sub(rb"nami:(livedAt|buriedAt|workedAt|foundedAt|frequented|performedAt|commemoratedAt|themedAfter|bornAt|createdAt)\b",
                  b"nami:mentionedAt", data)


def build(args) -> RecommendTourBestFirst:
    weights, (trust, trust_prefixes), excluded, roles, durations = type_level_annotations()
    store = KnowledgeGraphRepositoryImpl().open(config.STORE_DIR)
    local = LocalPOIRepository(store, config.GEO_ABOX_GRAPHS)
    remote = WikidataFactSource(local_repository=local)
    remote.durations = durations
    base = remote if args.no_cache else CachingFactSource(remote, config.ROOT_DIR / "store_gp")
    layers = ([] if args.no_curated else CURATED) + ([] if args.no_extracted else EXTRACTED)
    source = CuratedFactSource(layers, base, local, config.ZURICH_BBOX,
                               rewrite=generic_text_relations if args.generic_text_relations else None,
                               non_places=NON_PLACES,
                               texts=None if args.no_extracted or args.no_text_concepts
                               else config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_texts.json",
                               terms_cache=config.RAW_DIR / "concept_terms_cache.json")
    client = WikidataClient()
    return RecommendTourBestFirst(
        source=source,
        valuation=ValuationParams(theta=args.theta, lam=args.lam, tau=args.tau, gamma=args.gamma, d0=args.d0, d_pred=args.d_pred,
                                  relation_weights=weights, source_trust=trust, source_trust_prefixes=trust_prefixes,
                                  excluded_relations=excluded,
                                  role_weights=roles, phi_inherited=args.phi,
                                  unannotated_confidence=0.8, ignore_provenance=args.ignore_provenance),
        search=SearchParams(beam=args.beam, max_expansions=args.max_expansions or None),
        tour=TourParams(budget_min=args.budget),
        top_candidates=args.top,
        min_score=args.min_score,
        references=remote.references,
        link_state=remote._link_state,
        labeller=lambda ids: {
            **(base.labels if hasattr(base, "labels") else (lambda i, f: f(i)))(
                {i for i in ids if i and i[0] in "QP" and i[1:].isdigit()}, client.resolve_labels),
            **{u.removeprefix(POI): l for u, l in local.find_labels({POI + i for i in ids if i and i.isdigit()}).items()},
            **source.labels_of({i for i in ids if i and i.startswith(("bt_", "art_", "ded_", "osm_"))})},
    )


def add_args(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--theta", type=float, default=0.5)
    ap.add_argument("--lam", type=float, default=0.5)
    ap.add_argument("--tau", type=float, default=0.15)
    ap.add_argument("--gamma", type=float, default=0.7)
    ap.add_argument("--d0", type=int, default=2000)
    ap.add_argument("--d-pred", type=int, default=10)
    ap.add_argument("--phi", type=float, default=0.8, help="discount for a location inherited from a container")
    ap.add_argument("--beam", type=int, default=50)
    ap.add_argument("--max-expansions", type=int, default=400, help="0 = no budget")
    ap.add_argument("--ignore-provenance", action="store_true", help="ablation: confidence and trust 1 for every fact")
    ap.add_argument("--budget", type=float, default=240, help="tour time budget, minutes")
    ap.add_argument("--no-cache", action="store_true", help="query Wikidata directly, bypassing store_gp/")
    ap.add_argument("--no-extracted", action="store_true", help="leave out the facts extracted from the catalogue text")
    ap.add_argument("--no-curated", action="store_true", help="leave out the hand-curated facts")
    ap.add_argument("--no-text-concepts", action="store_true", help="do not link concepts named in the texts of places")
    ap.add_argument("--top", type=int, default=30, help="candidates kept in the report")
    ap.add_argument("--min-score", type=float, default=0.0, help="leave out stops worth less than this")
    ap.add_argument("--generic-text-relations", action="store_true",
                    help="ablation: read every extracted relation as nami:mentionedAt")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("user_file", type=Path, nargs="?")
    ap.add_argument("--interests", nargs="*")
    ap.add_argument("--out", type=Path)
    add_args(ap)
    args = ap.parse_args()
    profile = load_profile(args.user_file) if args.user_file else profile_from_interests(args.interests)
    report = build(args).execute(profile)
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    for c in report["candidates"][:15]:
        print(f"{c['score']:.3f}  {c['label']}")
        for e in c["explanations"]:
            print(f"        {e['text']}")
    for i, t in enumerate(report["tours"]):
        print(f"Tour {i + 1} ({t['order']}, value {t['value']}, {t['total_min']} min): "
              + " -> ".join(s["label"] for s in t["stops"]))
    print(json.dumps(report["stats"]))


if __name__ == "__main__":
    main()

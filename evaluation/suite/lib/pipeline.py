"""The NAMI pipeline of src/main_best_first.py, built for one condition of the suite. Every switch here is
an ablation or a setting of config.NAMI; nothing changes the method itself."""

from __future__ import annotations

import random
import sys
from pathlib import Path

from lib.suite_paths import CODE, SRC  # noqa: F401  (puts src/ on the path)

import config as code_config                                   # src/config.py
import main_best_first as M
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

UNREFERENCED = ("urn:nami:source:wikidata-unreferenced", "http://www.wikidata.org/entity/statement/")


class NoDerivedSeeds:
    """Ablation A3: the source answers that no interest stands for any concept."""

    def __init__(self, source):
        self._source = source

    def topical(self, node):
        return []

    def __getattr__(self, name):
        return getattr(self._source, name)


def build(params: dict, switches: dict, extra_layers: list[Path] = (), trust_prefixes: dict | None = None,
          city2: dict | None = None) -> RecommendTourBestFirst:
    p = {**params, **{k: v for k, v in switches.items() if k in params}}
    weights, (trust, prefixes), excluded, roles, durations = M.type_level_annotations()
    prefixes = dict(prefixes)
    if "unreferenced_trust" in switches:
        trust = {**trust, UNREFERENCED[0]: switches["unreferenced_trust"]}
        prefixes[UNREFERENCED[1]] = switches["unreferenced_trust"]
    prefixes.update(trust_prefixes or {})
    client = WikidataClient()
    if city2:
        remote = WikidataFactSource(bbox=tuple(city2["bbox"]), area=M.WD + city2["area"],
                                    area_subdivision_classes=tuple(M.WD + c for c in city2["subdivision_classes"]),
                                    local_repository=None)
        remote.durations = durations
        source = CachingFactSource(remote, CODE / "store_gp_city2")
        labeller = lambda ids: client.resolve_labels({i for i in ids if i and i[0] in "QP" and i[1:].isdigit()})
    else:
        store = KnowledgeGraphRepositoryImpl().open(code_config.STORE_DIR)
        local = LocalPOIRepository(store, code_config.GEO_ABOX_GRAPHS)
        remote = WikidataFactSource(local_repository=local)
        remote.durations = durations
        base = CachingFactSource(remote, CODE / "store_gp")
        only_wd = switches.get("wikidata_only", False)
        layers = ([] if only_wd else M.CURATED + M.EXTRACTED) + list(extra_layers)
        source = CuratedFactSource(layers, base, local, code_config.ZURICH_BBOX, non_places=M.NON_PLACES,
                                   texts=None if only_wd else code_config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_texts.json",
                                   terms_cache=code_config.RAW_DIR / "concept_terms_cache.json")
        labeller = lambda ids: {
            **(base.labels if hasattr(base, "labels") else (lambda i, f: f(i)))(
                {i for i in ids if i and i[0] in "QP" and i[1:].isdigit()}, client.resolve_labels),
            **{u.removeprefix(M.POI): l for u, l in local.find_labels({M.POI + i for i in ids if i and i.isdigit()}).items()},
            **source.labels_of({i for i in ids if i and i.startswith(("bt_", "art_", "ded_", "osm_"))})}
    if switches.get("no_derived"):
        source = NoDerivedSeeds(source)
    valuation = ValuationParams(
        theta=p["theta"], lam=p["lam"], tau=p["tau"], d0=p["d0"], d_pred=p["d_pred"],
        relation_weights=weights, source_trust=trust, source_trust_prefixes=prefixes, excluded_relations=excluded,
        role_weights=roles, unannotated_confidence=0.8, ignore_provenance=switches.get("ignore_provenance", False),
        **({"type_relations": frozenset()} if switches.get("allow_class_up") else {}))
    return RecommendTourBestFirst(
        source=source, valuation=valuation,
        search=SearchParams(beam=p["beam"], max_expansions=p["max_expansions"] or None, topical_weight=p["topical_weight"]),
        tour=TourParams(), top_candidates=30, min_score=0.0,
        references=remote.references, link_state=remote._link_state, labeller=labeller)


def injected_layer(path: Path, persons: list[str], places: dict[str, list[str]], spec: dict, seed: int) -> list[dict]:
    """A fabricated source for the control scenario (Prop. 3): `n_facts` facts linking a person of the
    benchmark to a place of the city that no source links to that person (a stop of another interest).
    Written as an extra layer of G_P; its trust is set per run through its URL prefix."""
    rng = random.Random(seed)
    facts = []
    for n in range(spec["n_facts"]):
        person = persons[n % len(persons)]
        pool = [q for i, qs in places.items() if i != person for q in qs]
        facts.append({"person": person, "place": rng.choice(pool), "source": f"{spec['source']}fact/{n}"})
    lines = ["@prefix nami: <https://example.org/nami/ontology#> .", "@prefix wd: <http://www.wikidata.org/entity/> .", ""]
    for f in facts:
        lines.append(f"wd:{f['person']} nami:{spec['predicate']} wd:{f['place']} "
                     f"{{| nami:source <{f['source']}> ; nami:confidence 0.9 |}} .")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return facts

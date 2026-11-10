"""QueryBuilder implements the paper's BuildQuery/ToQuery (Algorithm
1+2), federated across two sources: interests are Wikidata QIDs, and
their neighbourhood (any relation, either direction) is explored live on
Wikidata via SPARQL SERVICE, since our own store only knows ~1400
Zurich POIs.

Admission criterion: a reached node is a candidate iff it's geolocated
in Zurich, via either (a) its own Wikidata coordinate (wd:Q72, Zurich
itself, and every one of its own administrative districts, are excluded
-- trivially "in Zurich", a scope error not a result), or (b) failing
that, a local owl:sameAs bridge to our own catalogue, used only as a
last resort so it never overrides a node Wikidata already places.

BuildQuery(E, k) is compiled into a separate Branch (see branch.py) per
(interest, hop-count, direction, mechanism) combination and run one at a
time (see hybrid_poi_repository.py), so each result traces back to the
path that justified it."""

from __future__ import annotations

import itertools

import config
from domain.models.knowledge_graph.knowledge_graph import KnowledgeGraph
from domain.models.knowledge_graph.query import Query
from infrastructure.repositories.knowledge_graph.knowledge_graph_repository import KnowledgeGraphRepositoryImpl
from infrastructure.repositories.poi.branch import Branch
from infrastructure.repositories.poi.vocabulary import WikidataClass, WikidataEntity, WikidataProperty

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"


class QueryBuilder:
    """Compiles BuildQuery(E, k) into a list of Branch objects, against a
    given KnowledgeGraph. bbox/excluded_node/excluded_types/
    containment_predicates/excluded_predicates are instance config, not
    module constants, so a different city or dataset only means
    constructing a different QueryBuilder."""

    WDT_NAMESPACE = "http://www.wikidata.org/prop/direct/"

    def __init__(
        self,
        bbox: tuple[float, float, float, float] = config.ZURICH_BBOX,
        excluded_node: str = WikidataEntity.ZURICH,
        # As generic a "place" as excluded_node, just one type up: not one
        # specific entity but a whole class of them (e.g. every one of
        # Zurich's own administrative districts).
        excluded_types: tuple[str, ...] = (WikidataClass.ZURICH_DISTRICT,),
        # Resolves geolocation for an already-reached node with none of
        # its own (e.g. "ETH Library" wdt:P361 "ETH Zurich") -- not a
        # traversal predicate, one hop only.
        containment_predicates: tuple[str, ...] = (
            WikidataProperty.LOCATED_IN_ADMIN_ENTITY,
            WikidataProperty.LOCATION,
            WikidataProperty.PART_OF,
        ),
        # Excluded from the open traversal entirely (not just demoted in
        # display ranking): external-identifier predicates can collide by
        # coincidence across unrelated catalogues, and shared-attribute
        # ones (given/family name) only prove "same name", not "connected".
        excluded_predicates: tuple[str, ...] = (
            WikidataProperty.FIND_A_GRAVE_ID,
            WikidataProperty.STREET_KEY,
            WikidataProperty.CATALOG_CODE,
            WikidataProperty.GIVEN_NAME,
            WikidataProperty.FAMILY_NAME,
        ),
        kg: "KnowledgeGraph | None" = None,
        abox_graphs: list[str] | None = None,
        endpoint: str = WIKIDATA_ENDPOINT,
    ):
        self.bbox_west, self.bbox_south, self.bbox_east, self.bbox_north = bbox
        self.excluded_node = excluded_node
        self.excluded_types = excluded_types
        self.containment_predicates = containment_predicates
        self.excluded_predicates = excluded_predicates
        self.kg = kg or KnowledgeGraphRepositoryImpl().open(config.STORE_DIR)
        self.abox_graphs = config.GEO_ABOX_GRAPHS if abox_graphs is None else abox_graphs
        self.endpoint = endpoint
        self.prefixes = (
            "PREFIX schema: <https://schema.org/>\n"
            "PREFIX owl: <http://www.w3.org/2002/07/owl#>\n"
            "PREFIX wd: <http://www.wikidata.org/entity/>\n"
            "PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
            "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
            "PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
        )

    def _from_clause(self) -> str:
        return "".join(f"FROM <{g}>\n" for g in self.abox_graphs)

    def _wikidata_chain(self, e: str, h: int, dirs: tuple[str, ...], var_prefix: str, terminal: str):
        """A chain of h hops from e within Wikidata, any relation, either
        direction, AllDistinct among its own nodes, ending at `terminal`.
        Returns (triples, filter, pred_vars, nodes) -- nodes has length
        h+1, from e to terminal, with the intermediate ones fresh
        variables projected for path reconstruction. For h == 0 there's
        no chain; callers handle that separately.

        Each hop's predicate is restricted to wdt: (direct/truthy), not
        Wikidata's full reified statement model (p:/ps:/pq:/prov:) --
        without this, a chain can wander onto a statement/reference node,
        not a real-world entity. excluded_node is also excluded from
        every chain position, not just as a final candidate: it's
        "administratively part of the city" under almost any relation,
        never specific to the interest."""
        nodes = [e] + [f"?{var_prefix}_m{j}" for j in range(1, h)] + [terminal]
        triples, pred_vars = [], []
        for j in range(h):
            p = f"?{var_prefix}_p{j}"
            pred_vars.append(p)
            s, o = nodes[j], nodes[j + 1]
            triples.append(f"{s} {p} {o} ." if dirs[j] == "fwd" else f"{o} {p} {s} .")
        var_nodes = nodes[1:]
        pairs = [f"{a} != {b}" for a, b in itertools.combinations(var_nodes, 2)]
        pairs += [f"{n} != {e}" for n in var_nodes]
        pairs += [f"{n} != wd:{self.excluded_node}" for n in var_nodes]
        pairs += [f'STRSTARTS(STR({p}), "{self.WDT_NAMESPACE}")' for p in pred_vars]
        pairs += [f"{p} != wdt:{pred}" for p in pred_vars for pred in self.excluded_predicates]
        filter_clause = f"FILTER({' && '.join(pairs)})" if pairs else ""
        return " ".join(triples), filter_clause, pred_vars, nodes

    def _make_bridge_branch(self, e: str, h: int, dirs: tuple[str, ...], var_prefix: str) -> Branch:
        """(b) mechanism -- gated on Wikidata being silent (see module
        docstring), so this only adds what _make_geolocated_branches
        missed. Only "?poi owl:sameAs mid" is checked: wikidata_links*.ttl
        always writes the local POI as subject."""
        mid = e if h == 0 else f"?{var_prefix}_mid"
        if h == 0:
            triples, filt, pred_vars, nodes = "", "", [], [e]
        else:
            triples, filt, pred_vars, nodes = self._wikidata_chain(e, h, dirs, var_prefix, mid)
        # "Silent" means no coordinate at all, directly or via containment
        # -- not "no coordinate in Zurich": a node Wikidata places
        # elsewhere isn't re-admitted just because a local bridge claims
        # otherwise.
        not_exists_clauses = [f"FILTER NOT EXISTS {{ {mid} wdt:P625 ?anyGeo }}"]
        not_exists_clauses += [
            f"FILTER NOT EXISTS {{ {mid} wdt:{pred} ?anyCont . ?anyCont wdt:P625 ?anyGeo{i} }}"
            for i, pred in enumerate(self.containment_predicates)
        ]
        wd_block = (
            f"SERVICE <{self.endpoint}> {{ {triples} {filt} "
            f"{' '.join(not_exists_clauses)} }} ."
        )
        internal_vars = nodes[1:-1]  # fresh chain vars only, excludes e and mid
        project_extra = " ".join(pred_vars + internal_vars + ([mid] if h > 0 else []))
        sparql = (
            f"{self.prefixes}SELECT DISTINCT ?poi {project_extra}\n{self._from_clause()}WHERE {{\n"
            f"  {wd_block} ?poi owl:sameAs {mid} . ?poi a schema:Place .\n}}"
        )
        return Branch(
            query=Query(sparql=sparql),
            e=e, h=h, mechanism="bridge",
            hop_preds=pred_vars, hop_dirs=list(dirs), hop_nodes=nodes,
        )

    def _not_excluded_type_clause(self, subject: str) -> str:
        return "\n    ".join(f"FILTER NOT EXISTS {{ {subject} wdt:P31 wd:{t} }}" for t in self.excluded_types)

    def _coord_check(self, subject: str, v: str) -> str:
        # Parses the "Point(lon lat)" wktLiteral with plain SPARQL 1.1
        # string functions -- geof:longitude/latitude isn't supported on
        # Wikidata's endpoint, and wikibase:box needs a variable subject
        # (fails at h==0).
        return (
            f"FILTER({subject} != wd:{self.excluded_node})\n"
            f"    {self._not_excluded_type_clause(subject)}\n"
            f"    {subject} wdt:P625 ?{v} .\n"
            f'    BIND(xsd:double(STRBEFORE(STRAFTER(STR(?{v}), "("), " ")) AS ?{v}_lon)\n'
            f'    BIND(xsd:double(STRBEFORE(STRAFTER(STR(?{v}), " "), ")")) AS ?{v}_lat)\n'
            f"    FILTER(?{v}_lon >= {self.bbox_west} && ?{v}_lon <= {self.bbox_east}"
            f" && ?{v}_lat >= {self.bbox_south} && ?{v}_lat <= {self.bbox_north})"
        )

    def _make_geolocated_branches(self, e: str, h: int, dirs: tuple[str, ...], var_prefix: str) -> list[Branch]:
        """(a) mechanism: geolocated by its own Wikidata coordinate, or
        by inheriting one from a containing entity."""
        node = "?poi" if h > 0 else e
        if h == 0:
            triples, filt, pred_vars, nodes = "", "", [], [e]
        else:
            triples, filt, pred_vars, nodes = self._wikidata_chain(e, h, dirs, var_prefix, node)
        bind_clause = "" if h > 0 else f"BIND({e} AS ?poi) ."
        internal_vars = nodes[1:-1]

        branches = []
        v = f"pc{var_prefix}"
        direct_body = (
            f"SERVICE <{self.endpoint}> {{\n"
            f"    {triples} {filt}\n"
            f"    {self._coord_check(node, v)}\n"
            f"  }} . {bind_clause}\n"
        )
        project_extra = " ".join(pred_vars + internal_vars + [f"?{v}"])
        sparql = f"{self.prefixes}SELECT DISTINCT ?poi {project_extra}\n{self._from_clause()}WHERE {{\n{direct_body}\n}}"
        branches.append(Branch(
            query=Query(sparql=sparql),
            e=e, h=h, mechanism="geolocated",
            hop_preds=pred_vars, hop_dirs=list(dirs), hop_nodes=nodes,
            coord_var=v,
        ))

        for pred in self.containment_predicates:
            cont_var = f"cont{var_prefix}_{pred}"
            body = (
                f"SERVICE <{self.endpoint}> {{\n"
                f"    {triples} {filt}\n"
                # _coord_check below only excludes excluded_node/excluded_types
                # as the *container*; the candidate itself needs its own check.
                f"    FILTER({node} != wd:{self.excluded_node})\n"
                f"    {self._not_excluded_type_clause(node)}\n"
                # Only inherit when the node has none of its own -- same
                # own-data-first precedence as the bridge mechanism.
                f"    FILTER NOT EXISTS {{ {node} wdt:P625 ?ownGeo }}\n"
                f"    {node} wdt:{pred} ?{cont_var} .\n"
                f"    {self._coord_check(f'?{cont_var}', v)}\n"
                f"  }} . {bind_clause}\n"
            )
            project_extra = " ".join(pred_vars + internal_vars + [f"?{cont_var}", f"?{v}"])
            sparql = f"{self.prefixes}SELECT DISTINCT ?poi {project_extra}\n{self._from_clause()}WHERE {{\n{body}\n}}"
            branches.append(Branch(
                query=Query(sparql=sparql),
                e=e, h=h, mechanism="geolocated",
                hop_preds=pred_vars, hop_dirs=list(dirs), hop_nodes=nodes,
                contain_pred=f"wdt:{pred}", contain_var=f"?{cont_var}",
                coord_var=v,
            ))
        return branches

    def build(self, interests: list[str], k: int) -> list[Branch]:
        """BuildQuery(E, k), compiled as one Branch per (interest, hop-
        count, direction, mechanism) combination instead of one monolithic
        UNION, so each result can be traced back to the path that
        justified it."""
        branches: list[Branch] = []
        for i, e in enumerate(interests):
            for h in range(0, k):  # h Wikidata-side hops + 1 closing hop = depth h+1
                for j, dirs in enumerate(itertools.product(("fwd", "bwd"), repeat=h)):
                    var_prefix = f"e{i}h{h}d{j}"
                    branches.extend(self._make_geolocated_branches(e, h, dirs, var_prefix))
                    branches.append(self._make_bridge_branch(e, h, dirs, var_prefix))
        return branches

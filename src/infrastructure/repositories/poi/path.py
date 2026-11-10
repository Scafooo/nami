"""Path: one concrete route from an interest to a candidate POI -- an
ordered list of Hops ending in a GeolocatedMarker (undoing the fwd/bwd
rewriting QueryBuilder used to express either-direction traversal)."""

from __future__ import annotations

from infrastructure.repositories.poi.hop import Describe, GeolocatedMarker, Hop, Step, extract_wikidata_id
from infrastructure.repositories.sparql_rows import term_value


class Path:
    def __init__(self, steps: list[Step], interest: str | None = None):
        self.steps = steps
        self.interest = interest

    @property
    def is_personal(self) -> bool:
        """True if EVERY relatedness hop is outside STRUCTURAL_PREDICATES
        -- one purely organizational hop is enough to make the whole path
        generic. A Hop carrying a note (containment/bridge) doesn't
        count. No relatedness hops at all counts as personal by default."""
        for step in self.steps:
            if isinstance(step, Hop) and not step.note and step.is_structural():
                return False
        return True

    def render(self, describe: Describe) -> str:
        return " ; ".join(step.render(describe) for step in self.steps)

    def wikidata_ids(self) -> set[str]:
        ids: set[str] = set()
        for step in self.steps:
            values = (step.subject, step.predicate, step.object) if isinstance(step, Hop) else (step.node,)
            for val in values:
                wid = extract_wikidata_id(val)
                if wid:
                    ids.add(wid)
        return ids

    def local_uris(self, data_iri: str) -> set[str]:
        uris: set[str] = set()
        for step in self.steps:
            values = (step.subject, step.predicate, step.object) if isinstance(step, Hop) else (step.node,)
            for val in values:
                if val and val.startswith(data_iri) and not extract_wikidata_id(val):
                    uris.add(val)
        return uris

    def intermediate_wikidata_ids(self, exclude: set[str]) -> set[str]:
        """Wikidata ids appearing as a Hop's subject/object, excluding the
        given set (typically the run's interests and the final POI) --
        used to hub-check the nodes a path actually routes through."""
        ids: set[str] = set()
        for step in self.steps:
            if not isinstance(step, Hop):
                continue
            for val in (step.subject, step.object):
                wid = extract_wikidata_id(val)
                if wid and wid not in exclude:
                    ids.add(wid)
        return ids

    def uses_any(self, wikidata_ids: set[str]) -> bool:
        for step in self.steps:
            if not isinstance(step, Hop):
                continue
            for val in (step.subject, step.object):
                if extract_wikidata_id(val) in wikidata_ids:
                    return True
        return False

    @classmethod
    def from_branch_result(cls, branch, sol) -> "Path":
        """Reconstructs the concrete Path for one result row of `branch`
        (an infrastructure.repositories.poi.branch.Branch)."""

        def node_value(var_or_const: str) -> str:
            if var_or_const.startswith("?"):
                return term_value(sol, var_or_const[1:])
            # a constant like "wd:Q937" or "?poi" handled by caller
            return var_or_const

        steps: list[Step] = []
        for j in range(branch.h):
            s_expr, o_expr = branch.hop_nodes[j], branch.hop_nodes[j + 1]
            p_val = term_value(sol, branch.hop_preds[j].lstrip("?"))
            s_val = node_value(s_expr) if s_expr != "?poi" else term_value(sol, "poi")
            o_val = node_value(o_expr) if o_expr != "?poi" else term_value(sol, "poi")
            if branch.hop_dirs[j] == "fwd":
                steps.append(Hop(subject=s_val, predicate=p_val, object=o_val))
            else:
                steps.append(Hop(subject=o_val, predicate=p_val, object=s_val))

        if branch.mechanism == "bridge":
            mid_expr = branch.hop_nodes[-1]
            mid_val = node_value(mid_expr) if mid_expr != "?poi" else term_value(sol, "poi")
            poi_val = term_value(sol, "poi")
            steps.append(Hop(
                subject=poi_val,
                predicate="http://www.w3.org/2002/07/owl#sameAs",
                object=mid_val,
                note="no coordinate on Wikidata; located via local catalogue",
            ))
        elif branch.contain_pred:
            poi_val = term_value(sol, "poi")
            container_val = term_value(sol, branch.contain_var.lstrip("?"))
            steps.append(Hop(
                subject=poi_val,
                predicate=branch.contain_pred,
                object=container_val,
                note="no coordinate of its own; inherits geolocation from here",
            ))
            steps.append(GeolocatedMarker(node=container_val))
        else:
            poi_val = term_value(sol, "poi")
            steps.append(GeolocatedMarker(node=poi_val))

        return cls(steps, interest=branch.e)


def describe(value: str, labels: dict[str, str], local_labels: dict[str, str]) -> str:
    wid = extract_wikidata_id(value)
    if wid and wid in labels:
        return f"{labels[wid]} ({wid})"
    if value in local_labels:
        return f"{local_labels[value]} <{value}>"
    if wid:
        return wid
    bare = value[1:-1] if value.startswith("<") and value.endswith(">") else value
    return f"<{bare}>"

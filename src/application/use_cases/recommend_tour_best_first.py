"""RecommendTourBestFirst: the pipeline of the paper's Method section --
best-first retrieval (4.2), ranking and explanation (4.3), tour
composition (4.4) -- returning a plain, JSON-ready report."""

from __future__ import annotations

import time
from dataclasses import asdict

from domain.models.retrieval.path import RetrievalPath
from domain.models.user.profile import UserProfile
from domain.repositories.retrieval.fact_source import FactSource
from domain.services.best_first_retriever import BestFirstRetriever, SearchParams
from domain.services.candidate_ranker import CandidateRanker, RankedCandidate
from domain.services.path_valuation import ValuationParams
from domain.services.tour_composer import TourComposer, TourParams


def _local(iri: str | None) -> str | None:
    if iri is None:
        return None
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1]


class RecommendTourBestFirst:
    def __init__(self, source: FactSource, valuation: ValuationParams, search: SearchParams,
                 tour: TourParams, labeller=None, top_candidates: int = 30, min_score: float = 0.0,
                 references=None, link_state=None):
        self.source = source
        self.valuation = valuation
        self.search = search
        self.tour = tour
        self.labeller = labeller          # callable: set of bare ids -> {id: label}
        self.top = top_candidates
        self.min_score = min_score        # stops worth less are left out (0: keep all)
        self.references = references      # callable: [(s, p, o)] -> {(s, p, o): reference}, for explanations
        self.link_state = link_state      # callable: url -> (opens, archived copy or None)

    def _remote_queries(self):
        src, seen = self.source, 0
        while src is not None and seen < 5:
            if hasattr(src, "remote"):
                src = src.remote
            elif hasattr(src, "base"):
                src = src.base
            else:
                return getattr(src, "queries", None)
            seen += 1
        return None

    def execute(self, profile: UserProfile, start=None) -> dict:
        q0 = self._remote_queries() or 0
        t0 = time.time()
        result = BestFirstRetriever(self.source, self.valuation, self.search).retrieve(profile)
        t1 = time.time()
        # a derived seed counts as the seed it derives from; POIs standing on or within another are gathered
        # there; a predicate and its inverse give the same reason (paper, Sect. 4.3)
        origin = {s.iri: s.source for s in profile.seeds if s.source}
        own = {c for c, loc in result.locations.items() if c in result.candidates and not loc.inherited}
        own |= {loc.via for c, loc in result.locations.items() if c in result.candidates and loc.inherited}
        within = self.source.containers(own) if own else {}
        preds = {f.predicate for ps in result.candidates.values() for p in ps for f in p.facts}
        inverse = self.source.inverse_properties(preds) if preds else {}
        inverse = {**{q: p for p, q in inverse.items()}, **inverse}
        ranked = CandidateRanker(min_score=self.min_score).rank(result.candidates, result.locations,
                                                                origin=origin, within=within, inverse=inverse)
        info = self.source.describe({c.place for c in ranked}) if ranked else {}
        for c in ranked:
            if c.place in info:
                c.visit_min, c.accessible = info[c.place].visit_min, info[c.place].accessible
        tours = TourComposer(self.tour).compose(ranked, profile, start)
        t2 = time.time()

        shown = ranked[: self.top]
        ids = {_local(s.iri) for s in profile.seeds} | {_local(s.source) for s in profile.seeds if s.source}
        for c in shown + [s for t in tours for s in t.stops] + [r for c in shown for r in c.reasons]:
            ids.add(_local(c.place))
            ids |= {_local(g) for g in getattr(c, 'gathered', [])}
            if getattr(c, "location", None) is not None and c.location.via:
                ids.add(_local(c.location.via))
            for p in [p for ps in c.paths.values() for p in ps] or c.best_paths.values():
                ids |= {_local(n) for n in p.nodes} | {_local(f.predicate) for f in p.facts}
                ids |= {_local(f.role) for f in p.facts if f.role}
                ids |= {_local(f.source) for f in p.facts if f.source and "wikidata.org" in f.source}
        labels = self.labeller(ids) if self.labeller else {}
        # What each shown Wikidata fact rests on: the work it is stated in, or its reference URL.
        refs = {}
        if self.references:
            shown_facts = {(f.subject, f.predicate, f.object) for c in shown + [r for c in shown for r in c.reasons]
                           for p in [p for ps in c.paths.values() for p in ps] or c.best_paths.values() for f in p.facts
                           if f.source is None or f.source.startswith(("urn:nami", "http://www.wikidata.org/"))}
            try:
                refs = self.references(sorted(shown_facts))
            except Exception:       # explanations still render, with the coarser source
                refs = {}
        L = lambda iri: labels.get(_local(iri)) or _local(iri)

        def hop(f) -> dict:
            v = f.validity
            period = "-".join(str(d.year) if d else "?" for d in (v.start, v.end)) if v.known else None
            ref = refs.get((f.subject, f.predicate, f.object))
            link = None
            if ref and (ref["label"] or ref["url"] or ref["item"]):
                # the work the statement is stated in, else its reference URL
                src = "Wikidata, citing " + (ref["label"] or (L(ref["item"]) if ref["item"] else None) or ref["url"])
                link = ref["url"] or ("https://www.wikidata.org/wiki/" + _local(ref["item"]) if ref["item"] else None)
                if ref["url"] and ref.get("alive") is False:
                    # the cited page no longer opens: its archived copy, else the statement itself
                    link = ref.get("archive") or "https://www.wikidata.org/wiki/" + _local(f.subject)
                    src += " (archived copy)" if ref.get("archive") else " (page no longer available)"
            elif ref:
                src = "Wikidata, statement without reference"
                link = "https://www.wikidata.org/wiki/" + _local(f.subject)
            elif f.source is None or f.source.startswith("urn:nami"):
                src = "Wikidata"
                if f.subject.startswith("http://www.wikidata.org/entity/Q"):
                    link = "https://www.wikidata.org/wiki/" + _local(f.subject)     # the item, which a reader can open
            elif f.source.startswith("http://www.wikidata.org/entity/statement/"):
                # No reference: the statement itself, which a reader can open.
                src = "Wikidata, statement " + f.source.rsplit("/", 1)[-1]
            elif "wikidata.org/entity/Q" in f.source:
                src = L(f.source)
            else:
                src = f.source
                if f.source.startswith("http") and "wikidata.org" not in f.source and self.link_state:
                    alive, archive = self.link_state(f.source.split("#")[0])
                    if not alive and archive:
                        src, link = f"{f.source} (archived copy)", archive
            return {"from": L(f.subject), "via": L(f.predicate), "to": L(f.object), "role": L(f.role) if f.role else None,
                    "confidence": f.confidence, "period": period, "source": src, "evidence": f.evidence,
                    "ids": [f.subject, f.predicate, f.object], "source_iri": f.source, "source_link": link}

        def explain(path: RetrievalPath) -> dict:
            seed = profile.seed(path.seed)
            hops = [hop(f) for f in path.facts]
            text = f"You are interested in {L(seed.iri)} ({_local(seed.source) or 'declared'}). " + "; ".join(
                f"{h['from']} --{h['via']}" + (f" (as {h['role']})" if h["role"] else "") + f"--> {h['to']}"
                + (f" [{h['period']}]" if h["period"] else "") + (f" (source: {h['source']})" if h["source"] else "")
                + (f' ("{h["evidence"]}")' if h["evidence"] else "")
                for h in hops)
            return {"seed": L(path.seed), "value": round(path.value, 4), "length": path.length, "hops": hops, "text": text}

        def candidate(c: RankedCandidate) -> dict:
            return {"id": _local(c.place), "label": L(c.place), "score": round(c.score, 4),
                    "per_seed": {L(k): round(v, 4) for k, v in c.per_seed.items()},
                    "lat": c.location.lat, "lon": c.location.lon, "located_via": _local(c.location.via),
                    "located_at": L(c.location.via) if c.location.via else None,
                    "exists": "-".join(str(d.year) if d else "?" for d in (c.location.existence.start, c.location.existence.end))
                              if c.location.existence.known else None,
                    "visit_min": c.visit_min, "accessible": c.accessible,
                    "paths_found": c.path_count,
                    "gathered": [{"id": _local(g), "label": L(g)} for g in c.gathered],
                    # every distinct path from each seed, best first: the first is the reason it is ranked
                    "explanations": [explain(p) for p in sorted((p for ps in c.paths.values() for p in ps), key=lambda p: -p.value)]
                                    or [explain(p) for p in sorted(c.best_paths.values(), key=lambda p: -p.value)],
                    # other reasons gathered into this stop: things held or staged here, or at the same spot
                    "reasons": [{"id": _local(r.place), "label": L(r.place), "score": round(r.score, 4),
                                 "explanation": explain(max(r.best_paths.values(), key=lambda p: p.value))}
                                for r in c.reasons]}

        return {
            "profile": {"uri": profile.uri, "seeds": [{"id": _local(s.iri), "label": L(s.iri), "weight": s.weight,
                                                       "source": _local(s.source), "link_confidence": s.link_confidence}
                                                      for s in profile.seeds],
                        "not_interested_in": sorted(L(x) for x in profile.not_interested_in)},
            "params": {"valuation": {k: sorted(v) if isinstance(v, (set, frozenset)) else v
                                     for k, v in asdict(self.valuation).items()
                                     if k not in ("relation_weights", "source_trust", "excluded_relations")},
                       "search": asdict(self.search), "tour": asdict(self.tour)},
            "stats": {**{k: v for k, v in result.stats.items() if k != "frontier_max"},
                      "frontier_max": {L(k): round(v, 6) for k, v in result.stats.get("frontier_max", {}).items()},
                      "retrieval_s": round(t1 - t0, 2), "compose_s": round(t2 - t1, 2),
                      "remote_queries": (self._remote_queries() or 0) - q0},
            "candidates_total": len(ranked),
            "candidates": [candidate(c) for c in shown],
            "tours": [{"value": round(t.value, 4), "order": t.order, "travel_min": round(t.travel_min),
                       "total_min": round(t.total_min),
                       "stops": [{"id": _local(s.place), "label": L(s.place), "score": round(s.score, 4)} for s in t.stops],
                       "transitions": [[L(n) for n in tr] for tr in t.transitions]} for t in tours],
        }

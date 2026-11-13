"""Step 1: every condition of NAMI (settings.NAMI, the profiles with two interests, the control scenario
and the second city), each into runs/nami/<condition>.json. A run already saved is kept (resume); one
that failed is redone. Time is measured with perf_counter, which does not count a sleeping machine."""

from __future__ import annotations

import json
import logging
import signal
import time
from datetime import datetime, timezone

from lib.suite_paths import CODE  # noqa: F401

import settings
from domain.models.user.profile import Seed, UserProfile
from lib import bench
from lib.pipeline import build, injected_layer

WD = "http://www.wikidata.org/entity/"
OUT = settings.RUNS / "nami"
PROFILE_LIMIT_S = 3600               # one interest taking more than an hour is recorded as an error and redone later
# the variants without specificity or provenance explore far more of the graph: an interest they cannot answer in
# five minutes (over four times the slowest of NAMI, 67 s) is recorded as not answered in time (2026-11-11)
LIMIT_S = {"C3_noprov": 300, "A1_no_eta": 300, "A2_no_decay": 300}


class TooLong(Exception):
    pass


def _too_long(signum, frame):
    raise TooLong("no answer within the time limit")


def profile(ids: list[str], uri: str, avoid: set[str] = frozenset()) -> UserProfile:
    return UserProfile(uri=uri, seeds=[Seed(WD + i) for i in ids], not_interested_in=set(avoid))


def run(name: str, use_case, profiles: list[tuple[str, list[str], set]], meta: dict) -> None:
    out = OUT / f"{name}.json"
    OUT.mkdir(parents=True, exist_ok=True)
    report = json.loads(out.read_text()) if out.exists() else {"condition": name, "meta": meta, "runs": []}
    report["runs"] = [r for r in report["runs"] if "error" not in r]
    limit = LIMIT_S.get(name, PROFILE_LIMIT_S)
    if name in LIMIT_S:                  # a timeout under this limit is the result, not an error to redo
        report["runs"] = json.loads(out.read_text())["runs"] if out.exists() else []
        report["meta"]["limit_s"] = limit
    done = {r["profile_id"] for r in report["runs"]}
    for pid, ids, avoid in profiles:
        if pid in done:
            continue
        t = time.perf_counter()
        signal.signal(signal.SIGALRM, _too_long)
        signal.alarm(limit)          # a request that hangs must not stop the whole run
        try:
            res = use_case.execute(profile(ids, f"urn:nami:user:{pid}", avoid))
            entry = {"profile_id": pid, "interests": ids, "avoid": sorted(avoid), **res}
        except Exception as ex:          # one failing profile must not lose the others
            entry = {"profile_id": pid, "interests": ids, "avoid": sorted(avoid), "error": f"{type(ex).__name__}: {ex}"}
        signal.alarm(0)
        entry["seconds"] = round(time.perf_counter() - t, 2)
        entry.pop("tours", None)
        report["runs"].append(entry)
        report["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
        print(f"  {name:18s} {pid:22s} {entry.get('candidates_total', 'ERR'):>5}  {entry['seconds']:8.1f}s", flush=True)


def single_profiles() -> list:
    return [(i["id"], [i.get("seed", i["id"])], set()) for i in bench.interests()]   # "seed": the item a user picks for the label


def conditions(only: list[str] | None = None, slow: bool = False) -> list[str]:
    names = list(settings.NAMI)            # pairs, avoid, injected source and second city dropped (2026-11-05)
    if settings.CITY2["ENABLED"]:
        names.append("Z2_wikidata")
    if slow:
        names += list(settings.NAMI_SLOW)
    return [n for n in names if not only or n in only]


def main(only: list[str] | None = None, slow: bool = False, interests: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING)
    singles = [p for p in single_profiles() if not interests or p[0] in interests]
    for name in conditions(only, slow):
        print(f"[{name}]", flush=True)
        if name in settings.NAMI or name in settings.NAMI_SLOW:
            sw = {**settings.NAMI, **settings.NAMI_SLOW}[name]
            run(name, build(settings.PARAMS, sw), singles, {"switches": sw, "params": settings.PARAMS})
        elif name == "M_pairs":
            profs = [(f"{a}+{b}", [a, b], set()) for a, b in settings.PAIRS]
            run(name, build(settings.PARAMS, {}), profs, {"pairs": settings.PAIRS})
        elif name == "M_avoid":
            profs = [(f"{i}-avoid", [i], {c}) for i, c in settings.AVOID]
            run(name, build(settings.PARAMS, {}), profs, {"avoid": settings.AVOID})
        elif name.startswith("C9_inject_t"):
            trust = float(name.rsplit("t", 1)[1])
            layer = settings.RUNS / "inject" / "injected.ttl"
            spec = settings.INJECT
            if not layer.exists():
                persons = [i["id"] for i in bench.interests() if i["kind"] == "person"] or [i["id"] for i in bench.interests()]
                places = {i["id"]: [s["qid"] for s in i["stops"] if s.get("qid")] for i in bench.interests()}
                facts = injected_layer(layer, persons, places, spec, settings.SEED)
                (layer.parent / "injected.json").write_text(json.dumps(facts, indent=1))
            facts = json.loads((layer.parent / "injected.json").read_text())
            persons = sorted({f["person"] for f in facts})
            profs = [p for p in single_profiles() if p[0] in persons]
            run(name, build(settings.PARAMS, {}, extra_layers=[layer], trust_prefixes={spec["source"]: trust}),
                profs, {"trust": trust, "facts": facts})
        elif name == "Z2_wikidata":
            f = settings.RUNS / "city2.json"
            if not f.exists():
                print("  second city: run `suite.py city2-check` first")
                continue
            c2 = json.loads(f.read_text())
            profs = [(i["id"], [i["id"]], set()) for i in c2["interests"]]
            run(name, build(settings.PARAMS, {"wikidata_only": True}, city2=c2), profs, {"city": c2})

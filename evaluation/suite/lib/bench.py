"""The benchmark: interests, stops, agreed stops (named by two or more sources), and the matching of a
place to a stop. A place matches a stop when it is the same Wikidata item, lies within it (Wikidata
containment or the stop's footprint), is within 50 m of it, or has the same normalised name (v2: models
often place a building tens of metres off or give no coordinate at all; BENCHMARK_NOTES 2026-10-16)."""

from __future__ import annotations

import json
import os
import re
import unicodedata

from lib.suite_paths import SRC  # noqa: F401

import settings
from evaluate_ground_truth import INSIDE, containers, match

RADIUS = 50.0
STOP_WORDS = {"the", "der", "die", "das", "zur", "zum", "im", "am", "in", "of", "und", "and", "st", "sankt", "zurich", "zuerich"}


def load() -> dict:
    return json.loads(settings.BENCHMARK.read_text())


def interests() -> list[dict]:
    """The scored interests; NAMI_SUITE_INTERESTS (comma-separated ids) restricts them, for a dry run."""
    only = [x for x in os.environ.get("NAMI_SUITE_INTERESTS", "").split(",") if x]
    return [i for i in load()["interests"] if i.get("scored") and (not only or i["id"] in only)]


def agreed(interest: dict) -> list[dict]:
    return [s for s in interest["stops"] if s.get("scored") and len({x["url"] for x in s.get("sources", [])}) >= 2]


def norm_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", (name or "").lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\(.*?\)", " ", s)
    words = [w for w in re.findall(r"[a-z0-9]+", s) if w not in STOP_WORDS]
    return " ".join(words)


def names_of(stop: dict) -> set[str]:
    out = {norm_name(stop.get("name", ""))}
    out |= {norm_name(a) for a in stop.get("alt_names", [])}
    return {n for n in out if len(n) >= 4}


def prepare(places: list[dict]) -> None:
    """Load the Wikidata containers of every place (cached by evaluate_ground_truth)."""
    INSIDE.update(containers({p["qid"] for p in places if p.get("qid")}))


def matches(stop: dict, place: dict) -> str | None:
    cand = {"id": place.get("qid") or "", "lat": place.get("lat"), "lon": place.get("lon"),
            "located_via": place.get("located_via")}
    if stop.get("lat") is not None:
        how = match(stop, cand, RADIUS)
        if how:
            return how
    else:                                  # a stop without coordinates: same item, or within it
        ids = {stop.get("qid"), *stop.get("alt_qids", [])} - {None}
        if cand["id"] in ids:
            return "qid"
        if ids & ({cand.get("located_via")} | set(INSIDE.get(cand["id"], []))):
            return "inside"
    n = norm_name(place.get("name", ""))
    if n and len(n) >= 4 and any(n == m or (len(m) >= 6 and (m in n or n in m)) for m in names_of(stop)):
        return "name"
    return None


def benchmark_urls(interest: dict) -> set[str]:
    return {x["url"].split("#")[0].rstrip("/") for s in interest["stops"] for x in s.get("sources", [])} | \
           {x["url"].split("#")[0].rstrip("/") for x in interest.get("sources_consulted", [])}

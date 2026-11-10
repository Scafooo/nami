"""Crawl the Zurich Tourism Open Data API (v2) and cache raw JSON to disk.

Two-phase: (1) GET with no `id` -> the full category tree; (2) GET with
`?id=<id>` for every category id -> POI arrays. Querying a parent
category returns the union of its descendants' POIs, so crawling every
node is redundant in principle, but cheap and a useful reachability
cross-check. This crawl's only real job is to enumerate distinct POIs;
per-POI category links are resolved later in transform.py."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import requests
from tqdm import tqdm

import config


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT, "Accept": "application/json"})
    return s


def _get_json(session: requests.Session, params: dict[str, str] | None = None) -> Any:
    resp = session.get(config.API_BASE, params=params, timeout=config.REQUEST_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


def fetch_category_tree(session: requests.Session, force: bool = False) -> list[dict]:
    if config.CATEGORIES_RAW_FILE.exists() and not force:
        return json.loads(config.CATEGORIES_RAW_FILE.read_text())
    tree = _get_json(session)
    config.CATEGORIES_RAW_FILE.write_text(json.dumps(tree, ensure_ascii=False, indent=2))
    return tree


def fetch_category_pois(session: requests.Session, category_id: str, force: bool = False) -> list[dict]:
    out_file = config.RAW_DIR / f"id_{category_id}.json"
    if out_file.exists() and not force:
        return json.loads(out_file.read_text())
    data = _get_json(session, {"id": category_id})
    if not isinstance(data, list):
        data = []
    out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    time.sleep(config.REQUEST_DELAY_SECONDS)
    return data


def is_poi(obj: dict) -> bool:
    """Distinguish a POI object from a (nested) category node, defensively."""
    return "identifier" in obj and "@type" in obj


def crawl_all(sample_ids: list[str] | None = None, force: bool = False) -> tuple[list[dict], dict[str, dict]]:
    """Crawl the category tree and every category's POIs.

    Returns (category_tree, pois_by_identifier).
    """
    session = _session()
    tree = fetch_category_tree(session, force=force)
    ids = sample_ids if sample_ids is not None else [c["id"] for c in tree]

    pois: dict[str, dict] = {}
    for cid in tqdm(ids, desc="Fetching categories"):
        try:
            items = fetch_category_pois(session, cid, force=force)
        except requests.HTTPError as exc:
            print(f"  ! category {cid}: HTTP error {exc}", file=sys.stderr)
            continue
        for obj in items:
            if is_poi(obj):
                pois[obj["identifier"]] = obj

    return tree, pois


if __name__ == "__main__":
    tree, pois = crawl_all()
    print(f"Categories: {len(tree)}")
    print(f"Unique POIs: {len(pois)}")

"""Fetching the sources, the same rule for every list: a page that is gone (404, 410, unknown host) fails, but a
failure that may pass (timeout, refused connection, 429, 5xx) is NOT cached: the reason stays "retry", is left
out of every rate, and is fetched again by the next `suite.py llm-process` / `suite.py lists`. Without this, an
outage on the day of the run would count as a reason that does not hold (as happened in v1 to the models).

Two caches, in the forms the checks of src/ read: process_rag (url -> text or None) for the models' quotes,
audit_explanations (url -> {"status", "text"}) for NAMI's sources."""

from __future__ import annotations

import re
import time

import requests

from lib.suite_paths import CODE  # noqa: F401

import config as code_config
from process_rag import response_text

RETRY = "retry"
_FAILED_NOW: set[str] = set()       # tried in this run and not readable: not tried again until the next run


def _get(url: str):
    if url in _FAILED_NOW:
        return RETRY, None
    try:
        r = requests.get(url, headers={"User-Agent": code_config.USER_AGENT}, timeout=30)
    except requests.exceptions.ConnectionError as ex:
        msg = str(ex).lower()
        if "resolve" in msg or "name or service" in msg or "nodename nor servname" in msg:
            return "gone_host", None
        _FAILED_NOW.add(url)
        return RETRY, None
    except requests.RequestException:
        _FAILED_NOW.add(url)
        return RETRY, None
    finally:
        time.sleep(1.0)
    if r.status_code == 429 or r.status_code >= 500:
        _FAILED_NOW.add(url)
        return RETRY, None
    return r.status_code, r


def for_quote(url: str, pages: dict) -> str | None:
    """Fills the process_rag cache; returns RETRY when the page could not be read for now."""
    if url in pages:
        return None
    status, r = _get(url)
    if status == RETRY:
        return RETRY
    pages[url] = response_text(r) if r is not None and r.ok else None
    return None


def for_source(url: str, cache: dict) -> str | None:
    """Fills the audit_explanations cache; returns RETRY when the source could not be read for now."""
    if url in cache:
        return None
    status, r = _get(url)
    if status == RETRY:
        return RETRY
    if status == "gone_host":
        cache[url] = {"status": "unknown host", "text": None}
    else:
        text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", r.text)) if r.ok else None
        cache[url] = {"status": r.status_code, "text": text}
    return None

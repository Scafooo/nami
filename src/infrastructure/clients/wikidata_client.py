"""WikidataClient: resilient access to live Wikidata -- everything needed
to survive a federated SPARQL SERVICE call to a third-party endpoint we
don't control (retries, a hard wall-clock timeout, partial-result
recovery, batching). Not part of BuildQuery/ToQuery itself, just the
plumbing to run it against a live, occasionally flaky remote endpoint."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import defaultdict

import requests

import config
from domain.models.knowledge_graph.query import Query
from domain.models.poi.image import Image
from domain.models.poi.multilingual_text import MultilingualText

logger = logging.getLogger(__name__)

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"


class WikidataClient:
    """Configuration + behaviour for talking to a live Wikidata SPARQL
    endpoint. Holds no query-specific state -- one instance is reused
    across every query of a run."""

    def __init__(
        self,
        endpoint: str = WIKIDATA_ENDPOINT,
        user_agent: str | None = None,
        retry_delays_s: tuple[float, ...] = (5, 15),
        hard_timeout_s: float = 90,  # pyoxigraph exposes no HTTP timeout for SERVICE calls
        label_languages: tuple[str, ...] = ("en", "de", "mul"),
        label_batch_size: int = 300,
        # Real, specific institutions sit well under this; generic
        # classificatory/administrative hubs sit well over it. A path
        # whose only route to a candidate passes through a node this
        # connected isn't "related to the interest" in any specific sense.
        hub_degree_threshold: int = 50_000,
    ):
        self.endpoint = endpoint
        self.user_agent = user_agent or config.USER_AGENT
        self.retry_delays_s = retry_delays_s
        self.hard_timeout_s = hard_timeout_s
        self.label_languages = label_languages
        self.label_batch_size = label_batch_size
        self.hub_degree_threshold = hub_degree_threshold

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self.user_agent, "Accept": "application/sparql-results+json"}

    def _post(self, query: str, timeout: float) -> requests.Response:
        """POSTs `query`, retrying a timeout or connection failure --
        callers separately retry a bad HTTP status."""
        for delay in (*self.retry_delays_s, None):
            try:
                return requests.post(
                    self.endpoint, data={"query": query, "format": "json"}, headers=self._headers(), timeout=timeout
                )
            except requests.RequestException as ex:
                if delay is None:
                    raise
                logger.warning("Wikidata request failed (%s); retrying in %ss", type(ex).__name__, delay)
                time.sleep(delay)

    @staticmethod
    def _collect_partial(query_result) -> list[dict]:
        """Consumes a query result row by row so a mid-stream failure
        (Wikidata serializing one malformed term) loses only the rows
        after it, not the whole result. Converts each row to a plain dict
        immediately: pyoxigraph's result/term objects aren't safe to
        create on one thread and drop on another, which
        query_with_retry's hard-timeout wrapper would otherwise do."""
        var_names = [v.value for v in query_result.variables]
        rows = []
        it = iter(query_result)
        while True:
            try:
                row = next(it)
            except StopIteration:
                break
            except RuntimeError as ex:
                logger.warning("query result stream truncated after %d row(s): %s", len(rows), ex)
                break
            rows.append({name: (row[name].value if row[name] is not None else None) for name in var_names})
        return rows

    def _run_query(self, graph, query: Query, out: "queue.Queue") -> None:
        try:
            out.put(("ok", self._collect_partial(graph.query(query))))
        except Exception as ex:  # noqa: BLE001 -- forwarded to the waiting thread, not swallowed
            # An exception's __traceback__ keeps pyoxigraph objects alive
            # across the thread boundary just like a raw result would; a
            # freshly constructed one carries no traceback.
            cls = OSError if isinstance(ex, OSError) else RuntimeError
            out.put(("error", cls(f"{type(ex).__name__}: {ex}")))

    def query_with_retry(self, graph, query: Query) -> list:
        """Runs `query` against `graph` (anything with a `.query(Query)`
        method) with a hard wall-clock timeout and retries on network
        failure. Raises OSError if every attempt fails."""
        for attempt, delay in enumerate((*self.retry_delays_s, None)):
            result_queue: queue.Queue = queue.Queue()
            thread = threading.Thread(target=self._run_query, args=(graph, query, result_queue), daemon=True)
            thread.start()
            thread.join(timeout=self.hard_timeout_s)
            if thread.is_alive():
                # A genuinely hung connection: pyoxigraph gives no way to
                # cancel a SERVICE call in flight, so the thread is
                # abandoned (daemon, won't block process exit) and this
                # query is treated as failed like any other network fault.
                err = OSError(f"query exceeded {self.hard_timeout_s}s with no response (hung connection, abandoned)")
            else:
                status, payload = result_queue.get()
                if status == "ok":
                    return payload
                err = payload
            if not isinstance(err, OSError) or delay is None:
                raise err
            time.sleep(delay)

    def is_hub(self, qid: str, threshold: int | None = None) -> bool:
        """Cheap, bounded check: does `qid` have more than `threshold`
        edges (incoming or outgoing) on Wikidata? LIMIT caps the work even
        for a true hub with millions of edges -- no full count needed,
        just whether more than `threshold` rows exist."""
        threshold = self.hub_degree_threshold if threshold is None else threshold
        query = (
            "PREFIX wd: <http://www.wikidata.org/entity/>\n"
            f"SELECT * WHERE {{ {{ ?s ?p wd:{qid} }} UNION {{ wd:{qid} ?p ?o }} }} LIMIT {threshold + 1}"
        )
        # A true hub's response, capped at threshold+1 rows, is still
        # tens of megabytes -- resp.ok isn't enough to catch a
        # truncated/corrupted body, so JSON decoding is retried too.
        for attempt, delay in enumerate((*self.retry_delays_s, None)):
            resp = self._post(query, timeout=60)
            if not resp.ok:
                if delay is None:
                    resp.raise_for_status()
                time.sleep(delay)
                continue
            try:
                return len(resp.json()["results"]["bindings"]) > threshold
            except ValueError:
                if delay is None:
                    raise
                time.sleep(delay)

    def resolve_labels(self, ids: set[str]) -> dict[str, str]:
        """Batch-fetch a label for a set of bare Wikidata IDs (QIDs and/or
        PIDs), preferring English, then German, then Wikidata's
        language-neutral "mul" label -- many person names now carry only
        that one."""
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        langs = ", ".join(f'"{lang}"' for lang in self.label_languages)
        by_lang: dict[str, dict[str, str]] = {lang: {} for lang in self.label_languages}
        # A single VALUES request across all ids hit a real 414 URI Too
        # Long -- POST (body, not URL) plus chunking keeps requests small.
        for batch_start in range(0, len(ids), self.label_batch_size):
            batch = ids[batch_start : batch_start + self.label_batch_size]
            values = " ".join(f"wd:{i}" for i in batch)
            query = (
                "PREFIX wd: <http://www.wikidata.org/entity/>\n"
                "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
                f"SELECT ?id ?label WHERE {{ VALUES ?id {{ {values} }} "
                f"?id rdfs:label ?label . FILTER(LANG(?label) IN ({langs})) }}"
            )
            # Retried like every other live call: a transient 502/504
            # here must not throw away query results already gathered.
            for attempt, delay in enumerate((*self.retry_delays_s, None)):
                resp = self._post(query, timeout=30)
                if resp.ok or delay is None:
                    break
                time.sleep(delay)
            resp.raise_for_status()
            for row in resp.json()["results"]["bindings"]:
                qid = row["id"]["value"].rsplit("/", 1)[-1]
                by_lang[row["label"]["xml:lang"]][qid] = row["label"]["value"]
        out = {}
        for qid in ids:
            for lang in self.label_languages:
                if qid in by_lang[lang]:
                    out[qid] = by_lang[lang][qid]
                    break
        return out

    def resolve_locations(self, ids: set[str]) -> dict[str, str]:
        """Batch-fetch the "location" (P276) of Wikidata items -- id -> the
        location's id, only for items that have one (the smallest id if
        several)."""
        ids = sorted({i for i in ids if i})
        out: dict[str, str] = {}
        for batch_start in range(0, len(ids), self.label_batch_size):
            batch = ids[batch_start : batch_start + self.label_batch_size]
            values = " ".join(f"wd:{i}" for i in batch)
            query = (
                "PREFIX wd: <http://www.wikidata.org/entity/>\n"
                "PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
                f"SELECT ?id ?loc WHERE {{ VALUES ?id {{ {values} }} ?id wdt:P276 ?loc }}"
            )
            for attempt, delay in enumerate((*self.retry_delays_s, None)):
                resp = self._post(query, timeout=30)
                if resp.ok or delay is None:
                    break
                time.sleep(delay)
            resp.raise_for_status()
            for row in resp.json()["results"]["bindings"]:
                qid = row["id"]["value"].rsplit("/", 1)[-1]
                loc = row["loc"]["value"].rsplit("/", 1)[-1]
                if qid not in out or loc < out[qid]:
                    out[qid] = loc
        return out

    def resolve_descriptions(self, ids: set[str]) -> dict[str, MultilingualText]:
        """Batch-fetch a Wikidata item's own short description
        (schema:description -- at http://schema.org/, not https://, unlike
        this project's own local catalogue) for a set of bare QIDs, in
        every language self.label_languages covers. Unlike resolve_labels,
        every language found is kept, not just the first match."""
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        langs = ", ".join(f'"{lang}"' for lang in self.label_languages)
        by_id: dict[str, dict[str, str]] = defaultdict(dict)
        for batch_start in range(0, len(ids), self.label_batch_size):
            batch = ids[batch_start : batch_start + self.label_batch_size]
            values = " ".join(f"wd:{i}" for i in batch)
            query = (
                "PREFIX wd: <http://www.wikidata.org/entity/>\n"
                "PREFIX schema: <http://schema.org/>\n"
                f"SELECT ?id ?desc WHERE {{ VALUES ?id {{ {values} }} "
                f"?id schema:description ?desc . FILTER(LANG(?desc) IN ({langs})) }}"
            )
            for attempt, delay in enumerate((*self.retry_delays_s, None)):
                resp = self._post(query, timeout=30)
                if resp.ok or delay is None:
                    break
                time.sleep(delay)
            resp.raise_for_status()
            for row in resp.json()["results"]["bindings"]:
                qid = row["id"]["value"].rsplit("/", 1)[-1]
                by_id[qid][row["desc"]["xml:lang"]] = row["desc"]["value"]
        return {qid: MultilingualText(by_language=by_lang) for qid, by_lang in by_id.items()}

    def resolve_images(self, ids: set[str]) -> dict[str, list[Image]]:
        """Batch-fetch a Wikidata item's own image (wdt:P18) for a set of
        bare QIDs -- the endpoint already resolves this to a ready-to-use
        Special:FilePath url. No caption: P18 carries none."""
        ids = sorted({i for i in ids if i})
        if not ids:
            return {}
        result: dict[str, list[Image]] = defaultdict(list)
        for batch_start in range(0, len(ids), self.label_batch_size):
            batch = ids[batch_start : batch_start + self.label_batch_size]
            values = " ".join(f"wd:{i}" for i in batch)
            query = (
                "PREFIX wd: <http://www.wikidata.org/entity/>\n"
                "PREFIX wdt: <http://www.wikidata.org/prop/direct/>\n"
                f"SELECT ?id ?image WHERE {{ VALUES ?id {{ {values} }} ?id wdt:P18 ?image }}"
            )
            for attempt, delay in enumerate((*self.retry_delays_s, None)):
                resp = self._post(query, timeout=30)
                if resp.ok or delay is None:
                    break
                time.sleep(delay)
            resp.raise_for_status()
            for row in resp.json()["results"]["bindings"]:
                qid = row["id"]["value"].rsplit("/", 1)[-1]
                result[qid].append(Image(url=row["image"]["value"]))
        return dict(result)

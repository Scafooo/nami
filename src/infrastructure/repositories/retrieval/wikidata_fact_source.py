"""WikidataFactSource: G_P as read from live Wikidata, plus the local
Zurich Tourism catalogue for places Wikidata does not locate.

Wikidata's statement model already carries what our RDF-star annotations
express, so it is read as such:
    rank            -> confidence (preferred 1.0, normal 0.9; deprecated not
                       read), raised by further independent references
                       (noisy-or) and halved if the statement is disputed
    reference P248  -> source (the item a statement is "stated in")
    P580/P582/P585  -> validity interval (start, end, point in time)
    P3831/P2868/P39/P512 -> role (object/subject has role, position, degree)
Places also state when they existed (P571 inception, P576 dissolved or
demolished, P3999 closure), and whether they are accessible (P2846).
Only item-valued properties are traversed (external identifiers and
literals connect nothing), and instance-of/subclass-of edges are not
followed backwards: a class can have millions of instances, which
class_members() fetches directly, restricted to the area."""

from __future__ import annotations

import logging
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

import config
from domain.models.retrieval.fact import Fact, Interval
from domain.repositories.retrieval.fact_source import FactSource, Location

logger = logging.getLogger(__name__)

WD = "http://www.wikidata.org/entity/"
WDT = "http://www.wikidata.org/prop/direct/"
UNREFERENCED = "urn:nami:source:wikidata-unreferenced"
REFERENCED = "urn:nami:source:wikidata-referenced"
PREFIXES = """PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX pr: <http://www.wikidata.org/prop/reference/>
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX bd: <http://www.bigdata.com/rdf#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX hint: <http://www.bigdata.com/queryHints#>
"""
_POINT = re.compile(r"Point\(([-0-9.eE]+) ([-0-9.eE]+)\)")


def qid(iri: str) -> str:
    return iri.rsplit("/", 1)[-1]


def _date(value: str | None) -> date | None:
    if not value:
        return None
    m = re.match(r"^\+?(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return None
    y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, max(mo, 1), max(d, 1))
    except ValueError:
        return None



def _metres(a, b) -> float:
    from math import asin, cos, radians, sin, sqrt
    dlat, dlon = radians(b[0] - a[0]), radians(b[1] - a[1])
    h = sin(dlat / 2) ** 2 + cos(radians(a[0])) * cos(radians(b[0])) * sin(dlon / 2) ** 2
    return 2 * 6_371_000 * asin(sqrt(h))


def _overpass_curl(url: str, query: str):
    """The elements of an Overpass answer fetched with curl, or None if it fails."""
    import json as _json
    import subprocess
    try:
        out = subprocess.run(["curl", "-s", "--max-time", "150", "-A", config.USER_AGENT, "--data-urlencode",
                              f"data={query}", url], capture_output=True, timeout=180, check=True).stdout
        return _json.loads(out).get("elements", [])
    except (subprocess.SubprocessError, ValueError, OSError):
        return None

class WikidataFactSource(FactSource):
    def __init__(
        self,
        endpoint: str = "https://query.wikidata.org/sparql",
        bbox: tuple[float, float, float, float] = config.ZURICH_BBOX,
        area: str = WD + "Q72",
        area_subdivision_classes: tuple[str, ...] = (WD + "Q19644409",),
        local_repository=None,            # LocalPOIRepository, for catalogue-only locations
        degree_cap: int = 100_000,
        batch: int = 60,
        rows_limit: int = 20_000,
        workers: int = 6,
        locate_batch: int = 250,
        timeout_s: float = 90,
    ):
        self.endpoint = endpoint
        self.west, self.south, self.east, self.north = bbox
        self.area = area
        self.subdivision_classes = area_subdivision_classes
        self.local = local_repository
        self.osm_places: dict[str, tuple[float, float, str]] = {}   # OSM places met as concept members
        self.degree_cap = degree_cap
        self.batch = batch
        self.rows_limit = rows_limit
        self.workers = workers
        self.locate_batch = locate_batch
        self.timeout_s = timeout_s
        self.queries = 0
        self.failed = 0
        self._degree_cache: dict[str, int] = {}

    # -- plumbing -----------------------------------------------------------------
    def _select(self, query: str, attempts: int = 12, timeout_s: float | None = None) -> list[dict]:
        """Rows of a SPARQL query. A throttled or failed request is retried, honouring
        Retry-After; if Wikidata stays unavailable the query raises rather than return
        no rows, which the cache would otherwise record as a node without facts."""
        headers = {"User-Agent": config.USER_AGENT, "Accept": "application/sparql-results+json"}
        err = None
        for attempt in range(attempts):
            self.queries += 1
            try:
                r = requests.post(self.endpoint, data={"query": PREFIXES + query}, headers=headers,
                                  timeout=timeout_s or self.timeout_s)
                if r.ok:
                    return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]
                err = f"HTTP {r.status_code}"
                wait = int(r.headers.get("retry-after", 0) or 0)
            except requests.Timeout:
                if timeout_s:              # a caller that bounds the time wants an answer now, not a retry
                    raise TimeoutError(f"no answer in {timeout_s} s")
                err, wait = "Timeout", 0
            except (requests.RequestException, ValueError) as ex:
                err, wait = type(ex).__name__, 0
            time.sleep(wait or min(3 * 2 ** attempt, 120))
        self.failed += 1
        raise RuntimeError(f"Wikidata unavailable ({err})")

    def _chunks(self, items):
        items = sorted(items)
        return [items[i:i + self.batch] for i in range(0, len(items), self.batch)]

    @staticmethod
    def _values(nodes) -> str:
        return " ".join(f"<{n}>" for n in nodes)

    def _in_box(self, lon: float, lat: float) -> bool:
        return self.west <= lon <= self.east and self.south <= lat <= self.north

    # -- FactSource ---------------------------------------------------------------
    def expand(self, nodes: set[str]) -> dict[str, list[Fact]]:
        nodes = {n for n in nodes if n.startswith(WD)}
        out: dict[str, list[Fact]] = {n: [] for n in nodes}
        for chunk in self._chunks(nodes):
            for direction in ("fwd", "bwd"):
                pattern = "?n ?p ?st . ?st ?ps ?m ." if direction == "fwd" else "?m ?p ?st . ?st ?ps ?n ."
                no_class_fanout = "" if direction == "fwd" else "FILTER(?prop NOT IN (wd:P31, wd:P279))"
                rows = self._select(f"""
SELECT ?n ?prop ?m ?rank ?start ?end ?time (SAMPLE(?ref) AS ?src) (SAMPLE(?url) AS ?srcUrl)
       (SAMPLE(?st) AS ?stmt) (SAMPLE(?role) AS ?rl)
       (COUNT(DISTINCT ?rn) AS ?nref) (SAMPLE(?disp) AS ?disputed) WHERE {{
  VALUES ?n {{ {self._values(chunk)} }}
  {pattern}
  ?prop wikibase:claim ?p ; wikibase:statementProperty ?ps ; wikibase:propertyType wikibase:WikibaseItem .
  {no_class_fanout}
  FILTER(isIRI(?m) && STRSTARTS(STR(?m), "{WD}Q"))
  ?st wikibase:rank ?rank . FILTER(?rank != wikibase:DeprecatedRank)
  OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }} OPTIONAL {{ ?st pq:P585 ?time }}
  OPTIONAL {{ ?st prov:wasDerivedFrom ?rn . OPTIONAL {{ ?rn pr:P248 ?ref }} OPTIONAL {{ ?rn pr:P854 ?url }} }}
  OPTIONAL {{ ?st pq:P3831|pq:P2868|pq:P39|pq:P512 ?role . FILTER(isIRI(?role)) }}
  OPTIONAL {{ ?st pq:P1310 ?disp }}
}} GROUP BY ?n ?prop ?m ?rank ?start ?end ?time LIMIT {self.rows_limit}""")
                if len(rows) >= self.rows_limit:
                    logger.warning("expansion truncated at %d rows (%s)", self.rows_limit, direction)
                for row in rows:
                    n, m = row["n"], row["m"]
                    start, end, point = _date(row.get("start")), _date(row.get("end")), _date(row.get("time"))
                    validity = Interval(start or point, end or point)
                    s, o = (n, m) if direction == "fwd" else (m, n)
                    base = 1.0 if row["rank"].endswith("PreferredRank") else 0.9
                    nref = int(row.get("nref") or 0)
                    conf = 1.0 - (1.0 - base) ** max(nref, 1)   # independent references, noisy-or
                    if row.get("disputed"):
                        conf *= 0.5
                    out[n].append(Fact(
                        subject=s, predicate=WDT + qid(row["prop"]), object=o,
                        confidence=round(conf, 6),
                        # Every fact has a source: its "stated in" item or reference URL,
                        # else the statement itself, whose trust is set by prefix.
                        source=row.get("src") or row.get("srcUrl") or row.get("stmt") or UNREFERENCED,
                        validity=validity,
                        role=row.get("rl"),
                    ))
        return out

    def degree(self, nodes: set[str]) -> dict[str, int]:
        todo = [n for n in nodes if n not in self._degree_cache and n.startswith(WD)]

        def count(n):
            try:
                # throttling (HTTP 429) is waited out, as many times as Wikidata asks
                rows = self._select(f"SELECT (COUNT(*) AS ?c) WHERE {{ SELECT ?s WHERE {{ ?s ?p <{n}> }} LIMIT {self.degree_cap} }}",
                                    attempts=30, timeout_s=20)
            except TimeoutError:
                # only a node whose incoming links Wikidata cannot count in 20 s is taken as a hub: it gets the cap
                logger.warning("degree of %s not countable in time: taken as %d", n, self.degree_cap)
                return n, self.degree_cap
            return n, int(rows[0]["c"]) if rows else None

        with ThreadPoolExecutor(2) as pool:       # counts are costly for the service: two at a time
            for n, c in pool.map(count, todo):
                if c is not None:
                    self._degree_cache[n] = c
        return {n: self._degree_cache[n] for n in nodes if n in self._degree_cache}

    def _not_area(self, nodes) -> set[str]:
        """Nodes that are neither the area itself nor one of its administrative subdivisions."""
        nodes = set(nodes) - {self.area}
        bad: set[str] = set()
        for chunk in self._chunks(nodes):
            rows = self._select(f"""SELECT ?n WHERE {{ VALUES ?n {{ {self._values(chunk)} }}
  VALUES ?c {{ {self._values(self.subdivision_classes)} }} ?n wdt:P31 ?c }}""")
            bad |= {r["n"] for r in rows}
        return nodes - bad

    def _parallel(self, queries: list[str]) -> list[list[dict]]:
        with ThreadPoolExecutor(self.workers) as pool:
            return list(pool.map(self._select, queries))

    def locate(self, nodes: set[str]) -> dict[str, Location]:
        wd_nodes = sorted(n for n in nodes if n.startswith(WD))
        chunks = [wd_nodes[i:i + self.locate_batch] for i in range(0, len(wd_nodes), self.locate_batch)]
        found: dict[str, Location] = {}
        own_coord: set[str] = set()
        for rows in self._parallel([f"SELECT ?n ?c WHERE {{ VALUES ?n {{ {self._values(c)} }} ?n wdt:P625 ?c }}"
                                    for c in chunks]):
            for r in rows:
                own_coord.add(r["n"])
                m = _POINT.search(r["c"])
                if m and self._in_box(float(m.group(1)), float(m.group(2))) and r["n"] not in found:
                    found[r["n"]] = Location(lat=float(m.group(2)), lon=float(m.group(1)))
        # An event is a reason of the place where it happened, not a stop of its own: when it names
        # its location (P276) in the area, it is located there, via that place.
        for rows in self._parallel([f"""SELECT ?n ?loc ?c WHERE {{ VALUES ?n {{ {self._values(c)} }}
  ?n wdt:P276 ?loc . ?loc wdt:P625 ?c .
  ?n wdt:P31 ?k . ?k wdt:P279* wd:Q1190554 . hint:Prior hint:gearing "forward" . }}"""
                                    for c in chunks]):
            for r in rows:
                m = _POINT.search(r["c"])
                if m and self._in_box(float(m.group(1)), float(m.group(2))) and r["loc"] != self.area:
                    found[r["n"]] = Location(lat=float(m.group(2)), lon=float(m.group(1)), via=r["loc"])
        rest = sorted(set(wd_nodes) - own_coord)
        chunks = [rest[i:i + self.locate_batch] for i in range(0, len(rest), self.locate_batch)]
        excluded = self._values((self.area,))
        subdiv = self._values(self.subdivision_classes)
        for rows in self._parallel([f"""SELECT ?n ?cont ?c WHERE {{ VALUES ?n {{ {self._values(c)} }}
  ?n wdt:P131|wdt:P276|wdt:P361 ?cont . ?cont wdt:P625 ?c .
  FILTER(?cont NOT IN ({excluded}))
  FILTER NOT EXISTS {{ ?cont wdt:P31/wdt:P279* wd:Q56061 }} }}""" for c in chunks]):
            for r in rows:
                m = _POINT.search(r["c"])
                if m and self._in_box(float(m.group(1)), float(m.group(2))) and r["n"] not in found:
                    found[r["n"]] = Location(lat=float(m.group(2)), lon=float(m.group(1)), via=r["cont"])
        if self.local is not None:
            missing = {qid(n) for n in set(rest) - set(found)}
            if missing:
                twins = self.local.find_twins(missing)
                coords = self.local.find_coordinates(set(twins.values()))
                for wid, local_uri in twins.items():
                    if local_uri in coords:
                        lon, lat = coords[local_uri]
                        if self._in_box(lon, lat):
                            found.setdefault(WD + wid, Location(lat=lat, lon=lon, via=local_uri))
        keep = self._not_area(found)
        found = {n: loc for n, loc in found.items() if n in keep}
        existence = self._existence(set(found))
        return {n: (Location(loc.lat, loc.lon, loc.via, existence[n]) if n in existence else loc)
                for n, loc in found.items()}

    def _existence(self, nodes: set[str]) -> dict[str, Interval]:
        out: dict[str, Interval] = {}
        for chunk in self._chunks(nodes):
            rows = self._select(f"""SELECT ?n (MIN(?b) AS ?from) (MAX(?e) AS ?to) WHERE {{
  VALUES ?n {{ {self._values(chunk)} }}
  OPTIONAL {{ ?n wdt:P571 ?b }} OPTIONAL {{ ?n wdt:P576|wdt:P3999 ?e }} }} GROUP BY ?n""")
            for r in rows:
                iv = Interval(_date(r.get("from")), _date(r.get("to")))
                if iv.known:
                    out[r["n"]] = iv
        return out

    def describe(self, places: set[str], durations: dict[str, float] | None = None) -> dict:
        from domain.repositories.retrieval.fact_source import PlaceInfo
        durations = durations if durations is not None else getattr(self, "durations", {})
        visit: dict[str, float] = {}
        access: dict[str, bool] = {}
        nodes = {n for n in places if n.startswith(WD)}
        if durations:
            classes = self._values(durations)
            for chunk in self._chunks(nodes):
                for r in self._select(f"""SELECT DISTINCT ?n ?c WHERE {{ VALUES ?n {{ {self._values(chunk)} }}
  VALUES ?c {{ {classes} }} ?n wdt:P31/wdt:P279* ?c }}"""):
                    visit[r["n"]] = max(visit.get(r["n"], 0), durations[r["c"]])
        for chunk in self._chunks(nodes):
            for r in self._select(f"SELECT ?n ?a WHERE {{ VALUES ?n {{ {self._values(chunk)} }} ?n wdt:P2846 ?a }}"):
                if r["a"].endswith("Q24192069"):
                    access[r["n"]] = False
                elif r["a"].endswith(("Q24192067", "Q24192068")):
                    access.setdefault(r["n"], True)
        return {n: PlaceInfo(visit.get(n), access.get(n)) for n in places if n in visit or n in access}

    def instances_of(self, nodes: set[str], classes: set[str]) -> set[str]:
        hits = {n for n in nodes if n in classes}
        cls = [c for c in classes if c.startswith(WD)]
        if not cls:
            return hits
        for chunk in self._chunks({n for n in nodes if n.startswith(WD)} - hits):
            rows = self._select(f"""SELECT DISTINCT ?n WHERE {{ VALUES ?n {{ {self._values(chunk)} }}
  VALUES ?c {{ {self._values(cls)} }} ?n wdt:P31/wdt:P279* ?c }}""")
            hits |= {r["n"] for r in rows}
        return hits

    TOPICAL = ("P1269", "P527", "P921", "P2578", "P1056", "P533", "P2283")   # facet of, has part, main subject, studies, product, target (witch hunt -> witch), uses (drug policy -> harm reduction)

    def topical(self, node: str) -> list[str]:
        if not node.startswith(WD):
            return []
        props = "|".join("wdt:" + p for p in self.TOPICAL if p != "P527")
        # ... a part (P527) only if it acknowledges the whole (part of, facet of, subclass of): a
        # generic component ("killing" as a part of "witch hunt") brings places unrelated to the theme;
        # and, for "history of X", X itself (X has P2184 "history of topic" = the theme)
        rows = self._select(f"""SELECT DISTINCT ?c WHERE {{ {{ <{node}> {props} ?c }}
  UNION {{ <{node}> wdt:P527 ?c . ?c wdt:P361|wdt:P1269|wdt:P279 <{node}> }}
  UNION {{ ?c wdt:P2184 <{node}> }}
  FILTER(isIRI(?c)) }}""")
        return [r["c"] for r in rows if r["c"].startswith(WD + "Q")]

    def area_members(self, concept: str) -> list[Fact]:
        if not concept.startswith(WD):
            return []
        rows = self._select(f"""SELECT DISTINCT ?x ?p WHERE {{
  SERVICE wikibase:box {{ ?x wdt:P625 ?c .
    bd:serviceParam wikibase:cornerSouthWest "Point({self.west} {self.south})"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point({self.east} {self.north})"^^geo:wktLiteral . }}
  {{ ?x wdt:P31/wdt:P279* <{concept}> . BIND(wdt:P31 AS ?p) }}
  UNION {{ ?x ?p <{concept}> . FILTER(STRSTARTS(STR(?p), "{WDT}")) }}
}} LIMIT 2000""")
        facts = [Fact(subject=r["x"], predicate=WDT + qid(r["p"]), object=concept, confidence=1.0) for r in rows]
        return facts + self._osm_members(concept)

    OSM_NODE = "https://example.org/nami/data/plaque/osm_"
    OVERPASS = "https://overpass-api.de/api/interpreter"

    def _osm_members(self, concept: str) -> list[Fact]:
        """Places of the area that OpenStreetMap tags with the tag Wikidata gives for the concept
        (P1282, "OpenStreetMap tag or key"): e.g. labor union -> office=union. Their point and name
        are kept in self.osm_places for locating and labelling."""
        tags = [r["t"] for r in self._select(f"SELECT ?t WHERE {{ <{concept}> wdt:P1282 ?t }}")]
        facts = []
        for t in tags:
            m = re.match(r"^(?:Tag:)?([^=]+)=(.+)$", t)
            if not m or t.startswith("Key:"):
                continue
            k, v = m.group(1), m.group(2)
            q = (f'[out:json][timeout:90];nwr["{k}"="{v}"]({self.south},{self.west},{self.north},{self.east});'
                 f'out center tags;')
            elements = None
            for attempt in range(6):
                try:
                    r = requests.post(self.OVERPASS, data={"data": q}, timeout=120,
                                      headers={"User-Agent": config.USER_AGENT})
                    if r.ok:
                        elements = r.json().get("elements", [])
                        break
                    wait = int(r.headers.get("retry-after", 0) or 0)
                except requests.exceptions.SSLError:
                    # the Python of the evaluation machine (LibreSSL 2.8) cannot negotiate TLS with Overpass:
                    # the same request through curl, which uses the system's TLS
                    elements = _overpass_curl(self.OVERPASS, q)
                    if elements is not None:
                        break
                    wait = 0
                except (requests.RequestException, ValueError):
                    wait = 0
                time.sleep(wait or min(10 * 2 ** attempt, 120))
            if elements is None:
                # as in _select: never let a failed request be cached as "no members"
                raise RuntimeError(f"OpenStreetMap Overpass unavailable for {k}={v}")
            for e in elements:
                lat, lon = (e["lat"], e["lon"]) if "lat" in e else (e["center"]["lat"], e["center"]["lon"])
                node = f"{self.OSM_NODE}{e['type']}_{e['id']}"
                tags_e = e.get("tags", {})
                if tags_e.get("wikidata", "").startswith("Q"):
                    node = WD + tags_e["wikidata"]
                self.osm_places[node] = (lat, lon, tags_e.get("name") or v.replace("_", " "))
                facts.append(Fact(subject=node, predicate=WDT + "P31", object=concept, confidence=0.9,
                                  source=f"https://www.openstreetmap.org/{e['type']}/{e['id']}"))
        return facts

    LINKS = config.RAW_DIR / "link_status.json"

    def _link_state(self, url: str) -> tuple[bool, str | None]:
        """(the page opens, an archived copy when it does not), checked once and cached."""
        if not hasattr(self, "_links"):
            self._links = json.loads(self.LINKS.read_text()) if self.LINKS.exists() else {}
        if url not in self._links:
            alive, archive = False, None
            try:
                r = requests.get(url, timeout=30, headers={"User-Agent": config.USER_AGENT})
                alive = r.ok
            except requests.RequestException:
                alive = False
            time.sleep(1.0)
            if not alive:
                try:
                    r = requests.get("https://archive.org/wayback/available", params={"url": url}, timeout=30,
                                     headers={"User-Agent": config.USER_AGENT})
                    snap = (r.json().get("archived_snapshots") or {}).get("closest") if r.ok else None
                    archive = snap["url"] if snap and snap.get("available") else None
                except (requests.RequestException, ValueError):
                    archive = None
                time.sleep(1.0)
            self._links[url] = {"alive": alive, "archive": archive}
            self.LINKS.write_text(json.dumps(self._links, indent=0))
        v = self._links[url]
        return v["alive"], v["archive"]

    CONTAINER_M = 500.0          # a container farther than this is not one a place stands on or within

    def containers(self, nodes: set[str]) -> dict[str, str]:
        wd_nodes = sorted(n for n in nodes if n.startswith(WD))
        chunks = [wd_nodes[i:i + self.locate_batch] for i in range(0, len(wd_nodes), self.locate_batch)]
        excluded = self._values((self.area,))
        out: dict[str, str] = {}
        for rows in self._parallel([f"""SELECT ?n ?c ?cont ?cc WHERE {{ VALUES ?n {{ {self._values(c)} }}
  ?n wdt:P625 ?c . ?n wdt:P276 ?cont . ?cont wdt:P625 ?cc .
  FILTER(?cont NOT IN ({excluded}) && ?cont != ?n)
  FILTER NOT EXISTS {{ ?cont wdt:P31/wdt:P279* wd:Q56061 }}
  FILTER NOT EXISTS {{ ?cont wdt:P31/wdt:P279* wd:Q43229 }} }}""" for c in chunks]):
            for r in rows:
                a, b = _POINT.search(r["c"]), _POINT.search(r["cc"])
                if not (a and b):
                    continue
                d = _metres((float(a.group(2)), float(a.group(1))), (float(b.group(2)), float(b.group(1))))
                if d <= self.CONTAINER_M and r["n"] not in out:
                    out[r["n"]] = r["cont"]
        return out

    def inverse_properties(self, predicates: set[str]) -> dict[str, str]:
        direct = "http://www.wikidata.org/prop/direct/"
        props = sorted({p.removeprefix(direct) for p in predicates if p.startswith(direct)})
        out: dict[str, str] = {}
        for k in range(0, len(props), 200):
            vals = " ".join(f"wd:{p}" for p in props[k:k + 200])
            for r in self._select(f"SELECT ?p ?q WHERE {{ VALUES ?p {{ {vals} }} ?p wdt:P1696 ?q }}"):
                out[direct + r["p"].rsplit("/", 1)[-1]] = direct + r["q"].rsplit("/", 1)[-1]
        return out

    def references(self, triples: list[tuple[str, str, str]]) -> dict[tuple[str, str, str], dict]:
        """For explanations: what a Wikidata statement rests on. For each (subject, wdt:predicate, object),
        the work it is stated in (its label, else its title) or its reference URL, and the statement
        itself, which a reader can open. A statement without reference is reported as such."""
        triples = [t for t in triples if t[0].startswith(WD) and t[1].startswith(WDT) and t[2].startswith(WD)]
        out: dict[tuple[str, str, str], dict] = {}
        for i in range(0, len(triples), 40):
            chunk = triples[i:i + 40]
            values = " ".join(f"(<{s}> <{p.replace('/prop/direct/', '/prop/')}> <{p.replace('/prop/direct/', '/prop/statement/')}> <{o}>)"
                              for s, p, o in chunk)
            rows = self._select(f"""SELECT ?s ?pp ?o ?st ?src ?lab ?title ?url WHERE {{
  VALUES (?s ?pp ?ps ?o) {{ {values} }}
  ?s ?pp ?st . ?st ?ps ?o .
  OPTIONAL {{ ?st prov:wasDerivedFrom ?ref .
    OPTIONAL {{ ?ref pr:P248 ?src .
      OPTIONAL {{ ?src rdfs:label ?lab FILTER(lang(?lab) IN ("en", "de", "mul")) }}
      OPTIONAL {{ ?src wdt:P1476 ?title }} }}
    OPTIONAL {{ ?ref pr:P854 ?url }} }}
}}""".replace("SELECT", "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#> SELECT", 1))
            for r in rows:
                key = (r["s"], r["pp"].replace("/prop/", "/prop/direct/"), r["o"])
                cur = out.setdefault(key, {"statement": r["st"], "label": None, "url": None, "item": None})
                if r.get("src") and not cur["item"]:
                    cur["item"] = r["src"]
                if (r.get("lab") or r.get("title")) and not cur["label"]:
                    cur["label"] = r.get("lab") or r.get("title")
                if r.get("url") and not cur["url"]:
                    cur["url"] = r["url"]
        # A cited page that no longer opens: the copy the Internet Archive kept of it, if any.
        for v in out.values():
            if v["url"]:
                v["alive"], v["archive"] = self._link_state(v["url"])
        # Works cited without a name on the query service (e.g. scholarly articles, kept in a
        # separate graph): their label or title from the entity API.
        missing = sorted({v["item"] for v in out.values() if v["item"] and not v["label"]})
        for i in range(0, len(missing), 50):
            ids = "|".join(m.rsplit("/", 1)[-1] for m in missing[i:i + 50])
            try:
                r = requests.get("https://www.wikidata.org/w/api.php", timeout=30,
                                 params={"action": "wbgetentities", "ids": ids, "props": "labels|claims", "format": "json"},
                                 headers={"User-Agent": config.USER_AGENT})
                ents = r.json().get("entities", {}) if r.ok else {}
            except (requests.RequestException, ValueError):
                ents = {}
            for q, e in ents.items():
                labels = e.get("labels", {})
                name = next((labels[l]["value"] for l in ("en", "de", "mul") if l in labels),
                            next(iter(labels.values()), {}).get("value") if labels else None)
                if not name:
                    titles = e.get("claims", {}).get("P1476", [])
                    name = titles[0]["mainsnak"].get("datavalue", {}).get("value", {}).get("text") if titles else None
                if name:
                    for v in out.values():
                        if v["item"] == WD + q and not v["label"]:
                            v["label"] = name
        return out

    def class_members(self, cls: str) -> list[Fact]:
        if not cls.startswith(WD):
            return []
        # Only a class has instances; checking first keeps entity seeds cheap.
        if not self._select(f"SELECT ?x WHERE {{ ?x wdt:P31 <{cls}> }} LIMIT 1"):
            return []
        rows = self._select(f"""SELECT DISTINCT ?x WHERE {{
  SERVICE wikibase:box {{ ?x wdt:P625 ?c .
    bd:serviceParam wikibase:cornerSouthWest "Point({self.west} {self.south})"^^geo:wktLiteral ;
                    wikibase:cornerNorthEast "Point({self.east} {self.north})"^^geo:wktLiteral . }}
  ?x wdt:P31/wdt:P279* <{cls}> .
}} LIMIT 2000""")
        return [Fact(subject=r["x"], predicate=WDT + "P31", object=cls, confidence=1.0) for r in rows]

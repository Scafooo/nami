"""Fig. 2 of the paper: the places that four lists propose for one interest (Einstein by default), on a map of
Zurich with an inset for the centre, each place numbered and marked in a table by the lists that propose it.
Shown, not scored: where the ways of making a guide overlap and where each adds. With --grades, each place is
shaded by its mean human grade (0-3): darker, judged more worth a visit.

    python make_fig_map.py [--grades grades.json]

PROVISIONAL inputs until the v2 freeze: guides = stops of the interest in ground_truth_zurich.json (areas left out),
NAMI = output/evaluation_best_first.WT.json, GPT / Gemini = llm_baseline/*_search_2026-10-01.json (geocoded by
llm_baseline/geocode_cache.json).
Water: OpenStreetMap (ODbL), map_data/osm_water_zurich.json (Overpass, natural=water and waterway=river).
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
CODE = EVAL.parent
PAPER = CODE.parent / "paper"
WATER = HERE / "map_data" / "osm_water_zurich.json"
LIST_NAMES = ("Guides", "NAMI", "GPT", "Gemini")
# each model in the colour its maker uses (ChatGPT black, Gemini blue); NAMI the orange of its stops in Fig. 1, guides grey
HEX = {"Guides": "8A8A8A", "NAMI": "B35900", "GPT": "111111", "Gemini": "4285F4"}
COLOURS = {k: f"lst{k}" for k in HEX}
MERGE_M = 50.0
KM_LAT = 111.32
KM_LON = 111.32 * math.cos(math.radians(47.37))


# ------------------------------------------------------------------ data
def metres(a, b) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


V2 = {"Guides": "guides", "NAMI": "C1_nami", "GPT": "llm_gpt_run1", "Gemini": "llm_gemini_run1"}


def load_lists(qid: str) -> dict[str, list[tuple[str, float, float]]]:
    """The v2 lists of the suite (lists/*.json) when they exist; otherwise the provisional v1 inputs."""
    gt = json.loads((EVAL / "ground_truth_zurich.json").read_text())
    it = next(i for i in gt["interests"] if i["id"] == qid)
    areas = ("(district)", "Lake ", "Üetliberg")       # areas, not places one visits at a point
    guides = [(s["name"], s["lat"], s["lon"]) for s in it["stops"]   # all stops of the guides, not the ten compared
              if s.get("lat") and not any(a in s["name"] for a in areas)]
    v2 = HERE / "lists"
    if all((v2 / f"{f}.json").exists() for f in V2.values() if f != "guides"):
        out = {"Guides": guides}
        for n, f in V2.items():
            if f == "guides":
                continue
            prof = json.loads((v2 / f"{f}.json").read_text())["profiles"].get(qid, {"places": []})
            out[n] = [(p["name"], p["lat"], p["lon"]) for p in prof["places"] if p.get("lat") is not None and p.get("in_area", True)]
        return out
    out: dict[str, list] = {n: [] for n in LIST_NAMES}
    out["Guides"] = guides
    wt = json.loads((CODE / "output" / "evaluation_best_first.WT.json").read_text())
    run = next(r for r in wt["runs"] if r.get("interests") == [qid])
    out["NAMI"] = [(c["label"], c["lat"], c["lon"]) for c in run["candidates"][:10] if c.get("lat")]
    geo = json.loads((EVAL / "llm_baseline" / "geocode_cache.json").read_text())
    for name, f in (("GPT", "gpt_search_2026-10-01.json"), ("Gemini", "gemini_search_2026-10-01.json")):
        d = json.loads((EVAL / "llm_baseline" / f).read_text())
        e = next(x for x in d if x["interest_id"] == qid)
        for p in e["places"][:10]:
            g = geo.get(p.get("address") or "")
            if g:
                out[name].append((p["name"], float(g["lat"]), float(g["lon"])))
    return out


def tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-zäöü]+", name.lower()) if len(t) > 4} - {"zurich", "zürich", "strasse", "building"}


def same(p: dict, name: str, lat: float, lon: float) -> bool:
    """One place: closer than MERGE_M, or closer than 150 m with a distinctive word of the name in common."""
    d = metres((p["lat"], p["lon"]), (lat, lon))
    return d < MERGE_M or (d < 150 and bool(tokens(p["name"]) & tokens(name)))


def pool(lists: dict) -> list[dict]:
    places: list[dict] = []
    for lst in LIST_NAMES:
        for name, lat, lon in lists[lst]:
            hit = next((p for p in places if same(p, name, lat, lon)), None)
            if hit is None:
                hit = {"name": name, "lat": lat, "lon": lon, "lists": set()}
                places.append(hit)
            hit["lists"].add(lst)
    places.sort(key=lambda p: (-len(p["lists"]), -p["lat"]))
    return places


def short(name: str) -> str:
    parts = [x.strip() for x in re.sub(r"\s*\(.*?\)", "", name).split(",")]
    first = parts[0]
    addr = next((x for x in parts[1:] if re.search(r"\d", x)), None)
    if addr and not re.search(r"\d", first) and len(first) > 18:
        first = addr
    first = first if len(first) <= 22 else first[:20].rstrip() + "."
    return first.replace("&", r"\&").replace("_", r"\_").replace("#", r"\#").replace("%", r"\%")


# ------------------------------------------------------------------ geometry
class Frame:
    """A lat/lon window drawn into a box of width w (units), keeping the aspect of the ground."""

    def __init__(self, lat0, lon0, lat1, lon1, w):
        self.lat0, self.lon0, self.lat1, self.lon1 = lat0, lon0, lat1, lon1
        self.k = w / ((lon1 - lon0) * KM_LON)            # units per km
        self.w, self.h = w, (lat1 - lat0) * KM_LAT * self.k

    def xy(self, lat, lon):
        return (lon - self.lon0) * KM_LON * self.k, (lat - self.lat0) * KM_LAT * self.k

    def inside(self, lat, lon) -> bool:
        return self.lat0 <= lat <= self.lat1 and self.lon0 <= lon <= self.lon1


def rings(rel: dict) -> list[list[tuple[float, float]]]:
    """The outer rings of a multipolygon relation, joining its member ways end to end."""
    segs = [[(n["lat"], n["lon"]) for n in m["geometry"]] for m in rel["members"]
            if m.get("role") == "outer" and m.get("geometry")]
    out = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring += s[1:]
                elif s[-1] == ring[-1]:
                    ring += s[::-1][1:]
                elif s[-1] == ring[0]:
                    ring = s + ring[1:]
                elif s[0] == ring[0]:
                    ring = s[::-1] + ring[1:]
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        out.append(ring)
    return out


def water(fr: Frame) -> list[str]:
    d = json.loads(WATER.read_text())
    out = []

    def path(pts):
        pts = pts[::2] + [pts[-1]]
        return " -- ".join(f"({x:.2f},{y:.2f})" for x, y in (fr.xy(*p) for p in pts))

    for e in d["elements"]:
        tags = e.get("tags", {})
        if e["type"] == "way" and tags.get("waterway") == "river":
            out.append(rf"\draw[riverline] {path([(n['lat'], n['lon']) for n in e['geometry']])};")
        elif e["type"] == "way" and tags.get("natural") == "water" and len(e["geometry"]) > 3:
            out.append(rf"\fill[water] {path([(n['lat'], n['lon']) for n in e['geometry']])} -- cycle;")
        elif e["type"] == "relation" and tags.get("natural") == "water":
            for r in rings(e):
                if len(r) > 3:
                    out.append(rf"\fill[water] {path(r)} -- cycle;")
    return out


LABELS = [(47.3760, 8.5418, "Limmat", 68), (47.3640, 8.5455, "Lake Zurich", 0), (47.3808, 8.5600, "Zürichberg", 0),
          (47.3690, 8.5418, "Old Town", 0)]
BASE = HERE / "map_data" / "osm_base_zurich.json"      # streets, rail, parks, woods (OpenStreetMap, ODbL)
BASE_WIDE = HERE / "map_data" / "osm_base_zurich_wide.json"   # the same, main roads only, for the whole city
ROADS = {"primary": ("black!20", 0.9), "secondary": ("black!20", 0.9), "tertiary": ("black!14", 0.55),
         "residential": ("black!10", 0.3), "unclassified": ("black!10", 0.3), "living_street": ("black!10", 0.3),
         "pedestrian": ("black!10", 0.3)}


def base(fr: Frame, src: Path = BASE) -> tuple[list[str], list[str]]:
    """Under the water: parks and woods; over it: streets and rail."""
    if not src.exists():
        return [], []
    d = json.loads(src.read_text())
    under, over = [], []

    def path(geom):
        pts, last = [], None
        for n in geom:
            q = tuple(round(v, 2) for v in fr.xy(n["lat"], n["lon"]))
            if q != last:
                pts.append(q)
                last = q
        return " -- ".join(f"({x:.2f},{y:.2f})" for x, y in pts) if len(pts) > 1 else None

    for e in d["elements"]:
        t, g = e.get("tags", {}), e.get("geometry")
        if not g:
            continue
        pth = path(g)
        if not pth:
            continue
        if t.get("leisure") == "park" or t.get("landuse") in ("forest", "cemetery") or t.get("natural") == "wood":
            under.append(rf"\fill[green!35!black!10] {pth} -- cycle;")
        elif t.get("highway") in ROADS:
            col, w = ROADS[t["highway"]]
            over.append(rf"\draw[{col}, line width={w}pt, line cap=round] {pth};")
        elif t.get("railway") == "rail":
            over.append(rf"\draw[black!30, line width=0.35pt] {pth};")
    return under, over


# ------------------------------------------------------------------ drawing
def nudge(fr: Frame, places: list[dict], d: float, rounds: int = 200) -> None:
    """Push apart the labels of places closer than d units, keeping each near its point (a leader line shows it)."""
    pos = [list(fr.xy(p["lat"], p["lon"])) for p in places]
    for _ in range(rounds):
        moved = False
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                dist = math.hypot(dx, dy)
                if dist < d:
                    if dist == 0:
                        dx, dy, dist = 1.0, 0.0, 1.0
                    push = (d - dist) / 2 + 0.005
                    pos[i][0] -= dx / dist * push; pos[i][1] -= dy / dist * push
                    pos[j][0] += dx / dist * push; pos[j][1] += dy / dist * push
                    moved = True
        if not moved:
            break
    for p, (x, y) in zip(places, pos):
        if math.hypot(x - fr.xy(p["lat"], p["lon"])[0], y - fr.xy(p["lat"], p["lon"])[1]) > 0.01:
            p["pos"] = (x, y)

def shade(p: dict) -> str:
    g = p.get("grade")
    return "white" if g is None else f"black!{int(4 + 10 * g)}"     # grey, not to be read as NAMI's orange


def draw_map(fr: Frame, places: list[dict], dx: float, dy: float, label: str, scale_km: float, inset_tr: bool = False) -> list[str]:
    L = [rf"\begin{{scope}}[shift={{({dx:.2f},{dy:.2f})}}]",
         rf"\begin{{scope}}\clip (0,0) rectangle ({fr.w:.2f},{fr.h:.2f});"]
    under, over = base(fr)
    L += under + water(fr) + over
    for lat, lon, text, rot in LABELS:                    # a few names to find one's way
        if fr.inside(lat, lon) and not (inset_tr and text == "Zürichberg"):
            x, y = fr.xy(lat, lon)
            L.append(rf"\node[font=\fontsize{{6}}{{7}}\selectfont\itshape\sffamily, text=black!45, rotate={rot}] at ({x:.2f},{y:.2f}) {{{text}}};")
    L.append(r"\end{scope}")
    L.append(rf"\draw[black!30] (0,0) rectangle ({fr.w:.2f},{fr.h:.2f});")
    for p in places:
        if p.get("frame") is fr:
            x, y = p.get("pos") or fr.xy(p["lat"], p["lon"])
            tx, ty = fr.xy(p["lat"], p["lon"])
            if (x, y) != (tx, ty):
                L.append(rf"\fill[black!70] ({tx:.2f},{ty:.2f}) circle (0.35pt); \draw[black!45] ({tx:.2f},{ty:.2f}) -- ({x:.2f},{y:.2f});")
            L.append(rf"\node[place, fill={shade(p)}] at ({x:.2f},{y:.2f}) {{{p['n']}}};")
            on = [n for n in LIST_NAMES if n in p["lists"]]
            for k, n in enumerate(on):                     # one coloured mark per list, under the number
                L.append(rf"\node[mk, fill={COLOURS[n]}] at ({x - 0.075 * (len(on) - 1) + 0.15 * k:.2f},{y - 0.24:.2f}) {{}};")
    bar = scale_km * fr.k
    L.append(rf"\draw[black!60] (0.2,0.22) -- ({0.2 + bar:.2f},0.22) node[midway, above=0.5pt] {{{label}}};")
    L.append(r"\end{scope}")
    return L


def draw_overview(ov: Frame, win: Frame, places: list[dict], dx: float, dy: float) -> list[str]:
    """The whole city, small: where the places beyond the centre lie, and the centre as a rectangle."""
    L = [rf"\begin{{scope}}[shift={{({dx:.2f},{dy:.2f})}}]",
         rf"\fill[white] (0,0) rectangle ({ov.w:.2f},{ov.h:.2f});",
         rf"\begin{{scope}}\clip (0,0) rectangle ({ov.w:.2f},{ov.h:.2f});"]
    under, over = base(ov, BASE_WIDE)
    thin = [re.sub(r"line width=[0-9.]+pt", "line width=0.25pt", x).replace("black!20", "black!15").replace("black!14", "black!9").replace("black!30", "black!15")
            for x in over]
    L += under + water(ov) + thin
    L.append(r"\end{scope}")
    (x0, y0), (x1, y1) = ov.xy(win.lat0, win.lon0), ov.xy(win.lat1, win.lon1)
    L.append(rf"\draw[black!55, densely dashed] ({x0:.2f},{y0:.2f}) rectangle ({x1:.2f},{y1:.2f});")
    L.append(rf"\draw[black!45] (0,0) rectangle ({ov.w:.2f},{ov.h:.2f});")
    for p in places:
        if p.get("frame") is ov:
            x, y = p.get("pos") or ov.xy(p["lat"], p["lon"])
            tx, ty = ov.xy(p["lat"], p["lon"])
            if (x, y) != (tx, ty):
                L.append(rf"\fill[black!70] ({tx:.2f},{ty:.2f}) circle (0.3pt); \draw[black!45] ({tx:.2f},{ty:.2f}) -- ({x:.2f},{y:.2f});")
            on = [n for n in LIST_NAMES if n in p["lists"]]
            L.append(rf"\node[place, minimum size=2.6mm, font=\fontsize{{4.4}}{{5}}\selectfont\bfseries\sffamily, fill={shade(p)}] at ({x:.2f},{y:.2f}) {{{p['n']}}};")
            for k, n in enumerate(on):
                L.append(rf"\node[mk, minimum size=1.1mm, fill={COLOURS[n]}] at ({x - 0.065 * (len(on) - 1) + 0.13 * k:.2f},{y - 0.21:.2f}) {{}};")
    L.append(r"\end{scope}")
    return L


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--qid", default="Q937")
    ap.add_argument("--label", default="Einstein")
    ap.add_argument("--out", default=str(PAPER / "sections" / "fig_map.tex"))
    ap.add_argument("--grades", help="JSON {place name: mean grade 0-3}")
    a = ap.parse_args()

    lists = load_lists(a.qid)
    places = pool(lists)
    grades = json.loads(Path(a.grades).read_text()) if a.grades else {}
    for i, p in enumerate(places, 1):
        p["n"], p["grade"] = i, grades.get(p["name"])

    # the map of the centre; places beyond it are listed with an asterisk
    win = Frame(47.3625, 8.5370, 47.3845, 8.5670, 7.4)
    ov = Frame(47.344, 8.492, 47.415, 8.582, 2.3)       # the whole city, in a corner of the centre
    for p in places:
        p["frame"] = win if win.inside(p["lat"], p["lon"]) else ov if ov.inside(p["lat"], p["lon"]) else None
    nudge(win, [p for p in places if p["frame"] is win], 0.50)
    nudge(ov, [p for p in places if p["frame"] is ov], 0.42)

    L = [f"% Fig.: {a.label} in Zurich, the places four lists propose. Generated by code/evaluation/suite/make_fig_map.py.",
         ("% Inputs: the v2 lists (lists/*.json); guides: all stops of the interest." if (HERE / "lists" / "C1_nami.json").exists()
          else "% PROVISIONAL inputs (v1 lists): regenerate from the v2 lists."),
         *[rf"\definecolor{{{COLOURS[k]}}}{{HTML}}{{{HEX[k]}}}" for k in HEX],
         r"\begin{tikzpicture}[font=\fontsize{6}{7}\selectfont\sffamily,",
         r"  water/.style={fill=blue!9, draw=none}, riverline/.style={draw=blue!14, line width=4pt, line cap=round},",
         r"  place/.style={circle, draw=black!80, thick, minimum size=3.2mm, inner sep=0pt, font=\fontsize{5.2}{6}\selectfont\bfseries\sffamily},",
         r"  mk/.style={circle, minimum size=1.3mm, inner sep=0pt},",
         r"  off/.style={circle, draw=black!25, minimum size=1.6mm, inner sep=0pt}]"]
    L += draw_map(win, places, 0, 0, "200\\,m", 0.2, any(p["frame"] is ov for p in places))
    if any(p["frame"] is ov for p in places):
        L += draw_overview(ov, win, places, win.w - ov.w - 0.08, win.h - ov.h - 0.08)   # top right, over the Zürichberg woods
    full = win

    # the table, one column, to the right of the inset
    tx = full.w + 0.35
    dy = (full.h - 0.65) / (len(places) + 1)
    ty = 0.19 + len(places) * dy                       # the last line of the table sits on the bottom of the map
    marks = [tx + 2.35 + 0.2 * k for k in range(len(LIST_NAMES))]
    for k, n in enumerate(LIST_NAMES):
        L.append(rf"\node[anchor=south west, rotate=60, text={COLOURS[n]}, inner sep=0pt, font=\fontsize{{5}}{{6}}\selectfont\bfseries\sffamily] at ({marks[k] - 0.05:.2f},{ty + 0.08:.2f}) {{{n}}};")
    for j, p in enumerate(places):
        y = ty - 0.12 - j * dy
        L.append(rf"\node[place, fill={shade(p)}, minimum size=2.5mm, font=\fontsize{{4.6}}{{5}}\selectfont\bfseries\sffamily] at ({tx + 0.1:.2f},{y:.2f}) {{{p['n']}}};"
                 rf" \node[anchor=west, font=\fontsize{{5.5}}{{6.5}}\selectfont\sffamily] at ({tx + 0.25:.2f},{y:.2f}) {{{short(p['name'])}{'' if p['frame'] is win else '$^{*}$'}}};")
        for k, n in enumerate(LIST_NAMES):
            L.append(rf"\node[{'mk, minimum size=1.6mm, fill=' + COLOURS[n] if n in p['lists'] else 'off'}] at ({marks[k]:.2f},{y:.2f}) {{}};")
    if any(p["frame"] is not win for p in places):
        L.append(rf"\node[anchor=west, text=black!60, font=\fontsize{{5}}{{6}}\selectfont\sffamily] at ({tx + 0.25:.2f},{ty - 0.12 - len(places) * dy:.2f}) {{$^{{*}}$ in the inset of the whole city}};")
    L.append(r"\end{tikzpicture}")
    Path(a.out).write_text("\n".join(L) + "\n")
    print(f"{len(places)} places, {sum(bool(p['frame']) for p in places)} on the map -> {a.out}")


if __name__ == "__main__":
    main()

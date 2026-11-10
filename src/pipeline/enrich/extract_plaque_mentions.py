"""The people and movements named on the City of Zurich's blue information
plaques at heritage buildings (open data, data/raw/stadt_zuerich_blaue_tafeln.geojson,
764 plaques with title, address, point and a German text), linked to
Wikidata and written as annotated facts of G_P, like extract_mentions.py does
for the tourism catalogue:

    wd:Q123034 nami:livedAt wd:Q1594868 {| nami:source <...#bt_57> ;
        nami:evidence "Hier wohnte 1522-1525 der Reformator Huldrych Zwingli." ;
        nami:confidence 0.7 |} .

The place is the building: the Wikidata item within NEAR_M of the plaque whose
label shares the plaque's title or address, else a node of its own,
https://example.org/nami/data/plaque/<id>, written with its point and title so that
it can be located and labelled. Names are resolved through German Wikipedia
titles (only to reach the Wikidata item; no Wikipedia text is read). The
relation is the German cue closest to the mention, else nami:mentionedAt.

Run: python src/pipeline/enrich/extract_plaque_mentions.py"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import config  # noqa: E402
from extract_mentions import (CACHE_FILE, CONF_FULL, CONF_SURNAME, DISAMBIGUATION, FIRM_AFTER,  # noqa: E402
                              FIRM_BEFORE, HUMAN, MOVEMENTS, WD, Wikidata, candidates, link_mention,
                              link_span, name_tokens, sentences, spans, turtle_string)

PLAQUES = config.RAW_DIR / "stadt_zuerich_blaue_tafeln.geojson"
OUT_FILE = config.GEO_ONTOLOGY_DIR / "extracted" / "zurich_city_plaques.ttl"
SOURCE = "https://data.stadt-zuerich.ch/dataset/geo_liste_der_informationstafeln_an_denkmalschutz_objekten"
NODE = "https://example.org/nami/data/plaque/"
NEAR_M = 60

# German cues; the one closest to the mention names the relation.
CUES_DE = {
    "bornAt": r"geboren|Geburtshaus|kam hier zur Welt",
    # "Wohnhaus" alone names the kind of building ("1864 von Architekt Theodor Geiger als Wohnhaus erbaut"):
    # only "Wohnhaus von/des/der X" says that someone lived there
    "livedAt": r"wohnte|bewohnt\w*|lebte|Wohnhaus\s+(?:von|des|der)\b|Wohnsitz|residierte",
    "buriedAt": r"begraben|beigesetzt|Grabstätte|Grab\b",
    "workedAt": r"arbeitete|wirkte|lehrte|studierte|Werkstatt|Atelier|Druckerei|Offizin|Praxis|Kanzlei",
    "foundedAt": r"gründete|gegründet|Gründung",
    "frequented": r"verkehrte|Stammgast|Treffpunkt|trafen sich|besucht\w*",
    "performedAt": r"aufgeführt|Aufführung|Konzert|spielte|predigte|Predigt",
    "commemoratedAt": r"Gedenk\w*|erinnert an|zu Ehren|Denkmal",
}
CUE_RX = {rel: re.compile(rf"\b(?:{rx})", re.I) for rel, rx in CUES_DE.items()}


def relation_de(sentence: str, span: str) -> str:
    at = sentence.find(span)
    best, dist = "mentionedAt", None
    for rel, rx in CUE_RX.items():
        for m in rx.finditer(sentence):
            d = abs(m.start() - at) if at >= 0 else 0
            if dist is None or d < dist:
                best, dist = rel, d
    return best


def main() -> None:
    wd = Wikidata(CACHE_FILE)
    feats = json.loads(PLAQUES.read_text())["features"]
    west, south, east, north = config.ZURICH_BBOX
    plaques = []
    for f in feats:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        text = " ".join(x for x in (p.get("titel_tafel"), p.get("text_tafel")) if x)
        if text.strip() and west <= lon <= east and south <= lat <= north:
            plaques.append({"id": str(p.get("bt_id") or p.get("objectid")), "title": (p.get("titel_tafel") or "").strip(),
                            "address": (p.get("adressen_tafel") or "").strip(), "lat": lat, "lon": lon,
                            "sentences": sentences(re.sub(r"\s+", " ", (p.get("text_tafel") or "").strip()))})
    corpus = " ".join(s for p in plaques for s in p["sentences"])
    lowercase_words = set(re.findall(r"\b[a-zäöü][a-zäöüß'-]+\b", corpus))
    names = {c for p in plaques for s in p["sentences"] for sp in spans(s) for c, _, _ in candidates(sp)
             if not (len(c.split()) == 1 and c.lower() in lowercase_words)}
    print(f"{len(plaques)} plaques, {len(names)} candidate names", flush=True)
    try:
        title_qid = wd.titles(names, wiki="de")
        ents = wd.entities({q for q in title_qid.values() if q and q != DISAMBIGUATION})
        by_surname: dict[str, set[str]] = {}
        for c, q in title_qid.items():
            if q and q != DISAMBIGUATION and len(c.split()) > 1 and HUMAN in ents.get(q, {}).get("p31", []):
                by_surname.setdefault(c.split()[-1], set()).add(q)
        surnames = {s: next(iter(qs)) for s, qs in by_surname.items() if len(qs) == 1}
        found_by = {}
        for p in plaques:
            found: dict[str, dict] = {}
            for sent in p["sentences"]:
                for sp in spans(sent):
                    hit = link_span(sp, title_qid, ents, lowercase_words, surnames)
                    if not hit:
                        continue
                    text, q, ent, conf = hit
                    at = sent.find(text)
                    if at >= 0 and sent[at + len(text):].startswith("-"):
                        continue        # part of a compound: "Richard Wagner- und Schulhausstrasse"
                    if len(text.split()) == 1 and HUMAN in ent["p31"] and at >= 0 and (
                            FIRM_AFTER.match(sent[at + len(text):]) or FIRM_BEFORE.search(sent[:at])):
                        continue
                    m = {"qid": q, "label": ent["label"], "span": text, "relation": relation_de(sent, text),
                         "confidence": round(conf, 2), "evidence": sent}
                    old = found.get(q)
                    if old is None or (m["confidence"], m["relation"] != "mentionedAt") > \
                            (old["confidence"], old["relation"] != "mentionedAt"):
                        found[q] = m
            if found:
                found_by[p["id"]] = found
        near = wd.near({p["id"]: (p["lat"], p["lon"]) for p in plaques if p["id"] in found_by}, NEAR_M)
    finally:
        wd.save()

    lines = [
        "# Facts of G_P read from the City of Zurich's blue information plaques at heritage buildings",
        f"# (open data: {SOURCE}), by src/pipeline/enrich/extract_plaque_mentions.py.",
        "# Relations: mappings/text_relations.ttl; evidence: the sentence of the plaque.",
        "@prefix nami: <https://example.org/nami/ontology#> .",
        "@prefix wd:  <http://www.wikidata.org/entity/> .",
        "@prefix geo: <http://www.opengis.net/ont/geosparql#> .",
        "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
        "",
    ]
    review, n_facts = [], 0
    for p in plaques:
        found = found_by.get(p["id"])
        if not found:
            continue
        names_p = [name_tokens(p["title"]), name_tokens(p["address"])]
        best, score = None, 0.0
        for it in near.get(p["id"], []):
            for label in it["labels"]:
                lt = name_tokens(label)
                for nt in names_p:
                    if nt and lt and not ({t for t in nt if t.isdigit()} - lt):
                        sc = len(nt & lt) / max(len(nt), len(lt))
                        if sc > score:
                            best, score = it["id"], sc
        if best and score >= 0.5:
            place = f"wd:{best}"
        else:
            place = f"<{NODE}bt_{p['id']}>"
            lines.append(f"{place} rdfs:label {turtle_string(p['title'] or p['address'])} ;\n"
                         f"    geo:hasGeometry [ geo:asWKT \"POINT({p['lon']} {p['lat']})\"^^geo:wktLiteral ] .")
        for m in found.values():
            if place == f"wd:{m['qid']}":
                continue
            lines.append(f"wd:{m['qid']} nami:{m['relation']} {place} {{| nami:source <{SOURCE}#bt_{p['id']}> ;\n"
                         f"    nami:evidence {turtle_string(m['evidence'])} ;\n    nami:confidence {m['confidence']} |}} .")
            n_facts += 1
        review.append({"plaque": p["id"], "title": p["title"], "address": p["address"], "place": place,
                       "mentions": list(found.values())})
    OUT_FILE.write_text("\n".join(lines) + "\n")
    (config.ROOT_DIR / "output" / "plaque_mentions_review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False))
    print(f"{n_facts} facts from {len(review)} plaques -> {OUT_FILE}; {wd.calls} API calls")


if __name__ == "__main__":
    main()

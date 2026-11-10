"""Step 6 (design of 2026-11-05): every number of Sect. 5 of the paper, from lists/ and the filled workbooks only.

Writes results/report.md with
  - Table 3 (the lists compared): places worth a visit, persons and themes; places that exist; reasons per place;
    reasons stated by the source shown. Rows ready for LaTeX in results/tables/table3_rows.tex.
  - Table 4 (what each part of NAMI does): worth a visit, persons and themes; reasons per place.
  - "Numbers for the text", one line per placeholder of Sect. 5.3, in the order of the paper;
  - agreement between the people who judged, paired tests per interest (Holm), the quality of each workbook.
A task whose workbooks are not back is reported as missing, never filled in.

The helpers (annotations, consensus, agreement, verdicts) are those of results.py."""

from __future__ import annotations

import json
from statistics import mean, median

from lib.suite_paths import CODE  # noqa: F401

import settings
from lib import bench
from lib import results as old
from lib.results import G, E, MD, TEXT, GT, h, line, say, table, tex_rows, pct, p0, lists, single, kind, load_key, \
    annotations, agreement, consensus, holds, by_list, per_profile_worth
from lib.stats import holm, paired

R = settings.RESULTS

TAB3 = {"guides": "Guides", "B_onehop": "One-hop query", "C1_nami": r"\nami",
        "llm_gpt_run1": "GPT", "llm_gemini_run1": "Gemini",
        "hybrid_gpt_run1": r"\nami{} + GPT", "hybrid_gemini_run1": r"\nami{} + Gemini"}
TAB4 = {"C1_nami": r"\nami", "C2_wikidata": "Without the sources of the city", "A1_no_eta": r"Without specificity $\eta$",
        "A2_no_decay": "Without decay", "A3_no_derived": "Without derived seeds", "A4_class_up": "Classes followed upward",
        "C3_noprov": "Without provenance"}
NO_REASONS = {"guides", "B_onehop"}


def reasons_per_place(L: dict, lst: str):
    places = [p for pr in single(L, lst).values() for p in pr["places"]]
    return mean(sum(r["shown"] for r in p["reasons"]) for p in places) if places else None


def worth_of(items, grade, which):
    g = [grade[r] for pid, r in items if r in grade and pid in GT and kind(pid) == which]
    return sum(x >= 2 for x in g), len(g)


def reasons_part():
    """A1: does the source shown open and state the reason; per list, and the check's strictness."""
    h("A1: reasons")
    key = load_key("A1")
    if not key:
        line("A1 not built yet.")
        return {}
    kept, quality = annotations("A1", key)
    if not kept:
        line("**A1: no file back yet.**")
        return {}
    line(f"- agreement, the page states the reason: {agreement(kept, 'Q2 page states the reason')}")
    verdict = {rid: holds(kept, rid) for rid in key["rows"]}
    out = {}
    for lst, places in key["places"].items():
        vs = [verdict[r] for pl in places for r in pl["reasons"] if verdict[r] is not None]
        out[lst] = (sum(vs), len(vs))
    # why the models' reasons fail: the page is gone or does not open, or it opens and does not state them
    fails = [rid for rid, k in key["rows"].items() if k["reason_kind"] == "web" and verdict.get(rid) is False
             and any(l.startswith("llm_") for l in k["lists"])]
    gone = sum(consensus(kept, r, "Q1 source opens") == "no" for r in fails)
    out["fail_states_not"] = (len(fails) - gone, len(fails))
    drop = [verdict[r] for r, k in key["rows"].items() if "dropped" in k["lists"] and verdict[r] is not None]
    out["dropped_stated"] = (sum(drop), len(drop))
    return out


def places_part():
    h("A2: places")
    key, grade, exists, _ = old.grades()
    return key, grade, exists


def table3(L, a1, key, grade, exists):
    h("Table 3. The lists compared")
    bl = by_list(key)
    rows, tex, out = [], [], {}
    for lst, name in TAB3.items():
        items = [(pid, r) for pid, r in bl.get(lst, []) if pid in GT]
        wp, wt = worth_of(items, grade, "persons"), worth_of(items, grade, "themes")
        ex = [exists[r] for _, r in items if exists.get(r) not in (None, "disagree")]
        exist = (sum(x == "yes" for x in ex), len(ex))
        rpp = None if lst in NO_REASONS else reasons_per_place(L, lst)
        st = None if lst in NO_REASONS else a1.get(lst)
        out[lst] = {"wp": wp, "wt": wt, "exist": exist, "rpp": rpp, "stated": st, "worth_pi": per_profile_worth(items, grade)}
        rows.append([name, pct(*wp), pct(*wt), pct(*exist), "--" if rpp is None else round(rpp, 1),
                     "--" if st is None else pct(*st)])
        tex.append(f"{name} & {p0(*wp)} & {p0(*wt)} & {p0(*exist)} & {'--' if rpp is None else f'{rpp:.1f}'} & "
                   f"{'--' if st is None else p0(*st)} \\\\")
    table("table3", ["list", "worth a visit (persons)", "worth a visit (themes)", "places that exist",
                     "reasons per place", "reasons stated by the source"], rows)
    tex_rows("table3", tex)
    return out


def table4(L, key, grade):
    h("Table 4. What each part of NAMI does")
    bl = by_list(key)
    rows, tex, out = [], [], {}
    for lst, name in TAB4.items():
        items = [(pid, r) for pid, r in bl.get(lst, []) if pid in GT]
        wp, wt = worth_of(items, grade, "persons"), worth_of(items, grade, "themes")
        rpp = reasons_per_place(L, lst)
        out[lst] = {"wp": wp, "wt": wt, "rpp": rpp, "worth_pi": per_profile_worth(items, grade),
                    "empty": sum(not pr["places"] for pr in single(L, lst).values())}
        rows.append([name, pct(*wp), pct(*wt), "--" if rpp is None else round(rpp, 1)])
        tex.append(f"{name} & {p0(*wp)} & {p0(*wt)} & {'--' if rpp is None else f'{rpp:.1f}'} \\\\")
    table("table4", ["variant", "worth a visit (persons)", "worth a visit (themes)", "reasons per place"], rows)
    tex_rows("table4", tex)
    return out


def tests(t3, t4):
    h("Paired tests per interest (worth a visit), Holm-corrected")
    pi = {**{k: v["worth_pi"] for k, v in t3.items()}, **{k: v["worth_pi"] for k, v in t4.items()}}
    pick = lambda base: next((k for k in pi if k == base or k.startswith(base + "_run")), base)
    res = {f"{a} vs {b}": paired(pi.get(pick(a), {}), pi.get(pick(b), {}), settings.PERMUTATIONS)
           for a, b in settings.PAIRED_TESTS}
    adj = holm({k: v["p"] for k, v in res.items() if v["n"]})
    table("tests", ["comparison", "interests", "mean difference", "95% CI", "p (Holm)"],
          [[k, v["n"], round(v["diff"], 3) if v["n"] else None,
            f"[{v['ci'][0]:.3f}, {v['ci'][1]:.3f}]" if v["n"] else None, round(adj.get(k, float("nan")), 4)]
           for k, v in res.items()])


def lam_sensitivity(L):
    """Share of the first ten places of NAMI kept with lambda 0.4 and 0.6."""
    base = single(L, "C1_nami")
    out = []
    for v in ("S_lam04", "S_lam06"):
        other = single(L, v)
        if not other:
            continue
        kept = [len({p["id"] for p in base[pid]["places"]} & {p["id"] for p in other[pid]["places"]}) / max(1, len(base[pid]["places"]))
                for pid in base if pid in other and base[pid]["places"]]
        out.append(f"{v}: {100 * mean(kept):.0f}% kept" if kept else f"{v}: --")
    return "; ".join(out) or "runs S_lam04 / S_lam06 missing"


def seconds():
    f = settings.RUNS / "nami" / "C1_nami.json"
    if not f.exists():
        return "C1_nami missing"
    s = [r["seconds"] for r in json.loads(f.read_text())["runs"] if "seconds" in r and "error" not in r]
    return f"{median(s):.1f} s on median, {max(s):.1f} s at most ({len(s)} interests)" if s else "--"


def text_numbers(L, a1, t3, t4):
    h("Numbers for the text (Sect. 5.3), in order")
    TEXT.clear()
    models = [f"llm_{m}_run1" for m in settings.MODELS]
    rng = lambda xs: "--" if not xs else f"{min(xs):.0f}--{max(xs):.0f}"
    share = lambda kn: 100 * kn[0] / kn[1] if kn and kn[1] else None
    say("Safe: reasons of NAMI stated by the source", pct(*a1["C1_nami"]) if "C1_nami" in a1 else "--")
    say("Safe: models, range", rng([share(a1[m]) for m in models if m in a1 and a1[m][1]]))
    say("Safe: failing reasons of the models on a page that opens but does not state them",
        pct(*a1["fail_states_not"]) if "fail_states_not" in a1 else "--")
    hy = "hybrid_gpt_run1"
    say("Safe: NAMI+GPT reasons per place / NAMI alone",
        f"{t3.get(hy, {}).get('rpp') or float('nan'):.1f} / {t3.get('C1_nami', {}).get('rpp') or float('nan'):.1f}")
    say("Safe: NAMI+GPT reasons stated", pct(*a1[hy]) if hy in a1 else "--")
    say("Safe: reasons NAMI does not show, stated all the same", pct(*a1["dropped_stated"]) if "dropped_stated" in a1 else "--")
    for k, lab in (("wp", "persons"), ("wt", "themes")):
        say(f"Useful: NAMI worth a visit, {lab}", pct(*t3["C1_nami"][k]))
        say(f"Useful: guides worth a visit, {lab}", pct(*t3["guides"][k]))
        say(f"Useful: models worth a visit, {lab}, range", rng([share(t3[m][k]) for m in models if t3[m][k][1]]))
    say("Useful: models, places that exist, range", rng([share(t3[m]["exist"]) for m in models if t3[m]["exist"][1]]))
    say("Parts: without the city's sources, share of places kept",
        "--" if "C2_wikidata" not in L else f"{100 * mean(len({p['id'] for p in a['places']} & {p['id'] for p in single(L, 'C2_wikidata').get(pid, {'places': []})['places']}) / max(1, len(a['places'])) for pid, a in single(L, 'C1_nami').items()):.0f}%")
    say("Parts: reasons per place without the city's sources / with", f"{t4['C2_wikidata']['rpp'] or float('nan'):.1f} / {t4['C1_nami']['rpp'] or float('nan'):.1f}")
    themes = [i for i in GT.values() if i["kind"] != "person"]
    say("Parts: movements, periods and themes with no place without derived seeds",
        f"{sum(not single(L, 'A3_no_derived').get(i['id'], {'places': [1]})['places'] for i in themes)} of {len(themes)}")
    say("Parts: lambda 0.4 / 0.6", lam_sensitivity(L))
    say("Parts: response time of NAMI", seconds())
    MD.extend(TEXT)


def main() -> None:
    R.mkdir(parents=True, exist_ok=True)
    MD.clear()
    GT.clear()
    GT.update({i["id"]: i for i in bench.interests()})
    L = lists()
    if not L:
        print("no lists yet: run prepara.py")
        return
    bench.prepare([p for l in L.values() for pr in l["profiles"].values() for p in pr["places"]])
    MD.append("# Evaluation (design of 2026-11-05): results\n\nFrom lists/ and the filled workbooks only. "
              "Rows ready for LaTeX: results/tables/.")
    a1 = reasons_part()
    key, grade, exists = places_part()
    t3 = table3(L, a1, key, grade, exists)
    t4 = table4(L, key, grade)
    tests(t3, t4)
    text_numbers(L, a1, t3, t4)
    (R / "report.md").write_text("\n".join(MD) + "\n")
    print(f"-> {R / 'report.md'}")

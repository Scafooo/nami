"""Step 6: every number of the paper, in the order of the paper, from lists/ and the filled workbooks only.

Writes results/report.md:
  - the rows of Tables 3, 4 and 5, with intervals and tests, and ready for LaTeX in results/tables/*_rows.tex;
  - "Numbers for the text": one line per placeholder of Sect. 5, in the order it appears;
  - the quality of every returned workbook (controls, exclusions, agreement).
results/data/*.csv holds everything per interest and per list (the artifact).

A task whose workbooks are not back is reported as missing, never filled with automatic verdicts. An annotator
failing more than settings.EXCLUDE_IF_CONTROL_ERRORS_ABOVE of the controls is excluded. An item counts when the
kept annotators agree; the others are counted apart, to adjudicate."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from statistics import mean

from lib.suite_paths import CODE  # noqa: F401

import settings
from certify_ranking import certify
from lib import bench, sheets
from lib.stats import alpha, holm, paired, pairwise_kappa, wilson

R = settings.RESULTS
MD: list[str] = []
TEXT: list[str] = []          # numbers for the text, in the order of the paper

PAPER = {"llm_gpt_run1": "GPT", "llm_gemini_run1": "Gemini", "llmcheck_gpt_run1": "GPT + check",
         "llmcheck_gemini_run1": "Gemini + check", "C1_nami": r"\nami", "C2_wikidata": r"\nami, Wikidata only",
         "hybrid_gpt_run1": r"\nami{} + GPT", "hybrid_gemini_run1": r"\nami{} + Gemini",
         "B_onehop": "Direct links (Wikidata)", "B_prepackaged": "Pre-packaged tour"}
TAB4 = list(PAPER)
TAB5 = {"C1_nami": r"\nami", "A1_no_eta": r"Without specificity $\eta$", "A2_no_decay": r"Without decay ($\lambda = 1$)",
        "A3_no_derived": "Without derived seeds", "A4_class_up": "Classes followed upward",
        "C3_noprov": "Without provenance", "C2_wikidata": "Wikidata only"}
G, E = "G pertinence (0-3)", "E exists and can be visited or seen today"
GT: dict = {}


# ------------------------------------------------------------------ output
def h(title: str, level: int = 2) -> None:
    MD.append(f"\n{'#' * level} {title}\n")


def line(s: str = "") -> None:
    MD.append(s)


def say(where: str, text) -> None:
    TEXT.append(f"- **{where}**: {text}")


def fmt(v):
    return f"{v:.3f}" if isinstance(v, float) else ("" if v is None else str(v))


def table(name: str, head: list[str], rows: list[list]) -> None:
    MD.append("| " + " | ".join(head) + " |")
    MD.append("|" + "---|" * len(head))
    MD.extend("| " + " | ".join(fmt(v) for v in r) + " |" for r in rows)
    MD.append("")
    (R / "data").mkdir(parents=True, exist_ok=True)
    with open(R / "data" / f"{name}.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(head)
        w.writerows([[fmt(v) for v in r] for r in rows])


def tex_rows(name: str, rows: list[str]) -> None:
    (R / "tables").mkdir(parents=True, exist_ok=True)
    (R / "tables" / f"{name}_rows.tex").write_text(f"% rows of {name} (paper), in its order\n" + "\n".join(rows) + "\n")


def pct(k: int, n: int) -> str:
    if not n:
        return "n/a"
    lo, hi = (max(0.0, x) for x in wilson(k, n))
    return f"{100 * k / n:.1f}% [{100 * lo:.1f}, {100 * hi:.1f}] ({k}/{n})"


def p0(k, n) -> str:
    return f"{100 * k / n:.0f}" if n else "--"


# ------------------------------------------------------------------ data
def lists() -> dict:
    return {f.stem: json.loads(f.read_text()) for f in sorted(settings.LISTS.glob("*.json"))}


def single(L: dict, name: str) -> dict:
    if name not in L:
        return {}
    return {pid: p for pid, p in L[name]["profiles"].items() if pid in GT}


def kind(pid: str) -> str:
    return "persons" if GT[pid]["kind"] == "person" else "themes"


def recall(L: dict, name: str) -> dict:
    out = {}
    for pid, pr in single(L, name).items():
        ag = bench.agreed(GT[pid])
        if ag:
            places = [p for p in pr["places"] if p.get("in_area", True)]
            out[pid] = sum(any(bench.matches(s, p) for p in places) for s in ag) / len(ag)
    return out


# ------------------------------------------------------------------ annotations
def load_key(task: str):
    f = settings.ANNOT_KEYS / f"{task}_key.json"
    return json.loads(f.read_text()) if f.exists() else None


def reference_rows(task: str) -> dict:
    """The rows of the workbook as sent (its first file in ANNOT_OUT), to recognise a returned file of an
    earlier version, whose ids name other places."""
    for f in sorted(settings.ANNOT_OUT.glob(f"{task}_*.xlsx")):
        return sheets.read(f)
    return {}


def annotations(task: str, key: dict) -> tuple[dict, dict]:
    got = sheets.returned(task, settings.ANNOT_BACK)
    quality, kept = {}, {}
    ref = reference_rows(task)
    for who, ans in got.items():
        common = [rid for rid in ans if rid in ref]
        wrong = sum(1 for rid in common if ans[rid].get("place", "") != ref[rid].get("place", ""))
        if not common or wrong > 0.02 * len(common):     # a file of an earlier version: its ids mean other places
            quality[who] = {"rows": len(ans), "controls": 0, "errors": 0, "rate": None, "excluded": True}
            line(f"**{who}: EXCLUDED, a file of an earlier version** ({wrong} of {len(common)} ids name another place)")
            continue
        errors = checked = 0
        for rid, k in key["rows"].items():
            exp = k.get("expect")
            if not exp or rid not in ans:
                continue
            if isinstance(exp, list):
                g = ans[rid].get(G, "")
                if g in ("", "?"):
                    continue
                checked += 1
                errors += not (exp[0] <= int(float(g)) <= exp[1])
            else:
                for col, val in exp.items():
                    a = ans[rid].get(col, "").lower()
                    if a:
                        checked += 1
                        errors += a != val
        rate = errors / checked if checked else None
        excluded = rate is not None and rate > settings.EXCLUDE_IF_CONTROL_ERRORS_ABOVE
        quality[who] = {"rows": len(ans), "controls": checked, "errors": errors, "rate": rate, "excluded": excluded}
        if not excluded:
            kept[who] = {rid: row for rid, row in ans.items() if not key["rows"].get(rid, {}).get("expect")}
    rows = [[w, q["rows"], q["controls"], q["errors"], None if q["rate"] is None else round(q["rate"], 3),
             "EXCLUDED" if q["excluded"] else "kept"] for w, q in sorted(quality.items())]
    table(f"{task}_annotators", ["file", "rows", "controls", "control errors", "error rate", "status"], rows)
    return kept, quality


def consensus(kept: dict, rid: str, col: str):
    vals = [a[rid].get(col, "").lower() for a in kept.values() if rid in a and a[rid].get(col, "").lower() not in ("", "?", "unsure")]
    if not vals:
        return None
    return vals[0] if len(set(vals)) == 1 else "disagree"


def agreement(kept: dict, col: str, level: str = "nominal") -> str:
    units = defaultdict(list)
    for a in kept.values():
        for rid, r in a.items():
            v = r.get(col, "")
            if v.lower() not in ("", "?", "unsure"):          # no judgement, not an answer
                units[rid].append(int(float(v)) if level == "ordinal" else v.lower())
    k = pairwise_kappa({w: {rid: ("" if r.get(col, "").lower() in ("?", "unsure") else r.get(col, "").lower())
                            for rid, r in a.items()} for w, a in kept.items()})
    return f"alpha {alpha(units, level):.2f}" + ("; kappa " + ", ".join(f"{v:.2f} (n={n})" for n, v in k.values()) if k else "")


# ------------------------------------------------------------------ Table 3: reasons (A1)
def holds(kept: dict, rid: str):
    """True / False when the annotators agree that the source opens and states the reason; None otherwise."""
    q1, q2 = consensus(kept, rid, "Q1 source opens"), consensus(kept, rid, "Q2 page states the reason")
    if q1 == "no" or q2 in ("no", "partly"):
        return False
    if q1 == "yes" and q2 == "yes":
        return True
    return None


def table3(L: dict) -> dict:
    h("Table 3. Reasons, judged by people (A1)")
    key = load_key("A1")
    out = {}
    if not key:
        line("A1 not built yet.")
        return out
    kept, quality = annotations("A1", key)
    out["excluded"] = (sum(q["excluded"] for q in quality.values()), len(quality))
    if not kept:
        line("**A1: no file back yet**" + (" (all excluded by their controls)." if quality else "."))
        return out
    for col in ("Q1 source opens", "Q2 page states the reason", "Q3 reason links place to interest"):
        line(f"- agreement, {col}: {agreement(kept, col)}")
    out["kappa"] = agreement(kept, "Q2 page states the reason")
    verdict = {rid: holds(kept, rid) for rid in key["rows"]}
    rows, tex = [], []
    for lst, places in key["places"].items():
        n_r = [len(pl["reasons"]) for pl in places]
        rv = [verdict[r] for pl in places for r in pl["reasons"]]
        det = [v for v in rv if v is not None]
        allh, two = [], []
        for pl in places:
            vs = [verdict[r] for r in pl["reasons"]]
            if vs and all(v is not None for v in vs):
                allh.append(all(vs))
            yes, unk = sum(v is True for v in vs), sum(v is None for v in vs)
            if yes >= 2 or yes + unk < 2:
                two.append(yes >= 2)
        out[lst] = {"all": (sum(allh), len(allh)), "two": (sum(two), len(two)), "reasons": (sum(det), len(det))}
        rows.append([PAPER.get(lst, lst), len(places), round(mean(n_r), 1) if n_r else None, pct(sum(det), len(det)),
                     pct(sum(allh), len(allh)), pct(sum(two), len(two)), sum(v is None for v in rv)])
        tex.append(f"{PAPER.get(lst, lst)} & {mean(n_r):.1f} & {p0(sum(det), len(det))} & {p0(sum(allh), len(allh))} & "
                   f"{p0(sum(two), len(two))} \\\\" if n_r else f"% {lst}: no place")
    table("table3_reasons", ["list", "places", "reasons per place", "reasons that hold", "places: all hold",
                             "places: >=2 hold", "reasons to adjudicate"], rows)
    tex_rows("table3", tex)
    web = [(rid, k) for rid, k in key["rows"].items() if k["reason_kind"] == "web" and not k.get("expect")]
    acc = [verdict[r] for r, k in web if k["auto"] == "holds" and verdict[r] is not None]
    rej = [verdict[r] for r, k in web if k["auto"] == "fails" and verdict[r] is not None]
    out["check_accept"], out["check_reject"] = (sum(acc), len(acc)), (sum(rej), len(rej))
    nami = [r for r, k in key["rows"].items() if "C1_nami" in k["lists"] and k["reason_kind"] == "path"]
    q3 = [x for x in (consensus(kept, r, "Q3 reason links place to interest") for r in nami) if x not in (None, "disagree")]
    out["paths_no"] = (sum(x == "no" for x in q3), len(q3))
    return out


# ------------------------------------------------------------------ Tables 4 and 5: places (A2)
def grades():
    key = load_key("A2")
    if not key:
        line("A2 not built yet.")
        return None, {}, {}, (0, 0)
    kept, quality = annotations("A2", key)
    excluded = (sum(q["excluded"] for q in quality.values()), len(quality))
    if not kept:
        line("**A2: no file back yet**" + (" (all excluded by their controls)." if quality else "."))
        return key, {}, {}, excluded
    line(f"- agreement on pertinence: {agreement(kept, G, 'ordinal')}; on existence: {agreement(kept, E)}")
    grade, exists = {}, {}
    for rid in key["rows"]:
        gs = [int(float(a[rid][G])) for a in kept.values() if rid in a and a[rid].get(G, "") not in ("", "?")]
        if gs:
            grade[rid] = mean(gs)
        exists[rid] = consensus(kept, rid, E)
    return key, grade, exists, excluded


def by_list(key) -> dict:
    out = defaultdict(list)
    for rid, k in (key or {"rows": {}})["rows"].items():
        for l in k["lists"]:
            out[l].append((k["profile"], rid))
    return out


def worth(items, grade, which) -> tuple[int, int]:
    g = [grade[r] for pid, r in items if r in grade and pid in GT and kind(pid) == which]
    return sum(x >= 2 for x in g), len(g)


def per_profile_worth(items, grade) -> dict:
    per = defaultdict(list)
    for pid, r in items:
        if r in grade:
            per[pid].append(grade[r] >= 2)
    return {pid: mean(v) for pid, v in per.items()}


def table4(L: dict, key, grade, exists) -> dict:
    h("Table 4. Places (A2 and the benchmark)")
    bl = by_list(key)

    def discovery(rid):
        k = key["rows"][rid]
        p = {"qid": k["qid"], "name": k["name"], "lat": k["lat"], "lon": k["lon"], "id": k["place_id"]}
        return grade.get(rid, 0) >= 2 and not any(bench.matches(s, p) for s in GT[k["profile"]]["stops"])

    rows, tex, out = [], [], {}
    r100 = lambda v: "--" if v is None else f"{100 * v:.0f}"
    for lst in TAB4:
        items = [(pid, r) for pid, r in bl.get(lst, []) if pid in GT]
        wp, wt = worth(items, grade, "persons"), worth(items, grade, "themes")
        ex = [exists[r] for _, r in items if exists.get(r) not in (None, "disagree")]
        disc = sum(discovery(r) for _, r in items) if grade else None
        rec = recall(L, lst)
        rp = [v for pid, v in rec.items() if kind(pid) == "persons"]
        rt = [v for pid, v in rec.items() if kind(pid) == "themes"]
        out[lst] = {"wp": wp, "wt": wt, "gone": (sum(x == "no" for x in ex), len(ex)), "disc": disc,
                    "rp": mean(rp) if rp else None, "rt": mean(rt) if rt else None, "rec": rec,
                    "worth_pi": per_profile_worth(items, grade)}
        rows.append([PAPER[lst], pct(*wp), pct(*wt), pct(*out[lst]["gone"]), disc, out[lst]["rp"], out[lst]["rt"]])
        tex.append(f"{PAPER[lst]} & {p0(*wp)} & {p0(*wt)} & {p0(*out[lst]['gone'])} & {'--' if disc is None else disc} & "
                   f"{r100(out[lst]['rp'])} & {r100(out[lst]['rt'])} \\\\")
    table("table4_places", ["list", "worth a visit, persons", "worth a visit, themes", "do not exist", "discoveries",
                            "agreed stops, persons", "agreed stops, themes"], rows)
    tex_rows("table4", tex)
    return out


def table5(key, grade) -> None:
    h("Table 5. What each part does (A2)")
    bl = by_list(key)
    base = per_profile_worth([(p, r) for p, r in bl.get("C1_nami", []) if p in GT], grade)
    tests, vals = {}, {}
    for lst in TAB5:
        items = [(p, r) for p, r in bl.get(lst, []) if p in GT]
        vals[lst] = (worth(items, grade, "persons"), worth(items, grade, "themes"))
        if lst != "C1_nami":
            pi = per_profile_worth(items, grade)
            for which in ("persons", "themes"):
                tests[(lst, which)] = paired({k: v for k, v in pi.items() if kind(k) == which},
                                             {k: v for k, v in base.items() if kind(k) == which}, settings.PERMUTATIONS)
    adj = holm({k: v["p"] for k, v in tests.items() if v["n"]})
    rows, tex = [], []
    for lst, (wp, wt) in vals.items():
        star = lambda w: "$^{*}$" if adj.get((lst, w), 1) < 0.05 else ""
        test = lambda w: (f"{tests[(lst, w)]['diff']:+.3f}, p_Holm {adj[(lst, w)]:.3f}" if (lst, w) in adj else "")
        rows.append([TAB5[lst], pct(*wp), pct(*wt), test("persons"), test("themes")])
        tex.append(f"{TAB5[lst]} & {p0(*wp)}{star('persons')} & {p0(*wt)}{star('themes')} \\\\")
    table("table5_ablations", ["variant", "worth a visit, persons", "worth a visit, themes", "vs NAMI, persons", "vs NAMI, themes"], rows)
    tex_rows("table5", tex)


# ------------------------------------------------------------------ numbers for the text
def source_mix(L: dict, name: str) -> dict:
    c = defaultdict(int)
    for pr in single(L, name).values():
        for p in pr["places"]:
            for r in p["reasons"]:
                t = set(r.get("source_types", []))
                c["unreferenced" if "wikidata_unreferenced" in t else "referenced" if "wikidata_referenced" in t else "city"] += 1
    return c


def facts_errors() -> str:
    key = load_key("A3")
    if not key:
        return "A3 not built yet"
    h("A3: facts read from the city's sources")
    kept, _ = annotations("A3", key)
    if not kept:
        return "A3: no file back yet"
    wrong = det = 0
    for rid, k in key["rows"].items():
        if k.get("expect"):
            continue
        v = [consensus(kept, rid, c) for c in ("Q1 subject is the entity the text means",
                                                  "Q2 object is the entity the text means", "Q3 text states this relation")]
        if any(x in (None, "disagree") for x in v):
            continue
        det += 1
        wrong += "no" in v
    return pct(wrong, det)


def budget_line() -> str:
    raw, nb = settings.RUNS / "nami" / "C1_nami.json", settings.RUNS / "nami" / "NB_nobudget.json"
    if not raw.exists():
        return "C1 missing"
    c1 = {r["profile_id"]: r for r in json.loads(raw.read_text())["runs"] if "error" not in r}
    full = {r["profile_id"]: r for r in json.loads(nb.read_text())["runs"] if "error" not in r} if nb.exists() else {}
    stopped = cert = same = bad = 0
    for pid, r in c1.items():
        c = certify(r, settings.K)
        stopped += c["budget"]
        cert += c["budget"] and c["certified"]
        if pid in full:
            eq = {x["id"] for x in r["candidates"][:settings.K]} == {x["id"] for x in full[pid]["candidates"][:settings.K]}
            same += eq
            bad += c["certified"] and not eq
    tail = (f"; first ten unchanged without budget {same} of {len(full)}; certified but different {bad} (must be 0)"
            if full else "; run without budget (NB_nobudget) missing")
    return f"searches stopped by the budget {stopped} of {len(c1)}, of which certified {cert}" + tail


def text_numbers(L: dict, t3: dict, t4: dict, a2key, grade, a2_excluded) -> None:
    ex = t3.get("excluded", (0, 0))
    say("5.3, annotators excluded", f"{ex[0] + a2_excluded[0]} of {ex[1] + a2_excluded[1]} files")
    say("5.3, agreement (A1, Q2)", t3.get("kappa", "missing"))
    rng = []
    for fam in ("llm", "llmcheck", "hybrid"):
        for m in settings.MODELS:
            ms = [mean(r.values()) for r in (recall(L, n) for n in L if n.startswith(f"{fam}_{m}_run")) if r]
            if len(ms) > 1:
                rng.append(f"{fam}_{m}: agreed stops {min(ms):.3f}-{max(ms):.3f}")
    say("5.3, range over the three runs", "; ".join(rng) or "fewer than two runs")
    for lst in ("C1_nami", "llm_gpt_run1", "llm_gemini_run1"):
        if lst in t3:
            say(f"Q1, places whose reasons all hold, {PAPER[lst]}", pct(*t3[lst]["all"]))
    fails = defaultdict(int)
    for m in settings.MODELS:
        for pr in single(L, f"llm_{m}_run1").values():
            for p in pr["places"]:
                for r in p["reasons"]:
                    if r.get("auto") == "fails":
                        fails[r.get("check") == "not_on_page"] += 1
    say("Q1, failing reasons of the models whose page opens and does not state them", pct(fails[True], fails[True] + fails[False]))
    for lst in ("llm_gpt_run1", "llm_gemini_run1", "C1_nami"):
        if lst in t4:
            say(f"Q1, places that do not exist, {PAPER[lst]}", pct(*t4[lst]["gone"]))
    mix = source_mix(L, "C1_nami")
    n = sum(mix.values())
    say("Q1, NAMI's reasons rest on", f"city {pct(mix['city'], n)}; referenced {pct(mix['referenced'], n)}; unreferenced {pct(mix['unreferenced'], n)}")
    if "check_accept" in t3:
        say("Q1, check accepts, people find stated", pct(*t3["check_accept"]))
        say("Q1, check rejects, people find stated", pct(*t3["check_reject"]))
    say("Q1, facts from the city's texts with a wrong entity or relation", facts_errors())
    if "paths_no" in t3:
        say("Q1, NAMI's paths that do not link the place to the interest", pct(*t3["paths_no"]))
    for m in settings.MODELS:
        a, c = single(L, f"llm_{m}_run1"), single(L, f"llmcheck_{m}_run1")
        say(f"Q1, with the check, {m} alone keeps of its places",
            pct(sum(len(p["places"]) for p in c.values()), sum(len(p["places"]) for p in a.values())))
        if f"hybrid_{m}_run1" in t3 and f"llm_{m}_run1" in t3:
            say(f"Q1, places with >=2 reasons that hold, NAMI+{m} vs {m} alone",
                f"{pct(*t3[f'hybrid_{m}_run1']['two'])} vs {pct(*t3[f'llm_{m}_run1']['two'])}")
    if grade:
        for lst in ("C1_nami", "llm_gpt_run1"):
            say(f"Q2, worth a visit, {PAPER[lst]}", f"persons {pct(*t4[lst]['wp'])}; themes {pct(*t4[lst]['wt'])}")
        say("Q2, discoveries of NAMI", t4["C1_nami"]["disc"])
    for which in ("persons", "themes"):
        a = {k: v for k, v in t4.get("C1_nami", {}).get("rec", {}).items() if kind(k) == which}
        b = {k: v for k, v in t4.get("llm_gpt_run1", {}).get("rec", {}).items() if kind(k) == which}
        if a and b:
            t = paired(a, b, settings.PERMUTATIONS)
            say(f"Q2, agreed stops, {which}, NAMI vs GPT",
                f"{mean(a.values()):.3f} vs {mean(b.values()):.3f}; diff {t['diff']:+.3f} [{t['ci'][0]:.3f}, {t['ci'][1]:.3f}], p {t['p']:.3f}")
    ht = [v for k, v in t4.get("hybrid_gpt_run1", {}).get("rec", {}).items() if kind(k) == "themes"]
    if ht and t4.get("C1_nami", {}).get("rt") is not None:
        say("Q2, agreed stops on themes, NAMI+GPT vs NAMI", f"{mean(ht):.3f} vs {t4['C1_nami']['rt']:.3f}")
    if grade:
        bl = by_list(a2key)
        mp, mi = per_profile_worth(bl.get("M_pairs", []), grade), per_profile_worth(bl.get("M_interleave", []), grade)
        t = paired(mp, mi, settings.PERMUTATIONS)
        say("Q2, two interests: fused minus alternated, share worth a visit",
            f"{t['diff']:+.3f} [{t['ci'][0]:.3f}, {t['ci'][1]:.3f}], p {t['p']:.3f}, {t['n']} profiles" if t["n"] else "missing")
        for lst in ("Z2_wikidata", "Z2_llm_gpt_run1"):
            g = [grade[r] for _, r in bl.get(lst, []) if r in grade]
            say(f"Q2, Basel, worth a visit, {lst}", pct(sum(x >= 2 for x in g), len(g)))
    themes = [p for p in GT if kind(p) == "themes"]
    empty = [p for p, pr in single(L, "A3_no_derived").items() if kind(p) == "themes" and not pr["places"]]
    say("Q3, without derived seeds, themes with no place", f"{len(empty)} of {len(themes)}" if "A3_no_derived" in L else "missing")
    c1, c3 = single(L, "C1_nami"), single(L, "C3_noprov")
    unref = lambda p: bool(p["reasons"]) and "wikidata_unreferenced" in p["reasons"][0].get("source_types", [])
    ids = lambda d, pid: {q["id"] for q in d.get(pid, {}).get("places", [])}
    enter = [p for pid in c3 for p in c3[pid]["places"] if p["id"] not in ids(c1, pid)]
    leave = [p for pid in c1 for p in c1[pid]["places"] if p["id"] not in ids(c3, pid)]
    say("Q3, without provenance: ranked by an unreferenced statement, places that enter / that they replace",
        f"{pct(sum(map(unref, enter)), len(enter))} / {pct(sum(map(unref, leave)), len(leave))}")
    base, deltas = recall(L, "C1_nami"), []
    for lst in TAB5:
        if lst != "C1_nami" and lst in L:
            r = recall(L, lst)
            for which in ("persons", "themes"):
                ks = [k for k in r if k in base and kind(k) == which]
                if ks:
                    d = mean(r[k] for k in ks) - mean(base[k] for k in ks)
                    deltas.append((abs(d), f"{lst} {which} {100 * d:+.0f} points"))
    say("Q3, agreed stops, change by variant (largest first)", "; ".join(x for _, x in sorted(deltas, reverse=True)) or "missing")
    for name in sorted(n for n in L if n.startswith("C9_inject_t")):
        l = L[name]
        persons = sum(any(any("injected" in r.get("source_types", []) for r in p["reasons"]) for p in pr["places"])
                      for pr in l["profiles"].values())
        kept = total = 0
        for pid, pr in l["profiles"].items():
            x = ids(c1, pid)
            total += len(x)
            kept += len(x & {p["id"] for p in pr["places"]})
        say(f"Q3, fabricated source at trust {l['meta'].get('trust')}",
            f"persons with a place resting on it {persons}; places of NAMI kept {pct(kept, total)}")
    say("Q3, budget and certificates", budget_line())
    for m in settings.MODELS:
        rs = [r for pr in single(L, f"hybrid_{m}_run1").values() for p in pr["places"] for r in p["reasons"]
              if r["kind"] == "web" and r.get("auto") in ("holds", "fails")]
        say(f"Discussion, reasons {m} adds to NAMI's stops that do not hold", pct(sum(r["auto"] == "fails" for r in rs), len(rs)))
    say("Discussion, time of the check", "not measured: drop the sentence")
    h("Numbers for the text of Sect. 5, in the order of the paper")
    MD.extend(TEXT)


def benchmark_check() -> None:
    h("Benchmark (A4)")
    key = load_key("A4")
    if not key:
        line("A4 not built yet.")
        return
    kept, _ = annotations("A4", key)
    if not kept:
        line("**A4: no file back yet.**")
        return
    for col in ("Q1 source names this place for the interest", "Q2 map shows this place"):
        v = [x for x in (consensus(kept, r, col) for r, k in key["rows"].items() if not k.get("expect"))
             if x not in (None, "disagree")]
        line(f"- {col}: yes {pct(sum(x == 'yes' for x in v), len(v))}")


def per_interest(t4: dict) -> None:
    h("Per interest (the artifact: results/data/per_interest.csv)")
    rows = [[lst, pid, GT[pid]["label"], kind(pid), d["rec"].get(pid), d["worth_pi"].get(pid)]
            for lst, d in t4.items() for pid in sorted(GT)]
    table("per_interest", ["list", "interest", "label", "kind", "agreed stops recall", "share worth a visit"], rows)
    MD[-len(rows) - 3:] = ["(written to results/data/per_interest.csv)", ""]


def main() -> None:
    R.mkdir(parents=True, exist_ok=True)
    MD.clear()
    TEXT.clear()
    GT.clear()
    GT.update({i["id"]: i for i in bench.interests()})
    L = lists()
    if not L:
        print("no lists yet: run prepara.py")
        return
    bench.prepare([p for l in L.values() for pr in l["profiles"].values() for p in pr["places"]])
    MD.append("# Evaluation v2: results\n\nFrom lists/ and the filled workbooks only. Rows ready for LaTeX: results/tables/.")
    t3 = table3(L)
    h("A2: annotators")
    a2key, grade, exists, a2_excluded = grades()
    t4 = table4(L, a2key, grade, exists)
    table5(a2key, grade)
    text_numbers(L, t3, t4, a2key, grade, a2_excluded)
    benchmark_check()
    per_interest(t4)
    (R / "report.md").write_text("\n".join(MD) + "\n")
    print(f"-> {R / 'report.md'}")

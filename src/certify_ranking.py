"""Certificates of the ranking (paper, Sect. 4): when the search stops, the best value still waiting
for each seed, times lambda, bounds every path it did not reach (f_e), since a missed path adds at least
one hop beyond the first to a frontier path; a path below tau would be pruned anyway, so f_e < tau counts as 0. Every place y then has
    S_u(y) <= U(y) = 1 - prod_e (1 - max(s_{u,e}(y), f_e)),
and a place not reached at all has U = 1 - prod_e (1 - f_e); a place reached but not reported
(the report keeps the best ones) has U <= 1 - (1 - S_u(z)) prod_e (1 - f_e). The first k places are exactly the k best
when the lowest score among them is at least the highest bound outside them.

Reports, over the benchmark interests: how many searches ended by exhausting the frontier (exact by
construction) or by the budget; how many first-ten lists are certified exact; and the largest gap
U(y) - S_u(y) among the first ten places.

Run: python src/certify_ranking.py [--run output/evaluation_best_first.WT_bounds.json]"""

from __future__ import annotations

import argparse
import json
import statistics

import config


def certify(run: dict, k: int = 10) -> dict:
    tau = run["params"]["valuation"]["tau"]
    seeds = [s["label"] for s in run["profile"]["seeds"]]
    fm = run["stats"].get("frontier_max", {})
    # every path left in the frontier has at least one triple (the seeds are expanded in the first
    # round), so a missed path adds a hop to it and is worth at most lambda times its value
    lam = run["params"]["valuation"]["lam"]
    f = {e: (lam * fm.get(e, 0.0) if lam * fm.get(e, 0.0) >= tau else 0.0) for e in seeds}
    cands = run.get("candidates", [])

    def upper(per_seed: dict) -> float:
        miss = 1.0
        for e in seeds:
            miss *= 1.0 - max(per_seed.get(e, 0.0), f[e])
        return 1.0 - miss

    top, rest = cands[:k], cands[k:]
    unseen = upper({})
    # a place the report leaves out scores S(z) <= the last one reported, and since
    # 1 - max(a, b) >= (1 - a)(1 - b), its bound is at most 1 - (1 - S(z)) prod_e (1 - f_e)
    last = cands[-1]["score"] if cands else 0.0
    miss_f = 1.0
    for e in seeds:
        miss_f *= 1.0 - f[e]
    left_out = 1.0 - (1.0 - last) * miss_f if run.get("candidates_total", len(cands)) > len(cands) else 0.0
    outside = max([upper(c["per_seed"]) for c in rest] + [unseen, left_out])
    low = min((c["score"] for c in top), default=1.0)
    gaps = [upper(c["per_seed"]) - c["score"] for c in top]
    return {"budget": run["stats"].get("budget_exhausted", False), "certified": low >= outside - 1e-9 or not top,   # ties: the first ten are one of the best ten
            "max_gap": max(gaps, default=0.0), "places": len(top)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default=str(config.ROOT_DIR / "output" / "evaluation_best_first.WT_bounds.json"))
    args = ap.parse_args()
    answered = {a["interest_id"] for a in json.loads((config.ROOT_DIR / "output" / "llm_tour_reasons_gpt.json").read_text())["answers"]}
    runs = [r for r in json.loads(open(args.run).read())["runs"] if "error" not in r and r["interests"][0] in answered]
    res = {r["interests"][0]: certify(r) for r in runs}
    with_places = [v for v in res.values() if v["places"]]
    out = {"interests": len(res), "with places": len(with_places),
           "budget exhausted": sum(v["budget"] for v in res.values()),
           "frontier exhausted (exact)": sum(not v["budget"] for v in res.values()),
           "first ten certified exact": sum(v["certified"] for v in with_places),
           "max gap among first ten, median": round(statistics.median(v["max_gap"] for v in with_places), 4),
           "max gap among first ten, max": round(max(v["max_gap"] for v in with_places), 4),
           "per interest": res}
    print(json.dumps({k: v for k, v in out.items() if k != "per interest"}, indent=1))
    (config.ROOT_DIR / "output" / "certify_ranking.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

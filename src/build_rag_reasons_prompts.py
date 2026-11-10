"""NAMI + a language model on the task of round 5 (evaluation/llm_baseline/tour_reasons/): for
each interest, the model gets NAMI's first ten stops with every reason NAMI shows (all distinct
paths, each fact with its source) and composes the walk: it may keep, drop or reorder NAMI's
stops, add reasons to them and add places, under the rules of round 5 (one fact per reason, each
with a URL and the sentence of that page quoted exactly; no list of kinds of reason). Only the
graph's stops and facts are given in addition to the interest. The answers are checked by
process_rag_reasons.py, which keeps only the added reasons whose quote is on the cited page.

Writes evaluation/llm_rag_reasons/PROMPT_RAG_REASONS_<n>.txt, context.json (which stop each id
stands for) and empty answer files.

The prompt is part of the design of the hybrid configuration: variants are tried on the
development interests (--set dev) only; the chosen one is frozen and run on the test interests
(--set test), on which the results are reported. v1 (the first wording, "you may add reasons") is
kept in evaluation/llm_rag_reasons/v1_initial/; v2 asks for all the reasons of every place as
strongly as round 5 does; v3 adds to v2 that every retrieved stop worth a visit is kept.

Run: python src/build_rag_reasons_prompts.py --variant v2 --set dev|test [--per-stop 8]"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import config

OUT = config.ROOT_DIR / "evaluation" / "llm_rag_reasons"
BASE = config.ROOT_DIR / "evaluation" / "llm_baseline"

INSTRUCTIONS = """You are planning walking tours in the city of Zurich, Switzerland (the city itself, not the canton; Kilchberg, Küsnacht and other lakeside towns next to the city count).

For each interest below you get the stops that a knowledge-graph system retrieved, each with a value (0-1, higher = stronger link) and its reasons: chains of facts that link it to the interest, each fact with its source. Some interests have no retrieved stops.

Compose, for each interest, the list of places a visitor interested in it should visit on a walk, and for each place ALL the reasons that link it to the interest. Rules:
- Retrieved stops ("graph" stops): you may keep them, drop them, or reorder them. Never change their reasons or sources. Refer to a kept stop by its id (e.g. "S3").
- The system knows only some of the reasons. For every place you list, kept or new, give ALL the further reasons that link it to the interest, beyond those the system gives.
- You may add places the system did not retrieve.
- Only real, physical places that can be visited or seen today (buildings, squares, monuments, plaques, graves, museums, parks). No events, books, works of art held in collections, or places outside the area.
- At most 10 places per interest, most important first. Give fewer if you are not sure of more; do not fill the list.
- One reason states one fact (if a sentence states two facts, give two reasons). For each reason give the source: the URL of a web page that states it and the sentence of that page, quoted exactly in its original language, that states it.
- A reason without a URL and an exact quote is not allowed; leave it out. A new place without any such reason is not allowed; leave it out.

Answer ONLY with JSON in this form, one object per interest, in the same order:
[{{"interest_id": "Q937", "interest": "Albert Einstein", "places": [
  {{"rank": 1, "origin": "graph", "id": "S1", "reasons": [{{"reason": "...", "url": "https://...", "quote": "..."}}]}},
  {{"rank": 2, "origin": "new", "name": "...", "address": "...", "reasons": [{{"reason": "...", "url": "https://...", "quote": "..."}}]}}
]}}]

Interests:
{interests}"""


KEEP = """- Keep every retrieved stop that is worth a visit for this interest; drop only those that are not.
"""


def hop_text(h: dict) -> str:
    src = h.get("source") or "Wikidata"
    ev = f' -- "{h["evidence"][:200]}"' if h.get("evidence") else ""
    role = f" (as {h['role']})" if h.get("role") else ""
    return f"{h['from']} --{h['via']}{role}--> {h['to']} (source: {src}){ev}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", type=Path, default=config.ROOT_DIR / "output" / "evaluation_best_first.WT.json")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--blocks", type=int, default=7)
    ap.add_argument("--per-stop", type=int, default=8, help="reasons shown per stop, best first")
    ap.add_argument("--set", choices=("dev", "test", "all"), default="all",
                    help="dev: to design the prompt; test: the frozen prompt, for the results")
    ap.add_argument("--variant", default="v2")
    args = ap.parse_args()
    sets = {i["id"]: i.get("set") for i in json.loads((config.ROOT_DIR / "evaluation" / "ground_truth_zurich.json").read_text())["interests"]}
    out_dir = OUT / args.variant / args.set
    runs = {r["interests"][0]: r for r in json.loads(args.run.read_text())["runs"] if "error" not in r}
    # the interests of round 5, in its order
    lines = [l for l in (BASE / "PROMPT.txt").read_text().splitlines() if re.match(r"^- .*\(id Q\d+\)$", l)]
    interests = [(re.search(r"\(id (Q\d+)\)$", l).group(1), l[2:]) for l in lines]
    interests = [(q, t) for q, t in interests if args.set == "all" or sets.get(q) == args.set]
    if args.set == "dev":
        args.blocks = 2
    context, parts = {}, []
    for qid, title in interests:
        cands = runs.get(qid, {}).get("candidates", [])[:args.k]
        out = [f"\n## {title}"]
        if not cands:
            out.append("No retrieved stops.")
        context[qid] = []
        for n, c in enumerate(cands, 1):
            sid = f"S{n}"
            context[qid].append({"id": sid, "candidate_id": c["id"], "label": c["label"], "score": c["score"]})
            where = f", {c['located_at']}" if c.get("located_at") else ""
            out.append(f"- {sid}: {c['label']}{where} (value {c['score']:.2f})")
            reasons = [("because", e) for e in c["explanations"]] + [(f"also here ({r['label']})", r["explanation"]) for r in c.get("reasons", [])]
            reasons.sort(key=lambda t: -t[1]["value"])
            for tag, e in reasons[:args.per_stop]:
                out.append(f"    {tag}: " + "; ".join(hop_text(h) for h in e["hops"]))
            if len(reasons) > args.per_stop:
                out.append(f"    ... and {len(reasons) - args.per_stop} more reasons of lower value")
        parts.append("\n".join(out))
    out_dir.mkdir(parents=True, exist_ok=True)
    size = -(-len(parts) // args.blocks)
    for b in range(args.blocks):
        block = parts[b * size:(b + 1) * size]
        if block:
            text = INSTRUCTIONS
            if args.variant == "v3":           # v2 + keep the stops worth a visit
                anchor = "- The system knows only some of the reasons."
                text = text.replace(anchor, KEEP.replace("{", "{{").replace("}", "}}") + anchor)
            (out_dir / f"PROMPT_RAG_REASONS_{b + 1}.txt").write_text(text.format(interests="\n".join(block)) + "\n")
            for m in ("gemini", "gpt"):
                f = out_dir / f"{m}_rag_reasons_block{b + 1}_2026-10-08.json"
                if not f.exists():
                    f.write_text("")
    (out_dir / "context.json").write_text(json.dumps({"run": str(args.run), "k": args.k, "per_stop": args.per_stop,
                                                  "context": context}, indent=1, ensure_ascii=False))
    print(len(parts), "interests in", args.blocks, "prompts ->", out_dir,
          [len((out_dir / f"PROMPT_RAG_REASONS_{b + 1}.txt").read_text()) for b in range(args.blocks) if (out_dir / f"PROMPT_RAG_REASONS_{b + 1}.txt").exists()])


if __name__ == "__main__":
    main()

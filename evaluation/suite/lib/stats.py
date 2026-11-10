"""Statistics of the suite, written out so that every number can be traced: Wilson intervals for
proportions, bootstrap intervals for means, paired sign-flip permutation tests, Holm correction, Cohen's
kappa and Krippendorff's alpha (nominal or ordinal)."""

from __future__ import annotations

import math
import random
from collections import Counter
from itertools import combinations


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def mean(xs) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else float("nan")


def bootstrap(xs: list[float], n: int = 10_000, seed: int = 1) -> tuple[float, float]:
    if not xs:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    ms = sorted(mean(rng.choice(xs) for _ in xs) for _ in range(n))
    return ms[int(0.025 * n)], ms[min(n - 1, int(0.975 * n))]


def paired(a: dict, b: dict, n: int = 10_000, seed: int = 2) -> dict:
    """Paired comparison over the keys both have: mean difference a - b, bootstrap 95% CI, and the
    two-sided sign-flip permutation p-value."""
    keys = sorted(set(a) & set(b))
    d = [a[k] - b[k] for k in keys]
    if not d:
        return {"n": 0, "diff": float("nan"), "ci": (float("nan"), float("nan")), "p": float("nan")}
    rng = random.Random(seed)
    obs = abs(sum(d))
    hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= obs - 1e-12 for _ in range(n))
    return {"n": len(d), "diff": mean(d), "ci": bootstrap(d, n, seed), "p": (hits + 1) / (n + 1)}


def holm(ps: dict) -> dict:
    """Holm-adjusted p-values for a family of tests, {name: p} -> {name: p_adj}."""
    items = sorted(ps.items(), key=lambda kv: kv[1])
    out, run = {}, 0.0
    m = len(items)
    for i, (k, p) in enumerate(items):
        run = max(run, min(1.0, (m - i) * p))
        out[k] = run
    return out


def kappa(a: list, b: list) -> float:
    """Cohen's kappa of two annotators on the same items (blank answers left out by the caller)."""
    n = len(a)
    if n == 0:
        return float("nan")
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in set(a) | set(b)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def alpha(units: dict[str, list], level: str = "nominal") -> float:
    """Krippendorff's alpha. `units`: item -> the values the annotators gave it (two or more).
    level: 'nominal' or 'ordinal' (values must then be numbers, e.g. 0-3)."""
    units = {u: [v for v in vs if v is not None and v != ""] for u, vs in units.items()}
    units = {u: vs for u, vs in units.items() if len(vs) >= 2}
    if not units:
        return float("nan")
    values = sorted({v for vs in units.values() for v in vs})
    o = Counter()                                    # coincidences
    for vs in units.values():
        m = len(vs)
        for i, j in ((i, j) for i in range(m) for j in range(m) if i != j):
            o[(vs[i], vs[j])] += 1 / (m - 1)
    nc = Counter()
    for (c, _), w in o.items():
        nc[c] += w
    n = sum(nc.values())
    if level == "ordinal":
        def delta(c, k):
            lo, hi = sorted((values.index(c), values.index(k)))
            s = sum(nc[values[g]] for g in range(lo, hi + 1)) - (nc[c] + nc[k]) / 2
            return s * s
    else:
        def delta(c, k):
            return 0.0 if c == k else 1.0
    do = sum(w * delta(c, k) for (c, k), w in o.items())
    de = sum(nc[c] * nc[k] * delta(c, k) for c in values for k in values) / (n - 1)
    return 1 - do / de if de else 1.0


def pairwise_kappa(answers: dict[str, dict]) -> dict:
    """answers: annotator -> {item: value}. Cohen's kappa for every pair on the items both answered."""
    out = {}
    for x, y in combinations(sorted(answers), 2):
        common = [i for i in answers[x] if i in answers[y] and answers[x][i] not in (None, "")
                  and answers[y][i] not in (None, "")]
        if common:
            out[f"{x}~{y}"] = (len(common), kappa([answers[x][i] for i in common], [answers[y][i] for i in common]))
    return out

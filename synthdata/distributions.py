"""Samplers for field values and child-row counts.

Every sampler is built from a small JSON-style spec, so the same vocabulary is
used in released statistics, user overrides and built-in defaults. A spec is a
dict with either a statistics key (``frequencies``, ``histogram``,
``quantiles``) or a ``distribution`` name plus its parameters.
"""

from __future__ import annotations

import bisect
import math
import random
from typing import Any, Callable

Sampler = Callable[[random.Random], Any]

NAMED_DISTRIBUTIONS = (
    "uniform",
    "normal",
    "lognormal",
    "poisson",
    "exponential",
    "zipf",
    "weights",
    "constant",
)


class SpecError(ValueError):
    """Raised when a distribution or statistics spec cannot be used."""


def _clip(value: float, lo: float | None, hi: float | None) -> float:
    if lo is not None and value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value


def _weighted_choice(values: list[Any], weights: list[float]) -> Sampler:
    total = float(sum(weights))
    if not values or total <= 0:
        raise SpecError("a weighted choice needs at least one positive weight")
    cumulative = []
    running = 0.0
    for w in weights:
        running += w
        cumulative.append(running)

    def sample(rng: random.Random) -> Any:
        i = bisect.bisect_right(cumulative, rng.random() * total)
        return values[min(i, len(values) - 1)]

    return sample


def _poisson(rng: random.Random, mean: float) -> int:
    # Knuth's method for small means, normal approximation for large ones.
    if mean <= 0:
        return 0
    if mean > 50:
        return max(0, int(round(rng.gauss(mean, math.sqrt(mean)))))
    limit = math.exp(-mean)
    k, p = 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def build_sampler(spec: dict, *, lo: float | None = None, hi: float | None = None,
                  categories: list[Any] | None = None) -> Sampler:
    """Return a function ``rng -> value`` for a statistics or distribution spec.

    ``lo`` and ``hi`` clip numeric draws to the field's constraints.
    ``categories`` is the list of allowed values, used by ``zipf`` and as the
    fallback support for ``weights`` given as a list.
    """
    if "frequencies" in spec:
        freqs = spec["frequencies"]
        values = list(freqs.keys())
        return _weighted_choice(values, [float(freqs[v]) for v in values])

    if "histogram" in spec:
        edges = spec["histogram"]["edges"]
        counts = spec["histogram"]["counts"]
        if len(edges) != len(counts) + 1:
            raise SpecError("histogram needs one more edge than counts")
        pick_bin = _weighted_choice(list(range(len(counts))), [float(c) for c in counts])

        def sample_hist(rng: random.Random) -> float:
            i = pick_bin(rng)
            return rng.uniform(edges[i], edges[i + 1])

        return sample_hist

    if "quantiles" in spec:
        # Piecewise-uniform between consecutive quantiles, e.g. {"0": 18, "0.5": 52, "1": 99}.
        points = sorted((float(p), float(v)) for p, v in spec["quantiles"].items())
        if len(points) < 2 or points[0][0] != 0.0 or points[-1][0] != 1.0:
            raise SpecError("quantiles need at least the 0 and 1 points")
        probs = [p for p, _ in points]
        vals = [v for _, v in points]

        def sample_q(rng: random.Random) -> float:
            u = rng.random()
            i = max(1, bisect.bisect_right(probs, u))
            i = min(i, len(probs) - 1)
            p0, p1 = probs[i - 1], probs[i]
            frac = 0.0 if p1 == p0 else (u - p0) / (p1 - p0)
            return vals[i - 1] + frac * (vals[i] - vals[i - 1])

        return sample_q

    name = spec.get("distribution")
    if name is None:
        raise SpecError(f"spec has no statistics or distribution: {spec!r}")

    if name == "constant":
        value = spec["value"]
        return lambda rng: value

    if name == "uniform":
        a = spec.get("min", lo if lo is not None else 0)
        b = spec.get("max", hi if hi is not None else 1)
        return lambda rng: rng.uniform(a, b)

    if name == "normal":
        mu, sd = float(spec["mean"]), float(spec["sd"])
        return lambda rng: _clip(rng.gauss(mu, sd), spec.get("min", lo), spec.get("max", hi))

    if name == "lognormal":
        # Parameterised by the median and the geometric sd, which are easier to state than mu/sigma.
        mu = math.log(float(spec["median"]))
        sigma = math.log(float(spec.get("gsd", 2.0)))
        return lambda rng: _clip(rng.lognormvariate(mu, sigma), spec.get("min", lo), spec.get("max", hi))

    if name == "exponential":
        mean = float(spec["mean"])
        return lambda rng: _clip(rng.expovariate(1.0 / mean), spec.get("min", lo), spec.get("max", hi))

    if name == "poisson":
        mean = float(spec["mean"])
        return lambda rng: int(_clip(_poisson(rng, mean), spec.get("min", lo), spec.get("max", hi)))

    if name == "zipf":
        support = spec.get("values", categories)
        if not support:
            raise SpecError("zipf needs 'values' or a field with an enum")
        s = float(spec.get("s", 1.0))
        return _weighted_choice(list(support), [1.0 / (rank ** s) for rank in range(1, len(support) + 1)])

    if name == "weights":
        weights = spec["weights"]
        if isinstance(weights, dict):
            return _weighted_choice(list(weights.keys()), [float(w) for w in weights.values()])
        support = spec.get("values", categories)
        if not support or len(support) != len(weights):
            raise SpecError("list weights need 'values' (or an enum) of the same length")
        return _weighted_choice(list(support), [float(w) for w in weights])

    raise SpecError(f"unknown distribution {name!r}; expected one of {', '.join(NAMED_DISTRIBUTIONS)}")

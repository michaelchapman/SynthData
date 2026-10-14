"""Build a profile from real data, applying disclosure control.

This is the part a custodian runs inside their TRE. It reads a Frictionless
data package (CSV files plus a datapackage.json with schemas) and writes a
profile at tier 0 (structure and rounded row counts) or tier 1 (plus
one-column statistics). Every released count is suppressed below the threshold
and rounded to the rounding base; histogram bins are merged until each holds at
least the threshold; children-per-parent tails are top-coded.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
from pathlib import Path
from typing import Any

from .profile import PROFILE_VERSION, foreign_keys, load_json

NUMERIC = ("integer", "number", "year")
TEMPORAL = ("date", "datetime")


def _round_to(n: float, base: int) -> int:
    return int(base * round(n / base)) if base > 1 else int(n)


def _nice_width(span: float, bins: int) -> float:
    raw = span / bins if span > 0 else 1.0
    exp = math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * 10 ** exp >= raw:
            return m * 10 ** exp
    return 10 ** (exp + 1)


def _read_rows(base: Path, resource: dict) -> list[dict]:
    with open(base / resource["path"], newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _parse(ftype: str, raw: str) -> Any:
    if raw is None or raw == "":
        return None
    if ftype in ("integer", "year"):
        return int(float(raw))
    if ftype == "number":
        return float(raw)
    if ftype == "date":
        return dt.date.fromisoformat(raw[:10])
    if ftype == "datetime":
        return dt.datetime.fromisoformat(raw)
    return raw


def _frequencies(values: list[Any], threshold: int, rounding: int) -> tuple[dict, int]:
    counts: dict[str, int] = {}
    for v in values:
        key = str(v).lower() if isinstance(v, bool) else str(v)
        counts[key] = counts.get(key, 0) + 1
    released, suppressed = {}, 0
    for key in sorted(counts, key=lambda k: (-counts[k], k)):
        c = counts[key]
        if c < threshold:
            suppressed += 1
            continue
        released[key] = max(_round_to(c, rounding), threshold)
    return released, suppressed


def _merge_small_bins(edges: list, counts: list[int], threshold: int) -> tuple[list, list[int]]:
    edges, counts = list(edges), list(counts)
    changed = True
    while changed and len(counts) > 1:
        changed = False
        for i, c in enumerate(counts):
            if c < threshold:
                # Merge with the smaller neighbour so bins stay as even as possible.
                if i == 0:
                    j = 1
                elif i == len(counts) - 1:
                    j = i - 1
                else:
                    j = i - 1 if counts[i - 1] <= counts[i + 1] else i + 1
                lo, hi = min(i, j), max(i, j)
                counts[lo:hi + 1] = [counts[lo] + counts[hi]]
                del edges[hi]
                changed = True
                break
    return edges, counts


def _histogram(ftype: str, values: list[Any], threshold: int, rounding: int, bins: int,
               constraints: dict | None = None) -> dict | None:
    if len(values) < threshold:
        return None
    if ftype in TEMPORAL:
        days = [v.toordinal() if isinstance(v, dt.date) and not isinstance(v, dt.datetime)
                else v.date().toordinal() for v in values]
        lo, hi = min(days), max(days)
        first, last = dt.date.fromordinal(lo), dt.date.fromordinal(hi)
        # Year bins over long spans, month bins otherwise; both hide the exact extremes.
        if (last.year - first.year) >= 2:
            starts = [dt.date(y, 1, 1) for y in range(first.year, last.year + 2)]
        else:
            starts, y, m = [], first.year, first.month
            while dt.date(y, m, 1) <= last:
                starts.append(dt.date(y, m, 1))
                y, m = (y + 1, 1) if m == 12 else (y, m + 1)
            starts.append(dt.date(y, m, 1))
        edges_num = [s.toordinal() for s in starts]
        data = days
    else:
        lo, hi = min(values), max(values)
        width = _nice_width(hi - lo, bins)
        start = math.floor(lo / width) * width
        n = int(math.floor((hi - start) / width)) + 1
        edges_num = [start + k * width for k in range(n + 1)]
        data = values
    # Keep the outer edges inside the field's declared range, so released bins never imply impossible values.
    c = constraints or {}
    cmin, cmax = c.get("minimum"), c.get("maximum")
    if ftype in TEMPORAL:
        cmin = dt.date.fromisoformat(str(cmin)[:10]).toordinal() if cmin is not None else None
        cmax = dt.date.fromisoformat(str(cmax)[:10]).toordinal() + 1 if cmax is not None else None
    elif ftype in ("integer", "year") and cmax is not None:
        cmax = cmax + 1  # whole numbers: the top bin runs up to, not including, max + 1
    if cmin is not None and edges_num[0] < cmin < edges_num[1]:
        edges_num[0] = cmin
    if cmax is not None and edges_num[-2] < cmax < edges_num[-1]:
        edges_num[-1] = cmax
    counts = [0] * (len(edges_num) - 1)
    for v in data:
        i = min(max(0, _bisect(edges_num, v)), len(counts) - 1)
        counts[i] += 1
    edges_num, counts = _merge_small_bins(edges_num, counts, threshold)
    counts = [max(_round_to(c, rounding), threshold) for c in counts]
    if ftype in TEMPORAL:
        edges = [dt.date.fromordinal(int(e)).isoformat() for e in edges_num]
    elif ftype in ("integer", "year"):
        edges = [int(e) if float(e).is_integer() else e for e in edges_num]
    else:
        edges = [round(e, 10) for e in edges_num]
    return {"edges": edges, "counts": counts}


def _bisect(edges: list, value: float) -> int:
    lo, hi = 0, len(edges) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if edges[mid + 1] <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def _top_code(counts: dict[int, int], threshold: int) -> dict[int, int]:
    """Merge the long tail of children-per-parent into the largest safe value."""
    keys = sorted(counts)
    cut = next((k for k in keys if counts[k] < threshold), None)
    if cut is None:
        return dict(counts)
    merged = {k: counts[k] for k in keys if k < cut}
    tail = sum(counts[k] for k in keys if k >= cut)
    # Fold the tail into the cut value, stepping down while it is still too small.
    while merged and tail < threshold:
        top = max(merged)
        tail += merged.pop(top)
        cut = top
    merged[cut] = tail
    return merged


def profile_package(package_path: str | Path, *, tier: int = 1, threshold: int = 10, rounding: int = 5,
                    max_categories: int = 50, bins: int = 10, name: str | None = None) -> dict:
    """Profile every resource in a data package and return the profile dict."""
    package_path = Path(package_path)
    base = package_path.parent
    package = load_json(package_path)
    data = {res["name"]: _read_rows(base, res) for res in package["resources"]}

    resources = []
    for res in package["resources"]:
        rows = data[res["name"]]
        schema = res["schema"]
        out: dict[str, Any] = {"name": res["name"], "schema": schema}
        if res.get("rules"):
            out["rules"] = res["rules"]
        stats: dict[str, Any] = {"rowCount": _round_to(len(rows), rounding)}

        if tier >= 1:
            pk = schema.get("primaryKey")
            pk_fields = set(pk if isinstance(pk, list) else [pk] if pk else [])
            fks = [fk for fk in foreign_keys(res) if fk["resource"] != res["name"]]
            skip = pk_fields | {fk["field"] for fk in fks}
            field_stats: dict[str, Any] = {}
            for f in schema["fields"]:
                fname, ftype = f["name"], f.get("type", "string")
                c = f.get("constraints", {})
                if fname in skip or c.get("unique"):
                    continue
                parsed = [_parse(ftype, r.get(fname, "")) for r in rows]
                present = [v for v in parsed if v is not None]
                entry: dict[str, Any] = {}
                n_missing = len(parsed) - len(present)
                if parsed:
                    released_missing = 0 if n_missing < threshold else _round_to(n_missing, rounding)
                    entry["missing"] = round(released_missing / len(parsed), 3)
                distinct = len(set(present))
                categorical = "enum" in c or ftype in ("string", "boolean") or f.get("categorical") \
                    or (ftype in NUMERIC and distinct <= max_categories and ftype != "number")
                if categorical and distinct <= max_categories:
                    freqs, suppressed = _frequencies(present, threshold, rounding)
                    if freqs:
                        entry["frequencies"] = freqs
                    if suppressed:
                        entry["suppressedCategories"] = suppressed
                elif ftype in NUMERIC + TEMPORAL:
                    hist = _histogram(ftype, present, threshold, rounding, bins, c)
                    if hist:
                        entry["histogram"] = hist
                if entry:
                    field_stats[fname] = entry
            stats["fields"] = field_stats

            if fks:
                driver = fks[0]
                parent_keys = [r[driver["target"]] for r in data[driver["resource"]]]
                per_parent = {k: 0 for k in parent_keys}
                for r in rows:
                    if r[driver["field"]] in per_parent:
                        per_parent[r[driver["field"]]] += 1
                dist: dict[int, int] = {}
                for n in per_parent.values():
                    dist[n] = dist.get(n, 0) + 1
                dist = _top_code(dist, threshold)
                stats["childrenPerParent"] = {"frequencies": {
                    str(k): max(_round_to(v, rounding), threshold) for k, v in sorted(dist.items()) if v >= threshold
                }}
        out["statistics"] = stats
        resources.append(out)

    return {
        "profile": PROFILE_VERSION,
        "name": name or package.get("name", "dataset"),
        "title": package.get("title"),
        "version": package.get("version"),
        "tier": tier,
        "disclosure": {
            "threshold": threshold,
            "rounding": rounding,
            "methods": [
                "row counts rounded to the rounding base",
                "categories below the threshold withheld",
                "histogram bins merged until each holds at least the threshold; date bins are whole years or months",
                "children-per-parent tail top-coded into the largest value meeting the threshold",
                "missing counts below the threshold released as zero",
            ],
        },
        "resources": resources,
    }

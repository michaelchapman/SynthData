"""Generate synthetic tables from a profile.

For every field the generator uses the best information available, in order:

1. released statistics in the profile;
2. a distribution the user chose in an overrides file;
3. a default worked out from the field's type and constraints.

Child tables are built per parent row, so foreign keys are always valid. The
result records which source each field and table size came from.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
from pathlib import Path
from typing import Any

from .distributions import Sampler, build_sampler
from .profile import PROFILE_VERSION, foreign_keys, resource_order, validate_profile

DEFAULTS = {
    "rowCount": 100,
    "missing": 0.02,
    "childrenPerParent": {"distribution": "poisson", "mean": 2},
    "integer": {"min": 0, "max": 1000},
    "number": {"min": 0.0, "max": 100.0},
    "year": {"min": 1920, "max": 2020},
    "date": {"min": "2010-01-01", "max": "2024-12-31"},
}

EPOCH = dt.datetime(1970, 1, 1)


# --- type handling -------------------------------------------------------

def _to_internal(ftype: str, value: Any) -> Any:
    """Turn a stats/constraint value into the numeric form samplers work in."""
    if value is None:
        return None
    if ftype == "date":
        return dt.date.fromisoformat(str(value)).toordinal() if isinstance(value, str) else value
    if ftype == "datetime":
        if isinstance(value, str):
            return (dt.datetime.fromisoformat(value) - EPOCH).total_seconds()
        return value
    return value


def _cast(ftype: str, value: Any, field: dict) -> Any:
    """Turn a sampled value into its output form for the field's type."""
    if value is None:
        return None
    if ftype in ("integer", "year"):
        return int(round(float(value)))
    if ftype == "number":
        decimals = field.get("decimals", 2)
        return round(float(value), decimals)
    if ftype == "boolean":
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes")
        return bool(value)
    if ftype == "date":
        if isinstance(value, str):
            return value
        return dt.date.fromordinal(int(round(value))).isoformat()
    if ftype == "datetime":
        if isinstance(value, str):
            return value
        return (EPOCH + dt.timedelta(seconds=float(value))).isoformat(timespec="seconds")
    return str(value)


def _normalise_spec(ftype: str, spec: dict) -> dict:
    """Convert date and datetime values inside a spec to numbers."""
    if ftype not in ("date", "datetime"):
        return spec
    spec = dict(spec)
    if "histogram" in spec:
        h = spec["histogram"]
        spec["histogram"] = {"edges": [_to_internal(ftype, e) for e in h["edges"]], "counts": h["counts"]}
    if "quantiles" in spec:
        spec["quantiles"] = {p: _to_internal(ftype, v) for p, v in spec["quantiles"].items()}
    for key in ("min", "max", "mean", "median"):
        if key in spec:
            spec[key] = _to_internal(ftype, spec[key])
    return spec


def _cast_category_keys(ftype: str, spec: dict) -> dict:
    """JSON object keys are always strings; cast frequency keys to the field type."""
    if "frequencies" not in spec or ftype in ("string", "date", "datetime"):
        return spec
    spec = dict(spec)
    spec["frequencies"] = {_cast(ftype, k, {}): v for k, v in spec["frequencies"].items()}
    return spec


def _clip_value(value: Any, lo: Any, hi: Any) -> Any:
    if not isinstance(value, (int, float)):
        return value
    if lo is not None and value < lo:
        return lo
    if hi is not None and value > hi:
        return hi
    return value


# --- per-field planning ---------------------------------------------------

def _default_spec(field: dict) -> tuple[dict | None, str]:
    """A spec for a field with nothing else to go on, and its label."""
    ftype = field.get("type", "string")
    c = field.get("constraints", {})
    if "enum" in c:
        return {"weights": {str(v): 1 for v in c["enum"]}, "distribution": "weights"}, "default:equal-weights"
    if ftype == "boolean":
        return {"distribution": "weights", "weights": {"true": 1, "false": 1}}, "default:equal-weights"
    if ftype in ("integer", "number", "year", "date", "datetime"):
        base = DEFAULTS.get("date" if ftype == "datetime" else ftype, DEFAULTS["number"])
        lo = c.get("minimum", base["min"])
        hi = c.get("maximum", base["max"])
        return {"distribution": "uniform", "min": lo, "max": hi}, "default:uniform"
    return None, "default:labelled-string"


def _label(spec: dict, origin: str) -> str:
    for key in ("frequencies", "histogram", "quantiles"):
        if key in spec:
            return f"{origin}:{key}"
    return f"{origin}:{spec.get('distribution', 'unknown')}"


class FieldPlan:
    """How one field will be filled."""

    def __init__(self, field: dict, stats: dict | None, override: dict | None, defaults: dict):
        self.field = field
        self.name = field["name"]
        self.type = field.get("type", "string")
        c = field.get("constraints", {})
        self.required = bool(c.get("required"))
        self.max_length = c.get("maxLength")

        stats = stats or {}
        override = override or {}
        dist_keys = ("frequencies", "histogram", "quantiles", "distribution")
        has_stats = any(k in stats for k in dist_keys)
        has_override = any(k in override for k in dist_keys)

        if has_override and (override.get("replaceStatistics") or not has_stats):
            spec, self.source = override, _label(override, "override")
        elif has_stats:
            spec, self.source = stats, _label(stats, "statistics")
        else:
            spec, self.source = _default_spec(field)

        if self.required:
            self.missing, self.missing_source = 0.0, "constraint:required"
        elif "missing" in override and (override.get("replaceStatistics") or "missing" not in stats):
            self.missing, self.missing_source = float(override["missing"]), "override"
        elif "missing" in stats:
            self.missing, self.missing_source = float(stats["missing"]), "statistics"
        else:
            self.missing, self.missing_source = float(defaults["missing"]), "default"

        self.sampler: Sampler | None = None
        if spec is not None:
            spec = _cast_category_keys(self.type, _normalise_spec(self.type, spec))
            lo = _to_internal(self.type, c.get("minimum"))
            hi = _to_internal(self.type, c.get("maximum"))
            categories = c.get("enum")
            sampler = build_sampler(spec, lo=lo, hi=hi, categories=categories)
            if (lo is not None or hi is not None) and categories is None:
                # Whatever the source, never draw outside the field's declared range.
                self.sampler = lambda rng, s=sampler: _clip_value(s(rng), lo, hi)
            else:
                self.sampler = sampler

    def draw(self, rng: random.Random, row_number: int) -> Any:
        if self.missing and rng.random() < self.missing:
            return None
        if self.sampler is None:
            value = f"SYN-{self.name}-{row_number}"
        else:
            value = _cast(self.type, self.sampler(rng), self.field)
        if self.max_length and isinstance(value, str):
            value = value[: self.max_length]
        return value


# --- table generation -----------------------------------------------------

def _row_count(stats: dict, override: dict, defaults: dict) -> tuple[int, str]:
    if "rowCount" in override:
        return int(override["rowCount"]), "override"
    if "rowCount" in stats:
        return int(stats["rowCount"]), "statistics"
    return int(defaults["rowCount"]), "default"


def _children_sampler(stats: dict, override: dict, defaults: dict, n_parents: int) -> tuple[Sampler, str]:
    if "childrenPerParent" in override and (override.get("replaceStatistics") or "childrenPerParent" not in stats):
        spec, origin = override["childrenPerParent"], "override"
    elif "childrenPerParent" in stats:
        spec, origin = stats["childrenPerParent"], "statistics"
    elif "rowCount" in override or "rowCount" in stats:
        total = override.get("rowCount", stats.get("rowCount"))
        spec, origin = {"distribution": "poisson", "mean": total / max(n_parents, 1)}, "derived-from-rowCount"
    else:
        spec, origin = defaults["childrenPerParent"], "default"
    spec = _cast_category_keys("integer", spec)
    sampler = build_sampler(spec, lo=0)
    return (lambda rng: max(0, int(round(float(sampler(rng)))))), _label(spec, origin)


class RulePlan:
    """A cross-field rule: ``notBefore`` or ``offset``.

    ``reference`` names a field on the same row, or ``parent.<field>`` for the
    parent row the child was generated under. An ``offset`` rule sets the field
    to the reference plus a value drawn from ``offset`` (days for dates, seconds
    for datetimes, units otherwise). ``when`` limits a rule to rows whose fields
    equal the given values.
    """

    def __init__(self, rule: dict, types: dict):
        kind = rule.get("type")
        if kind not in ("notBefore", "offset"):
            raise ValueError(f"unknown rule type {kind!r}; expected notBefore or offset")
        self.kind = kind
        self.field = rule["field"]
        self.reference = rule["reference"]
        self.when = {k: str(v) for k, v in rule.get("when", {}).items()}
        self.type = types[self.field]
        self.offset = None
        if kind == "offset":
            spec = rule["offset"]
            scale = 86400.0 if self.type == "datetime" and rule.get("unit", "days") == "days" else 1.0
            sampler = build_sampler(spec, lo=spec.get("min"), hi=spec.get("max"))
            self.offset = lambda rng: float(sampler(rng)) * scale
        self.source = "rule:" + kind

    def apply(self, row: dict, parent_row: dict | None, rng: random.Random) -> None:
        if any(str(row.get(k)) != v for k, v in self.when.items()):
            return
        if self.reference.startswith("parent."):
            if parent_row is None:
                return
            ref_value = parent_row.get(self.reference.split(".", 1)[1])
        else:
            ref_value = row.get(self.reference)
        if ref_value is None:
            return
        ref_num = _to_internal(self.type, ref_value)
        if self.kind == "offset":
            row[self.field] = _cast(self.type, ref_num + self.offset(rng), {})
            return
        value = row.get(self.field)
        if value is not None and _to_internal(self.type, value) < ref_num:
            row[self.field] = ref_value


def generate(profile: dict, overrides: dict | None = None, seed: int = 0) -> tuple[dict[str, list[dict]], dict]:
    """Generate every table in the profile.

    Returns ``(tables, report)`` where ``tables`` maps resource name to a list of
    row dicts and ``report`` is the fidelity report.
    """
    validate_profile(profile)
    overrides = overrides or {}
    defaults = {**DEFAULTS, **overrides.get("defaults", {})}
    res_overrides = overrides.get("resources", {})
    rng = random.Random(seed)

    tables: dict[str, list[dict]] = {}
    report_resources: dict[str, dict] = {}

    for res in resource_order(profile):
        name = res["name"]
        schema = res["schema"]
        stats = res.get("statistics", {})
        override = res_overrides.get(name, {})
        fields = schema["fields"]
        types = {f["name"]: f.get("type", "string") for f in fields}
        pk = schema.get("primaryKey")
        pk = pk[0] if isinstance(pk, list) and len(pk) == 1 else pk if isinstance(pk, str) else None
        fks = [fk for fk in foreign_keys(res) if fk["resource"] != name]
        fk_fields = {fk["field"] for fk in fks}

        res_report: dict[str, Any] = {"fields": {}}

        # Decide how many rows, and which parent row each one belongs to.
        parent_rows: list[dict | None]
        if fks:
            driver = fks[0]
            parents = tables[driver["resource"]]
            per_parent, source = _children_sampler(stats, override, defaults, len(parents))
            parent_rows = []
            for prow in parents:
                parent_rows.extend([prow] * per_parent(rng))
            res_report["childrenPerParent"] = {"parent": driver["resource"], "source": source}
            res_report["rowCountSource"] = "sum-of-children"
        else:
            n, source = _row_count(stats, override, defaults)
            parent_rows = [None] * n
            res_report["rowCountSource"] = source

        plans = {}
        for f in fields:
            fname = f["name"]
            if fname == pk or fname in fk_fields:
                continue
            unique = f.get("constraints", {}).get("unique")
            if unique:
                continue
            plans[fname] = FieldPlan(f, stats.get("fields", {}).get(fname),
                                     override.get("fields", {}).get(fname), defaults)

        rules = [RulePlan(r, types) for r in res.get("rules", []) + override.get("rules", [])]

        rows = []
        for i, prow in enumerate(parent_rows, start=1):
            row: dict[str, Any] = {}
            for f in fields:
                fname, ftype = f["name"], types[f["name"]]
                if fname == pk or f.get("constraints", {}).get("unique"):
                    row[fname] = i if ftype in ("integer", "number") else f"SYN-{name}-{i}"
                elif fname in fk_fields:
                    fk = next(k for k in fks if k["field"] == fname)
                    if fk is fks[0] and prow is not None:
                        row[fname] = prow[fk["target"]]
                    elif prow is not None and fname in prow:
                        # Same key on the parent row (e.g. person_id on a visit): copy it so links agree.
                        row[fname] = prow[fname]
                    else:
                        others = tables[fk["resource"]]
                        row[fname] = rng.choice(others)[fk["target"]] if others else None
                else:
                    row[fname] = plans[fname].draw(rng, i)
            for rule in rules:
                rule.apply(row, prow, rng)
            rows.append(row)
        tables[name] = rows

        for f in fields:
            fname = f["name"]
            if fname == pk or f.get("constraints", {}).get("unique"):
                entry = {"source": "generated:sequential-key"}
            elif fname in fk_fields:
                entry = {"source": "generated:foreign-key"}
            else:
                plan = plans[fname]
                entry = {"source": plan.source, "missing": plan.missing, "missingSource": plan.missing_source}
                released = stats.get("fields", {}).get(fname, {})
                if "frequencies" in released and plan.source.startswith("statistics"):
                    entry["maxProportionDifference"] = _max_proportion_diff(rows, fname, ftype=plan.type,
                                                                            released=released["frequencies"])
            ruled = [r for r in rules if r.field == fname]
            if ruled:
                entry["rules"] = [r.kind + (f" when {r.when}" if r.when else "") for r in ruled]
            res_report["fields"][fname] = entry
        res_report["rows"] = len(rows)
        report_resources[name] = res_report

    report = {
        "synthetic": True,
        "profile": profile.get("name"),
        "profileVersion": profile.get("version"),
        "profileFormat": PROFILE_VERSION,
        "declaredTier": profile.get("tier"),
        "seed": seed,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "preserved": [
            "Table structure, field types, primary and foreign keys",
            "Each field's distribution where its source is 'statistics' or 'override', independently of other fields",
            "Rows per parent where childrenPerParent comes from statistics or an override",
            "Ordering rules declared in the profile",
        ],
        "notPreserved": [
            "Any relationship between fields other than foreign keys and declared rules",
            "Any distribution whose source starts with 'default' (it is a guess from the field type)",
        ],
        "resources": report_resources,
    }
    return tables, report


def _max_proportion_diff(rows: list[dict], field: str, ftype: str, released: dict) -> float:
    """Largest gap between released and synthetic category shares (non-missing values only)."""
    total_released = float(sum(released.values()))
    values = [r[field] for r in rows if r[field] is not None]
    if not values or not total_released:
        return 0.0
    counts: dict[Any, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    keys = {_cast(ftype, k, {}) if ftype not in ("string", "date", "datetime") else k for k in released}
    worst = 0.0
    for k_raw, c in released.items():
        k = _cast(ftype, k_raw, {}) if ftype not in ("string", "date", "datetime") else k_raw
        worst = max(worst, abs(c / total_released - counts.get(k, 0) / len(values)))
    for k, c in counts.items():
        if k not in keys:
            worst = max(worst, c / len(values))
    return round(worst, 4)


# --- writing ----------------------------------------------------------------

def write_output(profile: dict, tables: dict[str, list[dict]], report: dict, out_dir: str | Path) -> None:
    """Write one CSV per table, a datapackage.json describing them, and the fidelity report."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    resources = []
    for res in profile["resources"]:
        name = res["name"]
        columns = [f["name"] for f in res["schema"]["fields"]]
        path = out / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for row in tables[name]:
                writer.writerow({k: ("" if v is None else v) for k, v in row.items()})
        resources.append({"name": name, "path": f"{name}.csv", "schema": res["schema"]})
    package = {
        "name": f"{profile.get('name', 'dataset')}-synthetic",
        "title": f"Synthetic data generated from {profile.get('title', profile.get('name', 'a profile'))}",
        "description": "Synthetic data. Contains no real records and must not be used to draw analytic conclusions.",
        "keywords": ["synthetic"],
        "resources": resources,
    }
    (out / "datapackage.json").write_text(json.dumps(package, indent=2), encoding="utf-8")
    (out / "fidelity-report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

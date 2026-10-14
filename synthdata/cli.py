"""Command-line entry point: ``synthdata generate`` and ``synthdata profile``."""

from __future__ import annotations

import argparse
import json
import sys

from .generate import generate, write_output
from .profile import load_json, load_profile
from .profiler import profile_package


def _parse_rows(items: list[str]) -> dict:
    out: dict = {}
    for item in items or []:
        name, _, n = item.partition("=")
        if not n:
            raise SystemExit(f"--rows expects TABLE=N, got {item!r}")
        out.setdefault(name, {})["rowCount"] = int(n)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="synthdata", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("generate", help="generate synthetic tables from a profile")
    g.add_argument("profile", help="profile JSON file")
    g.add_argument("--out", required=True, help="output directory for CSVs and reports")
    g.add_argument("--overrides", help="JSON file of user-chosen distributions and sizes")
    g.add_argument("--seed", type=int, default=0)
    g.add_argument("--rows", nargs="*", metavar="TABLE=N", help="row count for a top-level table")

    p = sub.add_parser("profile", help="profile a data package (run inside the TRE)")
    p.add_argument("datapackage", help="datapackage.json describing the real CSV files")
    p.add_argument("--out", required=True, help="profile JSON file to write")
    p.add_argument("--tier", type=int, choices=(0, 1), default=1)
    p.add_argument("--threshold", type=int, default=10, help="smallest count that may be released")
    p.add_argument("--rounding", type=int, default=5, help="base to round released counts to")
    p.add_argument("--max-categories", type=int, default=50)

    args = parser.parse_args(argv)

    if args.command == "generate":
        profile = load_profile(args.profile)
        overrides = load_json(args.overrides) if args.overrides else {}
        for name, extra in _parse_rows(args.rows).items():
            overrides.setdefault("resources", {}).setdefault(name, {}).update(extra)
        tables, report = generate(profile, overrides, seed=args.seed)
        write_output(profile, tables, report, args.out)
        for name, rows in tables.items():
            print(f"{name}: {len(rows)} rows", file=sys.stderr)
        print(f"wrote {args.out}", file=sys.stderr)
        return 0

    profile = profile_package(args.datapackage, tier=args.tier, threshold=args.threshold,
                              rounding=args.rounding, max_categories=args.max_categories)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2)
    print(f"wrote {args.out}", file=sys.stderr)
    return 0

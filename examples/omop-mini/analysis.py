"""A typical piece of analysis code, written against the data dictionary.

The point of the example: this script is developed against synthetic data and
must run unchanged on the real tables once access is granted.

Usage: python analysis.py DATA_DIR
"""

import csv
import sys
from collections import Counter
from pathlib import Path


def read(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def main(data_dir: str) -> None:
    d = Path(data_dir)
    people = {int(p["person_id"]): p for p in read(d / "person.csv")}
    visits = read(d / "visit_occurrence.csv")
    conditions = read(d / "condition_occurrence.csv")

    visits_per_person = Counter(int(v["person_id"]) for v in visits)
    bands = {}
    for pid, p in people.items():
        band = f"{(2020 - int(p['year_of_birth'])) // 20 * 20}+"
        bands.setdefault(band, []).append(visits_per_person.get(pid, 0))
    print("Mean visits per person by age band")
    for band in sorted(bands, key=lambda b: int(b[:-1])):
        vals = bands[band]
        print(f"  {band:>4}: {sum(vals) / len(vals):5.2f}  (n={len(vals)})")

    los = [
        (int(v["visit_end_date"][:4]) * 366 + int(v["visit_end_date"][5:7]) * 31 + int(v["visit_end_date"][8:10]))
        - (int(v["visit_start_date"][:4]) * 366 + int(v["visit_start_date"][5:7]) * 31 + int(v["visit_start_date"][8:10]))
        for v in visits if v["visit_concept_id"] == "9201" and v["visit_end_date"]
    ]
    if los:
        print(f"Inpatient stays: {len(los)}, mean length {sum(los) / len(los):.1f} days (approx.)")

    top = Counter(c["condition_concept_id"] for c in conditions).most_common(3)
    print("Top conditions:", ", ".join(f"{k} ({n})" for k, n in top))

    linked = sum(1 for c in conditions if int(c["person_id"]) in people)
    print(f"Conditions linked to a person: {linked}/{len(conditions)}")


if __name__ == "__main__":
    main(sys.argv[1])

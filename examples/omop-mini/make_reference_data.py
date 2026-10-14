"""Create a stand-in "real" dataset for the OMOP-mini example.

No patient data can be used here, so this script simulates one with deliberate
relationships between fields (older people have more visits and more chronic
conditions; inpatient stays last several days). Profiling it and generating
from the profile shows which of those relationships survive at each tier.

Usage: python make_reference_data.py OUT_DIR [--people 2000] [--seed 1]
"""

import argparse
import csv
import datetime as dt
import json
import random
from pathlib import Path

HERE = Path(__file__).parent
CHRONIC = [201826, 320128, 255573, 4329847]   # diabetes, hypertension, COPD, heart attack
ACUTE = [80180, 432867, 260139, 437663]        # osteoarthritis, hyperlipidaemia, bronchitis, fever


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--people", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    people, visits, conditions = [], [], []
    start, span = dt.date(2015, 1, 1), (dt.date(2023, 12, 31) - dt.date(2015, 1, 1)).days
    for pid in range(1, args.people + 1):
        yob = int(min(2020, max(1920, rng.gauss(1965, 20))))
        age = 2020 - yob
        people.append({"person_id": pid, "gender_concept_id": rng.choice([8507, 8532]), "year_of_birth": yob,
                       "ethnicity_source_value": rng.choices(["A", "B", "C", "D", "Z", ""], [60, 12, 8, 6, 4, 10])[0]})
        n_visits = int(rng.expovariate(1 / (1 + age / 15)))
        for _ in range(n_visits):
            vid = len(visits) + 1
            vtype = rng.choices([9201, 9202, 9203], [1 + age / 30, 7, 2])[0]
            vstart = start + dt.timedelta(days=rng.randrange(span))
            length = int(rng.expovariate(1 / 5)) + 1 if vtype == 9201 else 0
            visits.append({"visit_occurrence_id": vid, "person_id": pid, "visit_concept_id": vtype,
                           "visit_start_date": vstart.isoformat(),
                           "visit_end_date": (vstart + dt.timedelta(days=length)).isoformat()})
            for _ in range(rng.choice([0, 1, 1, 1, 2, 2, 3])):
                pool = CHRONIC if rng.random() < min(0.9, age / 90) else ACUTE
                conditions.append({"condition_occurrence_id": len(conditions) + 1, "visit_occurrence_id": vid,
                                   "person_id": pid, "condition_concept_id": rng.choice(pool),
                                   "condition_start_date": vstart.isoformat()})

    profile = json.loads((HERE / "profile-dictionary-only.json").read_text())
    tables = {"person": people, "visit_occurrence": visits, "condition_occurrence": conditions}
    resources = []
    for res in profile["resources"]:
        rows = tables[res["name"]]
        with open(out / f"{res['name']}.csv", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[fld["name"] for fld in res["schema"]["fields"]])
            w.writeheader()
            w.writerows(rows)
        entry = {"name": res["name"], "path": f"{res['name']}.csv", "schema": res["schema"]}
        if res.get("rules"):
            entry["rules"] = res["rules"]
        resources.append(entry)
    package = {"name": "omop-mini", "title": "OMOP-mini stand-in reference data", "version": "1.0.0",
               "resources": resources}
    (out / "datapackage.json").write_text(json.dumps(package, indent=2))
    print(f"wrote {len(people)} people, {len(visits)} visits, {len(conditions)} conditions to {out}")


if __name__ == "__main__":
    main()

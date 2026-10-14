import csv
import json
import subprocess
import sys

from synthdata import generate, profile_package
from conftest import EXAMPLE


def _write_package(tmp_path, rows, fields):
    with open(tmp_path / "t.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[x["name"] for x in fields])
        w.writeheader()
        w.writerows(rows)
    pkg = {"name": "t", "resources": [{"name": "t", "path": "t.csv",
                                       "schema": {"fields": fields, "primaryKey": ["id"]}}]}
    (tmp_path / "datapackage.json").write_text(json.dumps(pkg))
    return tmp_path / "datapackage.json"


def test_small_categories_are_withheld_and_counts_rounded(tmp_path):
    rows = [{"id": i, "colour": "red" if i < 63 else "blue" if i < 97 else "green", "age": i % 90}
            for i in range(100)]
    fields = [{"name": "id", "type": "integer"}, {"name": "colour", "type": "string"},
              {"name": "age", "type": "integer"}]
    prof = profile_package(_write_package(tmp_path, rows, fields), threshold=10, rounding=5)
    stats = prof["resources"][0]["statistics"]
    assert stats["rowCount"] == 100
    colour = stats["fields"]["colour"]
    assert colour["frequencies"] == {"red": 65, "blue": 35}  # 63 -> 65, 34 -> 35, green (3) withheld
    assert colour["suppressedCategories"] == 1
    hist = stats["fields"]["age"]["histogram"]
    assert all(c >= 10 and c % 5 == 0 for c in hist["counts"])
    assert "id" not in stats["fields"]


def test_tier_zero_releases_only_structure_and_row_count(tmp_path):
    rows = [{"id": i, "colour": "red"} for i in range(23)]
    fields = [{"name": "id", "type": "integer"}, {"name": "colour", "type": "string"}]
    prof = profile_package(_write_package(tmp_path, rows, fields), tier=0)
    assert prof["resources"][0]["statistics"] == {"rowCount": 25}


def test_round_trip_on_stand_in_data(tmp_path):
    real = tmp_path / "real"
    subprocess.run([sys.executable, str(EXAMPLE / "make_reference_data.py"), str(real), "--people", "1500"],
                   check=True, capture_output=True)
    prof = profile_package(real / "datapackage.json")

    visits = prof["resources"][1]["statistics"]
    per_parent = visits["childrenPerParent"]["frequencies"]
    assert all(v >= 10 for v in per_parent.values())
    keys = sorted(int(k) for k in per_parent)
    assert keys == list(range(keys[0], keys[-1] + 1))  # tail top-coded, no gaps
    yob = prof["resources"][0]["statistics"]["fields"]["year_of_birth"]["histogram"]
    assert yob["edges"][-1] <= 2021

    tables, report = generate(prof, seed=5)
    assert len(tables["person"]) == prof["resources"][0]["statistics"]["rowCount"]
    concept = report["resources"]["condition_occurrence"]["fields"]["condition_concept_id"]
    assert concept["source"] == "statistics:frequencies"
    assert concept["maxProportionDifference"] < 0.03

    out = tmp_path / "syn"
    from synthdata import write_output
    write_output(prof, tables, report, out)
    result = subprocess.run([sys.executable, str(EXAMPLE / "analysis.py"), str(out)],
                            check=True, capture_output=True, text=True)
    assert "Conditions linked to a person" in result.stdout
    assert json.loads((out / "fidelity-report.json").read_text())["synthetic"] is True

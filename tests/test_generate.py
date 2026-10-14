import copy
import datetime as dt

import pytest

from synthdata import generate
from synthdata.profile import ProfileError


def _fields(profile, resource):
    res = next(r for r in profile["resources"] if r["name"] == resource)
    return {f["name"]: f for f in res["schema"]["fields"]}


def test_dictionary_only_profile_generates_valid_linked_tables(dictionary_profile):
    tables, report = generate(dictionary_profile, seed=1)

    people = {p["person_id"] for p in tables["person"]}
    visits = {v["visit_occurrence_id"]: v for v in tables["visit_occurrence"]}
    assert len(people) == 100  # default row count
    assert all(v["person_id"] in people for v in visits.values())
    for c in tables["condition_occurrence"]:
        assert c["visit_occurrence_id"] in visits
        # person_id is copied from the parent visit, so the two links agree
        assert c["person_id"] == visits[c["visit_occurrence_id"]]["person_id"]

    for p in tables["person"]:
        assert p["gender_concept_id"] in (8507, 8532)
        assert 1920 <= p["year_of_birth"] <= 2020
    for v in visits.values():
        if v["visit_end_date"] is not None:
            assert v["visit_end_date"] >= v["visit_start_date"]
    assert report["resources"]["person"]["fields"]["year_of_birth"]["source"] == "default:uniform"
    assert report["resources"]["visit_occurrence"]["childrenPerParent"]["source"] == "default:poisson"


def test_required_fields_are_never_missing(dictionary_profile):
    tables, _ = generate(dictionary_profile, {"defaults": {"missing": 0.5}}, seed=3)
    assert all(p["year_of_birth"] is not None for p in tables["person"])
    assert any(p["ethnicity_source_value"] is None for p in tables["person"])


def test_same_seed_gives_same_data(dictionary_profile):
    a, _ = generate(dictionary_profile, seed=7)
    b, _ = generate(dictionary_profile, seed=7)
    c, _ = generate(dictionary_profile, seed=8)
    assert a == b
    assert a != c


def test_overrides_fill_gaps_when_no_statistics(dictionary_profile, example_overrides):
    tables, report = generate(dictionary_profile, example_overrides, seed=1)
    assert len(tables["person"]) == 500
    years = [p["year_of_birth"] for p in tables["person"]]
    assert 1950 < sum(years) / len(years) < 1975
    person_fields = report["resources"]["person"]["fields"]
    assert person_fields["year_of_birth"]["source"] == "override:normal"
    assert person_fields["gender_concept_id"]["source"] == "default:equal-weights"
    # Inpatient stays get a positive length; outpatient visits end the day they start.
    for v in tables["visit_occurrence"]:
        start = dt.date.fromisoformat(v["visit_start_date"])
        end = dt.date.fromisoformat(v["visit_end_date"])
        if v["visit_concept_id"] == 9201:
            assert 1 <= (end - start).days <= 60
        elif v["visit_concept_id"] == 9202:
            assert end == start


def test_statistics_win_over_overrides_unless_replaced(dictionary_profile):
    profile = copy.deepcopy(dictionary_profile)
    person = profile["resources"][0]
    person["statistics"] = {"rowCount": 400, "fields": {"gender_concept_id": {"frequencies": {"8507": 300, "8532": 100}}}}
    override = {"resources": {"person": {"fields": {"gender_concept_id": {"distribution": "weights",
                                                                            "weights": {"8507": 1, "8532": 9}}}}}}

    tables, report = generate(profile, override, seed=1)
    share = sum(p["gender_concept_id"] == 8507 for p in tables["person"]) / len(tables["person"])
    assert len(tables["person"]) == 400
    assert 0.65 < share < 0.85
    entry = report["resources"]["person"]["fields"]["gender_concept_id"]
    assert entry["source"] == "statistics:frequencies"
    assert entry["maxProportionDifference"] < 0.1

    override["resources"]["person"]["fields"]["gender_concept_id"]["replaceStatistics"] = True
    tables, report = generate(profile, override, seed=1)
    share = sum(p["gender_concept_id"] == 8507 for p in tables["person"]) / len(tables["person"])
    assert share < 0.2
    assert report["resources"]["person"]["fields"]["gender_concept_id"]["source"] == "override:weights"


def test_histogram_and_children_statistics(dictionary_profile):
    profile = copy.deepcopy(dictionary_profile)
    profile["resources"][0]["statistics"] = {"rowCount": 300, "fields": {
        "year_of_birth": {"histogram": {"edges": [1940, 1960, 1980], "counts": [10, 90]}}}}
    profile["resources"][1]["statistics"] = {"childrenPerParent": {"frequencies": {"3": 1}}}
    tables, _ = generate(profile, seed=2)
    years = [p["year_of_birth"] for p in tables["person"]]
    assert all(1940 <= y <= 1980 for y in years)
    assert sum(y >= 1960 for y in years) / len(years) > 0.8
    assert len(tables["visit_occurrence"]) == 900


def test_samples_are_clipped_to_declared_range(dictionary_profile):
    override = {"resources": {"person": {"fields": {"year_of_birth": {"distribution": "normal",
                                                                        "mean": 2015, "sd": 30}}}}}
    tables, _ = generate(dictionary_profile, override, seed=4)
    assert all(1920 <= p["year_of_birth"] <= 2020 for p in tables["person"])


def test_bad_profiles_are_rejected(dictionary_profile):
    bad = copy.deepcopy(dictionary_profile)
    bad["profile"] = "something-else"
    with pytest.raises(ProfileError):
        generate(bad)
    bad = copy.deepcopy(dictionary_profile)
    bad["resources"][1]["schema"]["foreignKeys"][0]["reference"]["resource"] = "nope"
    with pytest.raises(ProfileError):
        generate(bad)

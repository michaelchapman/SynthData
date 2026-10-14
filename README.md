# SynthData

Generate synthetic versions of health datasets from their metadata and
disclosure-controlled summary statistics. The aim is to let people planning to
use a dataset judge its suitability and write their analysis code while they
wait for access.

The approach has three parts:

- **A profile format** ([docs/profile-spec.md](docs/profile-spec.md)): a
  Frictionless Data Package describing tables, fields, keys and code lists, plus
  optional statistics at increasing levels of detail and the disclosure rules
  applied.
- **A profiler** that a data custodian runs inside their trusted research
  environment to produce a profile from real data. It withholds small counts,
  rounds what it releases, merges sparse histogram bins and top-codes long tails.
- **A generator** that turns any profile into linked synthetic tables. It never
  sees real data. Where statistics are missing it uses distributions the user
  chooses, then defaults from each field's type, so a bare data dictionary is
  enough to start.

This is a proof of concept (version 0.1). It is not meant to produce data good
enough for analytic conclusions; it is meant to produce data your code will run
against.

## Quick start

Requires Python 3.9 or later. There are no runtime dependencies.

```sh
pip install -e .            # or run with: python -m synthdata ...

# 1. Structure only: generate from a data dictionary
synthdata generate examples/omop-mini/profile-dictionary-only.json --out out/dictionary

# 2. Add your own choices where the profile is silent
synthdata generate examples/omop-mini/profile-dictionary-only.json \
    --overrides examples/omop-mini/overrides.json --rows person=1000 --seed 42 --out out/with-choices

# 3. Play the custodian: make stand-in "real" data, profile it, generate from the profile
python examples/omop-mini/make_reference_data.py out/real
synthdata profile out/real/datapackage.json --out out/profile.json --threshold 10 --rounding 5
synthdata generate out/profile.json --out out/from-profile

# The same analysis script runs on all of them
python examples/omop-mini/analysis.py out/from-profile
```

Each output folder holds one CSV per table, a `datapackage.json`, and a
`fidelity-report.json` saying where every field's values came from.

## What to expect at each level

Running `analysis.py` on the stand-in data and on each synthetic version shows
the trade-off (figures vary a little with the seed). The stand-in data is built so older people have more visits and
inpatient stays last about five days.

| Source | Mean visits, age 20–39 | Mean visits, age 60–79 | Mean inpatient stay |
| --- | --- | --- | --- |
| Stand-in "real" data | 2.4 | 5.5 | 5.5 days |
| Data dictionary only | about 2 | about 2 | hundreds of days |
| Profiled statistics (tier 1) | about 3.5 to 4 | about 3.5 to 4 | hundreds of days |
| Dictionary plus user choices and stay-length rules | about 4 | about 4 | about 5 days |

Each column's own distribution is preserved at tier 1, but relationships
between columns are not. That is by design: those need the tier 2 joint
statistics described in the spec.

## Tests

```sh
pip install pytest
python -m pytest
```

## Status and next steps

- Joint tables and intervals released as statistics (tier 2), fitted with
  iterative proportional fitting or a graphical model.
- Output in OMOP CDM and FHIR form.
- Importing OHDSI Achilles results as a tier 1 profile.
- A JSON Schema for the profile, and links from HDR UK Gateway and
  HealthDCAT-AP records.

Background research and the design rationale are in the project's findings
document.

# Synthetic data profile format, version 0.1 (draft)

A **profile** describes a dataset well enough to generate synthetic data from it,
without containing any patient records. It is a
[Frictionless Data Package](https://specs.frictionlessdata.io/data-package/)
with two additions on each resource: optional `statistics`, and optional
`rules`. A profile with no statistics at all, just a data dictionary, is valid.

The profile is the only thing that leaves a secure environment. The generator
reads it and nothing else, so synthetic output carries no more disclosure risk
than the profile itself.

## Top level

| Key | Required | Meaning |
| --- | --- | --- |
| `profile` | yes | Always `"synthdata-profile/0.1"` for this version |
| `name` | yes | Short identifier for the dataset, lower case with hyphens |
| `title`, `description`, `version` | no | As in Data Package. `version` should track the dataset release the profile describes |
| `tier` | no | The highest statistical tier released (see below). Informational |
| `disclosure` | no | The disclosure rules applied: `threshold`, `rounding`, and a list of `methods` in plain words |
| `resources` | yes | One entry per table |

Catalogue records (DCAT, HealthDCAT-AP, the HDR UK Gateway schema) should link
to the profile as a distribution with `dct:conformsTo` pointing at this
specification, rather than copying it.

## Resources

Each resource follows Data Package and
[Table Schema](https://specs.frictionlessdata.io/table-schema/):

```json
{
  "name": "visit_occurrence",
  "schema": {
    "fields": [
      {"name": "visit_occurrence_id", "type": "integer", "constraints": {"required": true}},
      {"name": "person_id", "type": "integer", "constraints": {"required": true}},
      {"name": "visit_concept_id", "type": "integer", "constraints": {"enum": [9201, 9202, 9203]}},
      {"name": "visit_start_date", "type": "date", "constraints": {"minimum": "2015-01-01"}}
    ],
    "primaryKey": ["visit_occurrence_id"],
    "foreignKeys": [{"fields": ["person_id"], "reference": {"resource": "person", "fields": ["person_id"]}}]
  },
  "rules": [],
  "statistics": {}
}
```

Supported field types are `string`, `integer`, `number`, `boolean`, `year`,
`date` and `datetime`. The generator uses these Table Schema constraints:
`required`, `unique`, `enum`, `minimum`, `maximum` and `maxLength`. Version 0.1
supports single-field primary and foreign keys only.

When a table has more than one foreign key, the **first** one decides the
table's size (rows are generated per parent row). A later foreign key whose
field also exists on that parent row is copied from it, so that, for example, a
condition's `person_id` always matches its visit's `person_id`.

## Statistics

`statistics` holds what the custodian has cleared for release. Every key is
optional.

```json
"statistics": {
  "rowCount": 8470,
  "childrenPerParent": {"frequencies": {"0": 425, "1": 305, "2": 245, "16": 70}},
  "fields": {
    "visit_concept_id": {"missing": 0.0, "frequencies": {"9202": 4905, "9201": 2105, "9203": 1460}},
    "visit_start_date": {"histogram": {"edges": ["2015-01-01", "2016-01-01", "2017-01-01"], "counts": [940, 935]}}
  }
}
```

| Key | Meaning |
| --- | --- |
| `rowCount` | Number of rows, after rounding. Used for tables with no foreign key |
| `childrenPerParent` | How many rows each parent row has, through the first foreign key. Any distribution spec (below) |
| `fields.<name>` | A distribution spec for one field, plus optional `missing` (share of empty values, 0 to 1) and `suppressedCategories` (how many categories were withheld) |

### Distribution specs

The same vocabulary is used in released statistics, in user overrides, and in
`offset` rules. A spec is one of:

| Spec | Form | Notes |
| --- | --- | --- |
| Frequencies | `{"frequencies": {"value": count, ...}}` | Keys are strings in JSON and are cast to the field type |
| Histogram | `{"histogram": {"edges": [...], "counts": [...]}}` | One more edge than counts. Values are uniform within a bin. Dates as ISO strings |
| Quantiles | `{"quantiles": {"0": 18, "0.5": 52, "1": 99}}` | Must include 0 and 1. Linear between points |
| Uniform | `{"distribution": "uniform", "min": a, "max": b}` | Defaults to the field's constraints |
| Normal | `{"distribution": "normal", "mean": m, "sd": s}` | Clipped to the field's range |
| Log-normal | `{"distribution": "lognormal", "median": m, "gsd": g}` | `gsd` is the geometric standard deviation |
| Exponential | `{"distribution": "exponential", "mean": m}` | Good for intervals and lengths of stay |
| Poisson | `{"distribution": "poisson", "mean": m}` | Good for counts, such as children per parent |
| Zipf | `{"distribution": "zipf", "s": 1.1}` | Skewed weights over the field's `enum`, in listed order |
| Weights | `{"distribution": "weights", "weights": {"a": 3, "b": 1}}` | Or a list of weights matching `values` or the `enum` |
| Constant | `{"distribution": "constant", "value": v}` | |

Any spec may add `min` and `max`. Draws never fall outside a field's declared
`minimum` and `maximum`.

### Tiers

| Tier | Released | Status in 0.1 |
| --- | --- | --- |
| 0 | Structure and rounded row counts | Supported by profiler and generator |
| 1 | One-column statistics and children per parent | Supported by profiler and generator |
| 2 | Chosen joint tables, intervals between events | `offset` rules supported; joint tables planned |
| 3 | A model-based synthetic dataset made inside the TRE | Out of scope; a profile may link to one |

## Rules

Rules describe relationships between fields that hold on every row.

```json
"rules": [
  {"type": "notBefore", "field": "visit_end_date", "reference": "visit_start_date"},
  {"type": "offset", "field": "visit_end_date", "reference": "visit_start_date",
   "when": {"visit_concept_id": 9201},
   "offset": {"distribution": "exponential", "mean": 5, "min": 1, "max": 60}}
]
```

| Type | Effect |
| --- | --- |
| `notBefore` | If the field is earlier than the reference, it is set to the reference |
| `offset` | The field is set to the reference plus a draw from `offset`, in days for dates and datetimes |

`reference` is a field on the same row, or `parent.<field>` for a field on the
parent row (through the first foreign key). `when` limits a rule to rows whose
fields equal the given values. Rules run in order after all fields are drawn.

## Overrides (user choices)

A separate overrides file lets the person generating data fill gaps or choose
sizes. It is not part of the profile and is never released by a custodian.

```json
{
  "defaults": {"missing": 0.01, "rowCount": 100, "childrenPerParent": {"distribution": "poisson", "mean": 2}},
  "resources": {
    "person": {"rowCount": 500, "fields": {"year_of_birth": {"distribution": "normal", "mean": 1962, "sd": 20}}},
    "visit_occurrence": {"childrenPerParent": {"distribution": "poisson", "mean": 4}, "rules": []}
  }
}
```

For each field the generator uses, in order: released statistics; the user's
override; a default from the field's type (an `enum` gets equal weights, a
numeric or date field is uniform over its range, other strings get labelled
placeholder values). An override with `"replaceStatistics": true` wins over
released statistics. A `rowCount` override always sets the size of a top-level
table. Rules in overrides are added after the profile's own rules.

## Output

The generator writes one CSV per resource, a `datapackage.json` describing them,
and a `fidelity-report.json`. The report records, per field, which source was
used (`statistics:…`, `override:…`, `default:…`, `generated:…`), the missing
rate and its source, any rules, and for released frequencies the largest gap
between released and synthetic shares. Every relationship not listed as
preserved is random by design.

## Mapping to other standards (planned)

| This format | Maps to |
| --- | --- |
| Profile | DCAT distribution of the dataset record; HDR UK Gateway structural metadata |
| Field statistics | DDI Codebook `sumStat` and `catStat`; schema.org `Observation` |
| Joint tables (tier 2) | SDMX / W3C RDF Data Cube (dimensions, measures, suppression attributes) |
| OMOP datasets | OHDSI Achilles results tables can be converted into tier 1 statistics |
| Synthetic output | PROV-O provenance from profile to output |

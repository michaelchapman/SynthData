"""Loading profiles and overrides, and working out table order."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILE_VERSION = "synthdata-profile/0.1"


class ProfileError(ValueError):
    """Raised when a profile is malformed."""


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_profile(path: str | Path) -> dict:
    profile = load_json(path)
    validate_profile(profile)
    return profile


def validate_profile(profile: dict) -> None:
    """Check the parts of a profile the generator relies on.

    This is deliberately light: anything not listed here is optional, so a bare
    data dictionary is a valid profile.
    """
    if profile.get("profile") != PROFILE_VERSION:
        raise ProfileError(f"expected \"profile\": \"{PROFILE_VERSION}\"")
    resources = profile.get("resources")
    if not isinstance(resources, list) or not resources:
        raise ProfileError("profile needs a non-empty \"resources\" list")
    all_names = {r.get("name") for r in resources}
    names = set()
    for res in resources:
        name = res.get("name")
        if not name:
            raise ProfileError("every resource needs a name")
        if name in names:
            raise ProfileError(f"duplicate resource name {name!r}")
        names.add(name)
        fields = res.get("schema", {}).get("fields")
        if not fields:
            raise ProfileError(f"resource {name!r} has no schema.fields")
        field_names = {f["name"] for f in fields}
        for fk in res["schema"].get("foreignKeys", []):
            target = fk["reference"].get("resource")
            if target not in all_names:
                raise ProfileError(f"{name!r} references unknown resource {target!r}")
            for fld in _as_list(fk["fields"]):
                if fld not in field_names:
                    raise ProfileError(f"{name!r} foreign key uses unknown field {fld!r}")
        stats = res.get("statistics", {}).get("fields", {})
        for fld in stats:
            if fld not in field_names:
                raise ProfileError(f"{name!r} has statistics for unknown field {fld!r}")


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else [value]


def foreign_keys(resource: dict) -> list[dict]:
    """Foreign keys normalised to single fields: [{field, resource, target}]."""
    out = []
    for fk in resource["schema"].get("foreignKeys", []):
        fields = _as_list(fk["fields"])
        targets = _as_list(fk["reference"]["fields"])
        if len(fields) != 1 or len(targets) != 1:
            raise ProfileError(f"{resource['name']!r}: only single-field foreign keys are supported in v0.1")
        out.append({"field": fields[0], "resource": fk["reference"]["resource"], "target": targets[0]})
    return out


def resource_order(profile: dict) -> list[dict]:
    """Resources sorted so every parent table comes before its children."""
    by_name = {r["name"]: r for r in profile["resources"]}
    ordered: list[dict] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name) == "done":
            return
        if state.get(name) == "visiting":
            raise ProfileError(f"foreign keys form a cycle through {name!r}")
        state[name] = "visiting"
        for fk in foreign_keys(by_name[name]):
            if fk["resource"] != name:
                visit(fk["resource"])
        state[name] = "done"
        ordered.append(by_name[name])

    for res in profile["resources"]:
        visit(res["name"])
    return ordered

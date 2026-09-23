"""Schema loading and validation against the committed JSON Schemas.

The canonical contracts live in `schemas/*.schema.json` and are the source of
truth; this module only loads and validates against them.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"

_SCHEMA_CACHE: dict[str, dict] = {}


def schema_path(name: str) -> Path:
    return SCHEMAS_DIR / f"{name}.schema.json"


def list_schemas() -> list[str]:
    return sorted(p.name[: -len(".schema.json")] for p in SCHEMAS_DIR.glob("*.schema.json"))


def load_schema(name: str) -> dict:
    if name not in _SCHEMA_CACHE:
        with open(schema_path(name), "r", encoding="utf-8") as fh:
            _SCHEMA_CACHE[name] = json.load(fh)
    return _SCHEMA_CACHE[name]


def validate_schema(data, schema_name: str) -> list[str]:
    """Validate `data` against the named schema; return list of error strings."""
    schema = load_schema(schema_name)
    errors: list[str] = []
    validator = jsonschema.Draft7Validator(schema)
    for err in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"{'.'.join(str(p) for p in err.path) or '<root>'}: {err.message}")
    return errors


def validate_all_schemas() -> dict[str, list[str]]:
    """Every committed schema must itself be valid JSON Schema draft-07."""
    import jsonschema.validators

    problems: dict[str, list[str]] = {}
    for name in list_schemas():
        schema = load_schema(name)
        try:
            jsonschema.validators.validator_for(schema).check_schema(schema)
        except jsonschema.SchemaError as exc:
            problems[name] = [str(exc)]
    return problems
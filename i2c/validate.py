"""JSON Schema validation for i2c state files.

Ported from toolkit's structured_llm/core.py (validate_json_schema, load_schema,
_format_validation_error) with i2c-specific additions: a filename→schema registry
and per-file convenience wrappers. Keeps state.py and assemble_context.py from
having to know schema paths.

Only external dependency: jsonschema.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


# Map of state filename → schema filename. The filename in .state/ determines
# which schema to apply; callers don't need to know the mapping.
SCHEMA_BY_FILENAME: dict[str, str] = {
    "project.json": "project.schema.json",
    "phases.json": "phases.schema.json",
    "steps.json": "steps.schema.json",
    "decisions.json": "decisions.schema.json",
    "followups.json": "followups.schema.json",
    # devlog.jsonl is per-line; use DEVLOG_ENTRY_SCHEMA for each record.
}

DEVLOG_ENTRY_SCHEMA = "devlog_entry.schema.json"
TELEMETRY_ENTRY_SCHEMA = "telemetry_entry.schema.json"
EXIT_SIGNAL_SCHEMA = "exit_signal.schema.json"

# Map of JSONL state filename → per-line schema filename. Each line of the
# file is one JSON object validated against this schema. Distinct from
# SCHEMA_BY_FILENAME (whole-file JSON object/array schemas).
JSONL_SCHEMA_BY_FILENAME: dict[str, str] = {
    "devlog.jsonl": DEVLOG_ENTRY_SCHEMA,
    "telemetry.jsonl": TELEMETRY_ENTRY_SCHEMA,
}


def schemas_dir() -> Path:
    """Packaged schemas directory: ``i2c/data/schemas`` resolved as package data.

    Schemas ship inside the installed package (DESIGN_packaging_v1.md §5.2),
    so they are located relative to the package, not the consumer's project.
    ``importlib.resources.files`` works for both editable and wheel installs.
    """
    return Path(resources.files("i2c") / "data" / "schemas")


def load_schema(name: str, *, schemas_root: Path | None = None) -> dict[str, Any]:
    """Load a schema by filename from the schemas directory."""
    root = schemas_root or schemas_dir()
    path = root / name
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Schema not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Schema file {path} is not valid JSON: {error}") from error

    if not isinstance(data, dict):
        raise ValueError(f"Schema {path} must be a JSON object")
    return data


def validate_json_schema(
    data: Any,
    schema: dict[str, Any],
    label: str = "",
) -> None:
    """Validate any JSON-deserialized value against a schema. Raises ValueError on failure."""
    try:
        Draft202012Validator(schema).validate(data)
    except ValidationError as error:
        message = _format_validation_error(error)
        if label:
            message = f"{label}: {message}"
        raise ValueError(message) from error


def validate_state_file(
    path: str | Path,
    *,
    schemas_root: Path | None = None,
) -> Any:
    """Read a .state/ JSON file and validate it against the registered schema.

    Returns the parsed data on success. Raises ValueError on schema lookup
    failure, JSON parse failure, or schema validation failure.
    """
    p = Path(path)
    schema_name = SCHEMA_BY_FILENAME.get(p.name)
    if schema_name is None:
        raise ValueError(
            f"No schema registered for {p.name}. "
            f"Known: {sorted(SCHEMA_BY_FILENAME)}"
        )

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"State file not found: {p}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"State file {p} is not valid JSON: {error}") from error

    schema = load_schema(schema_name, schemas_root=schemas_root)
    validate_json_schema(data, schema, label=str(p))
    return data


def validate_jsonl(
    path: str | Path,
    schema_name: str,
    *,
    schemas_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Validate every line of a .jsonl file against ``schema_name``.

    Blank lines are skipped. Returns the parsed entries. Raises ValueError on
    a missing file, a malformed line, or a schema violation.
    """
    p = Path(path)
    schema = load_schema(schema_name, schemas_root=schemas_root)

    entries: list[dict[str, Any]] = []
    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ValueError(f"JSONL file not found: {p}") from error

    for lineno, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{p}:{lineno}: not valid JSON: {error}") from error
        validate_json_schema(entry, schema, label=f"{p}:{lineno}")
        entries.append(entry)
    return entries


def validate_devlog_jsonl(
    path: str | Path,
    *,
    schemas_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Validate every line of a devlog.jsonl file. Returns parsed entries."""
    return validate_jsonl(path, DEVLOG_ENTRY_SCHEMA, schemas_root=schemas_root)


def _format_validation_error(error: ValidationError) -> str:
    path = ".".join(str(part) for part in error.path)
    if path:
        return f"{path}: {error.message}"
    return error.message

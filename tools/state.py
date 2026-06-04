"""i2c state write CLI.

Atomic, schema-validated writes to .state/ files. The only way workers should
modify state. Replaces e2e's sed-on-frontmatter and DEVLOG markdown editing.

Subcommands:
    set FILE key=value [key=value ...]
        Set top-level keys on a JSON object file (e.g., project.json).
        Values are parsed as JSON literals; non-JSON falls back to string.

    complete FILE --phase N [--step M] [--commit HASH]
        Mark a record's status='complete'. Works on:
          - steps.json: requires both --phase and --step (matches one record)
          - phases.json: requires --phase only (matches id=N)
        --commit is optional and only written when provided.

    append FILE 'JSON_STRING'
        Append one record to a .jsonl file. Validates against the registered
        per-line schema (e.g., devlog_entry.schema.json for devlog.jsonl).

    append-gotcha FILE 'TEXT'
        Push a string onto project.json.gotchas.

All writes are atomic: temp file in the same directory, then os.replace().
Every write is validated against the registered schema before commit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import validate as v


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


def atomic_write_json(path: Path, data: Any) -> None:
    """Serialize data and write to path atomically (temp + os.replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort cleanup; ignore if temp already gone.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append a JSON record as one line to a .jsonl file.

    Not strictly atomic across processes (uses O_APPEND), but each write is a
    single syscall for a small line, which POSIX/NT guarantee won't interleave
    with other O_APPEND writes under typical sizes. Adequate for i2c's
    single-worker model.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------------------------------------------------------------------------
# Value parsing for `set key=value`
# ---------------------------------------------------------------------------


def parse_value(raw: str) -> Any:
    """Parse a CLI value as JSON literal; fall back to string on failure."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_kv_pairs(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected key=value, got: {pair!r}")
        key, _, raw = pair.partition("=")
        if not key:
            raise ValueError(f"Empty key in pair: {pair!r}")
        out[key] = parse_value(raw)
    return out


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_set(args: argparse.Namespace) -> int:
    """Update top-level keys on a JSON object file."""
    path = Path(args.file)
    updates = parse_kv_pairs(args.pairs)

    if not path.exists():
        sys.stderr.write(
            f"ERROR: {path} does not exist. Create it manually for first-time init.\n"
        )
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        sys.stderr.write(
            f"ERROR: `set` requires a JSON object file; {path} contains {type(data).__name__}.\n"
        )
        return 2

    data.update(updates)

    # Validate before write so a bad update doesn't corrupt the file.
    schema_name = v.SCHEMA_BY_FILENAME.get(path.name)
    if schema_name is None:
        sys.stderr.write(
            f"ERROR: no schema registered for {path.name}. Known: "
            f"{sorted(v.SCHEMA_BY_FILENAME)}\n"
        )
        return 2

    schema = v.load_schema(schema_name)
    try:
        v.validate_json_schema(data, schema, label=str(path))
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    atomic_write_json(path, data)
    print(f"OK: {path} updated ({', '.join(updates)})")
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """Mark a step or phase record as status='complete'."""
    path = Path(args.file)
    if not path.exists():
        sys.stderr.write(f"ERROR: {path} does not exist.\n")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.stderr.write(
            f"ERROR: `complete` requires a JSON array file; {path} contains "
            f"{type(data).__name__}.\n"
        )
        return 2

    matched_index = _find_record_index(data, path.name, args)
    if matched_index is None:
        sys.stderr.write(
            f"ERROR: no matching record in {path} for "
            f"phase={args.phase}, step={args.step}.\n"
        )
        return 1

    record = data[matched_index]
    record["status"] = "complete"
    if args.commit:
        record["commit"] = args.commit

    schema_name = v.SCHEMA_BY_FILENAME.get(path.name)
    if schema_name is None:
        sys.stderr.write(f"ERROR: no schema registered for {path.name}.\n")
        return 2

    schema = v.load_schema(schema_name)
    try:
        v.validate_json_schema(data, schema, label=str(path))
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    atomic_write_json(path, data)
    descriptor = f"phase {args.phase}"
    if args.step is not None:
        descriptor += f", step {args.step}"
    print(f"OK: {path} {descriptor} -> complete")
    return 0


def _find_record_index(
    records: list[dict[str, Any]],
    filename: str,
    args: argparse.Namespace,
) -> int | None:
    """Locate the record to mutate based on filename + args."""
    if filename == "steps.json":
        if args.step is None:
            raise ValueError("--step is required for complete on steps.json")
        for i, r in enumerate(records):
            if r.get("phase") == args.phase and r.get("step") == args.step:
                return i
        return None
    if filename == "phases.json":
        for i, r in enumerate(records):
            if r.get("id") == args.phase:
                return i
        return None
    raise ValueError(f"`complete` not supported for {filename}")


def cmd_append(args: argparse.Namespace) -> int:
    """Append a JSON record to a JSONL file (typically devlog.jsonl)."""
    path = Path(args.file)
    try:
        record = json.loads(args.record)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: record is not valid JSON: {e}\n")
        return 2

    if not isinstance(record, dict):
        sys.stderr.write("ERROR: record must be a JSON object.\n")
        return 2

    # Choose schema by filename. Currently only devlog.jsonl is registered.
    if path.name == "devlog.jsonl":
        schema = v.load_schema(v.DEVLOG_ENTRY_SCHEMA)
        try:
            v.validate_json_schema(record, schema, label=f"{path} (new entry)")
        except ValueError as e:
            sys.stderr.write(f"VALIDATION FAILED: {e}\n")
            return 1
    else:
        sys.stderr.write(
            f"ERROR: no per-line schema registered for {path.name}.\n"
        )
        return 2

    atomic_append_jsonl(path, record)
    print(f"OK: appended to {path}")
    return 0


def cmd_append_gotcha(args: argparse.Namespace) -> int:
    """Push a string onto project.json's gotchas array."""
    path = Path(args.file)
    if path.name != "project.json":
        sys.stderr.write(
            f"ERROR: append-gotcha only operates on project.json (got {path.name}).\n"
        )
        return 2
    if not path.exists():
        sys.stderr.write(f"ERROR: {path} does not exist.\n")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        sys.stderr.write(f"ERROR: {path} is not a JSON object.\n")
        return 2

    text = args.text.strip()
    if not text:
        sys.stderr.write("ERROR: gotcha text cannot be empty.\n")
        return 2

    gotchas = data.setdefault("gotchas", [])
    if not isinstance(gotchas, list):
        sys.stderr.write("ERROR: project.json.gotchas exists and is not an array.\n")
        return 2

    gotchas.append(text)

    schema = v.load_schema(v.SCHEMA_BY_FILENAME["project.json"])
    try:
        v.validate_json_schema(data, schema, label=str(path))
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    atomic_write_json(path, data)
    print(f"OK: gotcha appended to {path} (now {len(gotchas)} total)")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="state.py", description="Atomic state writes for i2c."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Set top-level keys on a JSON object file.")
    p_set.add_argument("file")
    p_set.add_argument("pairs", nargs="+", help="key=value pairs")
    p_set.set_defaults(func=cmd_set)

    p_complete = sub.add_parser(
        "complete", help="Mark a step (steps.json) or phase (phases.json) complete."
    )
    p_complete.add_argument("file")
    p_complete.add_argument("--phase", type=int, required=True)
    p_complete.add_argument("--step", type=int, default=None)
    p_complete.add_argument("--commit", default=None)
    p_complete.set_defaults(func=cmd_complete)

    p_append = sub.add_parser(
        "append", help="Append one record (JSON string) to a JSONL file."
    )
    p_append.add_argument("file")
    p_append.add_argument("record", help="JSON object as a string")
    p_append.set_defaults(func=cmd_append)

    p_gotcha = sub.add_parser(
        "append-gotcha", help="Append a string to project.json.gotchas."
    )
    p_gotcha.add_argument("file")
    p_gotcha.add_argument("text")
    p_gotcha.set_defaults(func=cmd_append_gotcha)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

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

    append-record FILE 'JSON_RECORD'
        Append one record to a JSON-array file (steps.json, phases.json,
        decisions.json). Validates the resulting array against the registered
        schema; atomic write.

    update-record FILE --match KEY=VALUE [field=value ...]
        Update fields on one record in a JSON-array file. Matches by a
        single KEY=VALUE pair; errors on no-match or multi-match. Validates
        the resulting array against the registered schema; atomic write.

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

from i2c import validate as v


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


# ---------------------------------------------------------------------------
# Path resolution (FU-19) + payload-from-file helper (FU-21)
# ---------------------------------------------------------------------------


def resolve_state_path(arg: str) -> Path:
    """Resolve a state-file argument, auto-prefixing bare schema filenames.

    Per FU-19: instruction examples write bare filenames like
    ``state.py append-record steps.json '...'`` but the workflow's CWD is
    the project root (which contains ``.state/`` as a sibling). To keep
    the examples honest, this helper auto-resolves a bare filename to
    ``.state/<name>`` when:
      - ``arg`` doesn't already exist as a file,
      - CWD contains a ``.state/`` directory,
      - ``arg`` is a bare filename (no directory components),
      - the basename is a registered state file (project/phases/steps/
        decisions JSON) or ``devlog.jsonl``,
      - the ``.state/<name>`` candidate exists.

    Outside a project root, or when ``arg`` already exists / contains
    directory components / isn't a known schema filename, this returns
    ``Path(arg)`` unchanged. Callers then surface the standard
    "does not exist" error.
    """
    p = Path(arg)
    if p.exists():
        return p
    # Bare filename = no directory components. ``Path("foo.json").parts == ("foo.json",)``;
    # ``Path(".state/foo.json").parts`` has two elements; anything path-like is skipped.
    if len(p.parts) != 1:
        return p
    basename = p.name
    known = basename in v.SCHEMA_BY_FILENAME or basename in v.JSONL_SCHEMA_BY_FILENAME
    if not known:
        return p
    state_dir = Path(".state")
    if not state_dir.is_dir():
        return p
    candidate = state_dir / basename
    if candidate.exists():
        return candidate
    return p


def read_payload_from_file(path_str: str) -> str:
    """Read a ``--from-file`` payload as UTF-8 text. Raises FileNotFoundError on miss.

    Used by the four payload-bearing subcommands to bypass shell-quoting
    pitfalls (PowerShell ``$``-interpolation, multi-line heredoc gotchas).
    The returned string substitutes 1:1 for the inline positional payload.
    """
    p = Path(path_str)
    if not p.is_file():
        raise FileNotFoundError(f"--from-file path does not exist: {p}")
    return p.read_text(encoding="utf-8")


def _resolve_payload(args: argparse.Namespace, positional_attr: str) -> str | None:
    """Return the payload text from either ``--from-file`` or the positional arg.

    Enforces manual mutex (per ``_add_payload_args``): both-supplied or
    neither-supplied is an error. On error, writes a structured message
    and returns ``None`` so the caller can ``return 2``.
    """
    from_file = getattr(args, "from_file", None)
    inline = getattr(args, positional_attr, None)
    if from_file and inline is not None:
        sys.stderr.write(
            f"ERROR: --from-file is mutually exclusive with the positional "
            f"{positional_attr!r} argument.\n"
        )
        return None
    if from_file:
        try:
            return read_payload_from_file(from_file)
        except FileNotFoundError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return None
    if inline is None:
        sys.stderr.write(
            f"ERROR: missing payload (provide positional {positional_attr!r} "
            f"or --from-file).\n"
        )
        return None
    return inline


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


def append_validated_jsonl(
    path: Path, record: dict[str, Any], *, schema_name: str
) -> None:
    """Validate ``record`` against ``schema_name`` then append it as one JSONL line.

    Shared by the ``append`` CLI subcommand and in-process writers (e.g. the
    runner's telemetry sidecar) so every .jsonl write goes through one
    schema-checked path. Raises ValueError on a schema violation (nothing is
    written in that case).
    """
    schema = v.load_schema(schema_name)
    v.validate_json_schema(record, schema, label=f"{path} (new entry)")
    atomic_append_jsonl(path, record)


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
    path = resolve_state_path(args.file)
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
    path = resolve_state_path(args.file)
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
    path = resolve_state_path(args.file)
    raw = _resolve_payload(args, "record")
    if raw is None:
        return 2
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: record is not valid JSON: {e}\n")
        return 2

    if not isinstance(record, dict):
        sys.stderr.write("ERROR: record must be a JSON object.\n")
        return 2

    # Choose schema by filename via the JSONL registry (devlog.jsonl,
    # telemetry.jsonl, ...).
    schema_name = v.JSONL_SCHEMA_BY_FILENAME.get(path.name)
    if schema_name is None:
        sys.stderr.write(
            f"ERROR: no per-line schema registered for {path.name}. Known: "
            f"{sorted(v.JSONL_SCHEMA_BY_FILENAME)}\n"
        )
        return 2
    try:
        append_validated_jsonl(path, record, schema_name=schema_name)
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    print(f"OK: appended to {path}")
    return 0


def cmd_append_record(args: argparse.Namespace) -> int:
    """Append a JSON record to a JSON-array file (steps, phases, decisions).

    Reads the existing array, appends the new record, validates the entire
    array against the registered schema, and atomically rewrites the file.
    """
    path = resolve_state_path(args.file)
    if not path.exists():
        sys.stderr.write(f"ERROR: {path} does not exist.\n")
        return 2

    raw = _resolve_payload(args, "record")
    if raw is None:
        return 2
    try:
        record = json.loads(raw)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"ERROR: record is not valid JSON: {e}\n")
        return 2

    if not isinstance(record, dict):
        sys.stderr.write("ERROR: record must be a JSON object.\n")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.stderr.write(
            f"ERROR: `append-record` requires a JSON array file; "
            f"{path} contains {type(data).__name__}.\n"
        )
        return 2

    schema_name = v.SCHEMA_BY_FILENAME.get(path.name)
    if schema_name is None:
        sys.stderr.write(
            f"ERROR: no schema registered for {path.name}. Known: "
            f"{sorted(v.SCHEMA_BY_FILENAME)}\n"
        )
        return 2

    data.append(record)

    schema = v.load_schema(schema_name)
    try:
        v.validate_json_schema(data, schema, label=str(path))
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    atomic_write_json(path, data)
    print(f"OK: appended record to {path} (now {len(data)} total)")
    return 0


def cmd_update_record(args: argparse.Namespace) -> int:
    """Update fields on one record in a JSON-array file.

    Match KEY=VALUE selects exactly one record by a top-level field. The
    field/value pairs in `updates` are applied to that record. The full
    array is validated against the registered schema before atomic write.

    When ``--from-file`` is used, the file's content must be a JSON
    object whose keys/values are the field updates. JSON values are used
    verbatim (no shell-escape pitfalls).
    """
    path = resolve_state_path(args.file)
    if not path.exists():
        sys.stderr.write(f"ERROR: {path} does not exist.\n")
        return 2

    if "=" not in args.match:
        sys.stderr.write(f"ERROR: --match expects KEY=VALUE, got: {args.match!r}\n")
        return 2
    match_key, _, match_raw = args.match.partition("=")
    if not match_key:
        sys.stderr.write("ERROR: --match key is empty.\n")
        return 2
    match_val = parse_value(match_raw)

    if getattr(args, "from_file", None) and args.updates:
        sys.stderr.write(
            "ERROR: --from-file is mutually exclusive with the positional "
            "updates list.\n"
        )
        return 2

    if getattr(args, "from_file", None):
        try:
            text = read_payload_from_file(args.from_file)
        except FileNotFoundError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 2
        try:
            file_updates = json.loads(text)
        except json.JSONDecodeError as e:
            sys.stderr.write(f"ERROR: --from-file content is not valid JSON: {e}\n")
            return 2
        if not isinstance(file_updates, dict):
            sys.stderr.write(
                "ERROR: --from-file must contain a JSON object of field updates.\n"
            )
            return 2
        updates = file_updates
    else:
        try:
            updates = parse_kv_pairs(args.updates or [])
        except ValueError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 2
    if not updates:
        sys.stderr.write("ERROR: at least one field=value update is required.\n")
        return 2

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        sys.stderr.write(
            f"ERROR: `update-record` requires a JSON array file; "
            f"{path} contains {type(data).__name__}.\n"
        )
        return 2

    schema_name = v.SCHEMA_BY_FILENAME.get(path.name)
    if schema_name is None:
        sys.stderr.write(
            f"ERROR: no schema registered for {path.name}. Known: "
            f"{sorted(v.SCHEMA_BY_FILENAME)}\n"
        )
        return 2

    matches = [i for i, r in enumerate(data) if r.get(match_key) == match_val]
    if not matches:
        sys.stderr.write(
            f"ERROR: no record in {path} matches {match_key}={match_val!r}.\n"
        )
        return 1
    if len(matches) > 1:
        sys.stderr.write(
            f"ERROR: {len(matches)} records in {path} match {match_key}={match_val!r}; "
            f"--match must be unique.\n"
        )
        return 1

    idx = matches[0]
    data[idx].update(updates)

    schema = v.load_schema(schema_name)
    try:
        v.validate_json_schema(data, schema, label=str(path))
    except ValueError as e:
        sys.stderr.write(f"VALIDATION FAILED: {e}\n")
        return 1

    atomic_write_json(path, data)
    print(
        f"OK: {path} {match_key}={match_val!r} updated "
        f"({', '.join(updates)})"
    )
    return 0


def cmd_append_gotcha(args: argparse.Namespace) -> int:
    """Push a string onto project.json's gotchas array."""
    path = resolve_state_path(args.file)
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

    raw = _resolve_payload(args, "text")
    if raw is None:
        return 2
    text = raw.strip()
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
    _add_payload_args(p_append, positional="record", help_text="JSON object as a string")
    p_append.set_defaults(func=cmd_append)

    p_append_record = sub.add_parser(
        "append-record",
        help="Append one record (JSON object) to a JSON-array file (steps/phases/decisions).",
    )
    p_append_record.add_argument("file")
    _add_payload_args(
        p_append_record, positional="record", help_text="JSON object as a string"
    )
    p_append_record.set_defaults(func=cmd_append_record)

    p_update_record = sub.add_parser(
        "update-record",
        help="Update fields on one record in a JSON-array file (matched by KEY=VALUE).",
    )
    p_update_record.add_argument("file")
    p_update_record.add_argument(
        "--match", required=True,
        help="KEY=VALUE selecting exactly one record",
    )
    # update-record's positional `updates` is nargs="*"; --from-file is an
    # optional alternative. Mutex is enforced manually in cmd_update_record
    # because argparse's add_mutually_exclusive_group + nargs="*" positional
    # combinations swallow surrounding flags (e.g., --match) as positionals.
    p_update_record.add_argument(
        "updates", nargs="*", default=[],
        help="field=value pairs to apply (omit when using --from-file)",
    )
    p_update_record.add_argument(
        "--from-file", dest="from_file", default=None,
        help=(
            "Path to a JSON file containing an object of field updates. "
            "Mutually exclusive with the positional updates list."
        ),
    )
    p_update_record.set_defaults(func=cmd_update_record)

    p_gotcha = sub.add_parser(
        "append-gotcha", help="Append a string to project.json.gotchas."
    )
    p_gotcha.add_argument("file")
    _add_payload_args(
        p_gotcha, positional="text",
        help_text="Gotcha text (UTF-8 string)",
    )
    p_gotcha.set_defaults(func=cmd_append_gotcha)

    return parser


def _add_payload_args(
    sub_parser: argparse.ArgumentParser,
    *,
    positional: str,
    help_text: str,
) -> None:
    """Attach a (positional | --from-file) payload pair with manual mutex.

    argparse's ``add_mutually_exclusive_group`` is unreliable when one
    member is a positional — interaction with sibling flags like
    ``--match`` breaks. Validation lives in ``_resolve_payload`` instead:
    if both or neither is supplied the handler emits a structured error
    and exits 2.

    Used by ``append``, ``append-record``, ``append-gotcha``.
    """
    sub_parser.add_argument(positional, nargs="?", default=None, help=help_text)
    sub_parser.add_argument(
        "--from-file", dest="from_file", default=None,
        help=(
            f"Path to a UTF-8 file whose content is used as the {positional!r} "
            "payload. Mutually exclusive with the positional argument; bypasses "
            "shell-quoting hazards (PowerShell $-interpolation, heredoc edge cases)."
        ),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args, extras = parser.parse_known_args(argv)
    # argparse's nargs="+/*" positionals can't pick up values that appear
    # *after* an optional flag (e.g., `update-record file --match id=2 status=x`
    # leaves `status=x` in extras). `parse_known_args` collects those into
    # `extras`; we merge them back into the positional list for the one
    # subcommand that takes a variadic positional (`update-record`).
    # Other subcommands surface unknown args as an error like normal argparse.
    if extras:
        if getattr(args, "cmd", None) == "update-record":
            args.updates = list(args.updates or []) + extras
        else:
            sys.stderr.write(
                f"ERROR: unrecognized arguments: {' '.join(extras)}\n"
            )
            return 2
    try:
        return args.func(args)
    except ValueError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

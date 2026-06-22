"""FU-11 — validate the JSON examples embedded in instructions/*.md.

The instruction files teach the worker to write state via `state.py`
commands whose payloads are inline JSON records (e.g.
``state.py append-record steps.json '{...}'`` or
``state.py append devlog.jsonl '{...}'``). A schema change can silently
invalidate those examples — the worker then copies a now-wrong shape. This
test lifts every such example and validates it against the registered
schema so doc drift fails CI.

Scope: the ``append`` / ``append-record`` examples whose payload is a
single record (devlog entry, or one element of an array file). Other
commands are intentionally skipped: ``append-gotcha`` (a string),
``update-record`` (a partial field-update, not a full record), ``set``
(key=value), and ``--from-file`` (no inline JSON).
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
INSTRUCTIONS_DIR = I2C_ROOT / "i2c" / "data" / "instructions"

from i2c import validate as v

# file token in the state.py command → (schema filename, is_array_file).
# Array files validate the whole file; one appended record is validated
# against the array schema's `items`. devlog.jsonl is already per-record.
_RECORD_SCHEMA: dict[str, tuple[str, bool]] = {
    "steps.json": ("steps.schema.json", True),
    "phases.json": ("phases.schema.json", True),
    "decisions.json": ("decisions.schema.json", True),
    "devlog.jsonl": (v.DEVLOG_ENTRY_SCHEMA, False),
}

# `state.py (append|append-record) <FILE> '{` — the JSON object starts at
# the trailing brace. `append-record` is listed first so the alternation
# prefers it; `append-gotcha` never matches (no whitespace after "append").
_CMD = re.compile(r"(?:append-record|append)\s+([\w.]+)\s*'\s*\{")

# Sanity floor: if the extractor silently matches nothing (e.g. a regex
# regression), the test must fail rather than pass vacuously.
_MIN_EXAMPLES = 20


def _json_object_at(text: str, brace_index: int) -> str | None:
    """Return the balanced ``{...}`` substring starting at ``brace_index``.

    String-aware so braces inside JSON string values don't miscount.
    """
    depth = 0
    in_str = False
    esc = False
    for i in range(brace_index, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[brace_index:i + 1]
    return None


def _iter_examples():
    """Yield (md_path, file_token, json_text) for each inline record example."""
    for md in sorted(INSTRUCTIONS_DIR.glob("*.md")):
        text = md.read_text(encoding="utf-8")
        for m in _CMD.finditer(text):
            file_token = m.group(1)
            if file_token not in _RECORD_SCHEMA:
                continue
            raw = _json_object_at(text, m.end() - 1)
            if raw is not None:
                yield md, file_token, raw


class TestInstructionJsonExamples(unittest.TestCase):
    def test_inline_examples_validate(self):
        failures: list[str] = []
        count = 0
        schema_cache: dict[str, dict] = {}
        for md, file_token, raw in _iter_examples():
            count += 1
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                failures.append(f"{md.name}: not valid JSON ({e}): {raw[:60]}...")
                continue
            schema_name, is_array = _RECORD_SCHEMA[file_token]
            if schema_name not in schema_cache:
                s = v.load_schema(schema_name)
                schema_cache[schema_name] = s["items"] if is_array else s
            try:
                v.validate_json_schema(
                    data, schema_cache[schema_name],
                    label=f"{md.name} → {file_token}",
                )
            except ValueError as e:
                failures.append(f"{md.name} → {file_token}: {e}")
        self.assertEqual(failures, [], msg="\n".join(failures))
        self.assertGreaterEqual(
            count, _MIN_EXAMPLES,
            msg=f"only {count} examples found; extractor may have regressed",
        )


if __name__ == "__main__":
    unittest.main()

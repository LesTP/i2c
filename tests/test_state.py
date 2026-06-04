"""Tests for tools/state.py — atomic, schema-validated state writes."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest import mock

# Make tools/ importable.
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import state  # noqa: E402


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


class TempStateDir:
    """Context manager: temp dir with .state/ pre-populated for tests."""

    def __enter__(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.state_dir = root / ".state"
        self.state_dir.mkdir()
        write_json(self.state_dir / "project.json", {
            "phase": 1, "state": "execute", "blocked": False, "gotchas": []
        })
        write_json(self.state_dir / "phases.json", [
            {"id": 1, "title": "Bootstrap", "regime": "build", "dependencies": [], "status": "in_progress"},
            {"id": 2, "title": "Loop", "regime": "build", "dependencies": [], "status": "pending"},
        ])
        write_json(self.state_dir / "steps.json", [
            {"phase": 1, "step": 1, "title": "Setup", "status": "complete", "commit": "abc1234"},
            {"phase": 1, "step": 2, "title": "Wire", "status": "pending"},
        ])
        # Empty devlog.
        (self.state_dir / "devlog.jsonl").write_text("", encoding="utf-8")
        return self

    def __exit__(self, *args):
        self._tmp.cleanup()


def run_state(*argv: str) -> tuple[int, str, str]:
    """Run state.main with argv; capture (exit_code, stdout, stderr)."""
    out = StringIO()
    err = StringIO()
    with mock.patch.object(sys, "stdout", out), mock.patch.object(sys, "stderr", err):
        try:
            rc = state.main(list(argv))
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------


class TestParseValue(unittest.TestCase):
    def test_bool(self):
        self.assertIs(state.parse_value("true"), True)
        self.assertIs(state.parse_value("false"), False)

    def test_null(self):
        self.assertIsNone(state.parse_value("null"))

    def test_int(self):
        self.assertEqual(state.parse_value("42"), 42)

    def test_float(self):
        self.assertEqual(state.parse_value("1.5"), 1.5)

    def test_string_fallback(self):
        self.assertEqual(state.parse_value("execute"), "execute")

    def test_array(self):
        self.assertEqual(state.parse_value("[]"), [])
        self.assertEqual(state.parse_value('["a", "b"]'), ["a", "b"])


class TestParseKvPairs(unittest.TestCase):
    def test_parses_multi(self):
        d = state.parse_kv_pairs(["state=review", "blocked=true", "phase=3"])
        self.assertEqual(d, {"state": "review", "blocked": True, "phase": 3})

    def test_missing_equals(self):
        with self.assertRaisesRegex(ValueError, "key=value"):
            state.parse_kv_pairs(["badpair"])

    def test_empty_key(self):
        with self.assertRaisesRegex(ValueError, "Empty key"):
            state.parse_kv_pairs(["=value"])


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------


class TestAtomicWrite(unittest.TestCase):
    def test_writes_and_replaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            state.atomic_write_json(path, {"a": 1})
            self.assertEqual(json.loads(path.read_text()), {"a": 1})

    def test_no_leftover_tempfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            state.atomic_write_json(path, {"a": 1})
            leftovers = [p for p in Path(tmp).iterdir() if p.name != "x.json"]
            self.assertEqual(leftovers, [])

    def test_creates_parent_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "x.json"
            state.atomic_write_json(path, {"a": 1})
            self.assertTrue(path.exists())


# ---------------------------------------------------------------------------
# `set` subcommand
# ---------------------------------------------------------------------------


class TestSet(unittest.TestCase):
    def test_updates_existing_keys(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "set", str(t.state_dir / "project.json"),
                "state=review", "blocked=true",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "project.json").read_text())
            self.assertEqual(data["state"], "review")
            self.assertIs(data["blocked"], True)

    def test_rejects_invalid_enum(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "set", str(t.state_dir / "project.json"), "state=bogus",
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)
            # File untouched.
            data = json.loads((t.state_dir / "project.json").read_text())
            self.assertEqual(data["state"], "execute")

    def test_rejects_unknown_key(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "set", str(t.state_dir / "project.json"), "typo=1",
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)

    def test_missing_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "set", str(t.state_dir / "missing.json"), "state=plan",
            )
            self.assertEqual(rc, 2)

    def test_unregistered_filename(self):
        with TempStateDir() as t:
            other = t.state_dir / "random.json"
            other.write_text("{}")
            rc, out, err = run_state("set", str(other), "x=1")
            self.assertEqual(rc, 2)
            self.assertIn("no schema registered", err)


# ---------------------------------------------------------------------------
# `complete` subcommand
# ---------------------------------------------------------------------------


class TestComplete(unittest.TestCase):
    def test_complete_step(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "complete", str(t.state_dir / "steps.json"),
                "--phase", "1", "--step", "2",
                "--commit", "def5678",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "steps.json").read_text())
            step2 = next(s for s in data if s["step"] == 2)
            self.assertEqual(step2["status"], "complete")
            self.assertEqual(step2["commit"], "def5678")

    def test_complete_step_without_commit(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "complete", str(t.state_dir / "steps.json"),
                "--phase", "1", "--step", "2",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "steps.json").read_text())
            step2 = next(s for s in data if s["step"] == 2)
            self.assertEqual(step2["status"], "complete")
            self.assertNotIn("commit", step2)

    def test_complete_step_no_match(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "complete", str(t.state_dir / "steps.json"),
                "--phase", "99", "--step", "1",
            )
            self.assertEqual(rc, 1)
            self.assertIn("no matching record", err)

    def test_complete_phase(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "complete", str(t.state_dir / "phases.json"),
                "--phase", "1",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "phases.json").read_text())
            phase1 = next(p for p in data if p["id"] == 1)
            self.assertEqual(phase1["status"], "complete")

    def test_complete_invalid_commit_pattern(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "complete", str(t.state_dir / "steps.json"),
                "--phase", "1", "--step", "2", "--commit", "not-hex!",
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)


# ---------------------------------------------------------------------------
# `append` subcommand
# ---------------------------------------------------------------------------


class TestAppend(unittest.TestCase):
    def test_append_valid_devlog_entry(self):
        with TempStateDir() as t:
            entry = {
                "phase": 1, "step": 2, "action": "execute",
                "outcome": "complete", "summary": "wired",
                "timestamp": "2026-06-04T04:00:00Z",
            }
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"),
                json.dumps(entry),
            )
            self.assertEqual(rc, 0, msg=err)
            lines = (t.state_dir / "devlog.jsonl").read_text().splitlines()
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["summary"], "wired")

    def test_append_rejects_bad_outcome(self):
        with TempStateDir() as t:
            entry = {
                "phase": 1, "step": 2, "action": "execute",
                "outcome": "BOGUS", "summary": "x",
                "timestamp": "2026-06-04T04:00:00Z",
            }
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"), json.dumps(entry),
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)

    def test_append_rejects_unregistered_jsonl(self):
        with TempStateDir() as t:
            path = t.state_dir / "random.jsonl"
            rc, out, err = run_state("append", str(path), '{"x":1}')
            self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# `append-gotcha` subcommand
# ---------------------------------------------------------------------------


class TestAppendGotcha(unittest.TestCase):
    def test_appends(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "sed quoting: use copy-paste template",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "project.json").read_text())
            self.assertEqual(len(data["gotchas"]), 1)
            self.assertIn("sed quoting", data["gotchas"][0])

    def test_appends_to_missing_array(self):
        with TempStateDir() as t:
            path = t.state_dir / "project.json"
            data = json.loads(path.read_text())
            del data["gotchas"]
            path.write_text(json.dumps(data))
            rc, out, err = run_state("append-gotcha", str(path), "first one")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(
                json.loads(path.read_text())["gotchas"], ["first one"]
            )

    def test_rejects_empty_text(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"), "   ",
            )
            self.assertEqual(rc, 2)

    def test_rejects_wrong_filename(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "phases.json"), "x",
            )
            self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# Atomic guarantee: validation failure must not modify file
# ---------------------------------------------------------------------------


class TestNoCorruptionOnFailedValidation(unittest.TestCase):
    def test_set_failure_preserves_file(self):
        with TempStateDir() as t:
            path = t.state_dir / "project.json"
            before = path.read_text()
            run_state("set", str(path), "state=bogus")
            after = path.read_text()
            self.assertEqual(before, after)

    def test_complete_failure_preserves_file(self):
        with TempStateDir() as t:
            path = t.state_dir / "steps.json"
            before = path.read_text()
            run_state(
                "complete", str(path),
                "--phase", "1", "--step", "2", "--commit", "BAD!",
            )
            after = path.read_text()
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

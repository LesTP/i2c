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
# `append-record` subcommand
# ---------------------------------------------------------------------------


class TestAppendRecord(unittest.TestCase):
    def test_append_step(self):
        with TempStateDir() as t:
            new_step = {
                "phase": 1, "step": 3, "title": "Cleanup",
                "status": "pending",
            }
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                json.dumps(new_step),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "steps.json").read_text())
            self.assertEqual(len(data), 3)
            self.assertEqual(data[-1]["title"], "Cleanup")

    def test_append_phase(self):
        with TempStateDir() as t:
            new_phase = {
                "id": 3, "title": "Audit", "regime": "build",
                "dependencies": ["Loop"], "status": "pending",
            }
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "phases.json"),
                json.dumps(new_phase),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "phases.json").read_text())
            self.assertEqual(len(data), 3)
            self.assertEqual(data[-1]["id"], 3)

    def test_append_decision(self):
        with TempStateDir() as t:
            path = t.state_dir / "decisions.json"
            path.write_text("[]")
            new_decision = {
                "id": "D-1", "title": "Storage", "status": "open",
                "decision": "Use local FS",
            }
            rc, out, err = run_state(
                "append-record", str(path), json.dumps(new_decision),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(path.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "D-1")

    def test_append_record_rejected_by_schema(self):
        with TempStateDir() as t:
            # Missing required `regime` field on phase.
            bad_phase = {"id": 3, "title": "x", "status": "pending"}
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "phases.json"),
                json.dumps(bad_phase),
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)
            # File untouched.
            data = json.loads((t.state_dir / "phases.json").read_text())
            self.assertEqual(len(data), 2)

    def test_append_record_rejects_unknown_filename(self):
        with TempStateDir() as t:
            other = t.state_dir / "random.json"
            other.write_text("[]")
            rc, out, err = run_state(
                "append-record", str(other), '{"x": 1}',
            )
            self.assertEqual(rc, 2)
            self.assertIn("no schema registered", err)

    def test_append_record_rejects_non_array_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "project.json"),
                '{"x": 1}',
            )
            self.assertEqual(rc, 2)
            self.assertIn("JSON array file", err)

    def test_append_record_rejects_missing_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "nope.json"),
                '{"x": 1}',
            )
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err)

    def test_append_record_rejects_bad_json(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                '{not valid json',
            )
            self.assertEqual(rc, 2)
            self.assertIn("not valid JSON", err)

    def test_append_record_rejects_non_object(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                '[1, 2, 3]',
            )
            self.assertEqual(rc, 2)
            self.assertIn("must be a JSON object", err)


# ---------------------------------------------------------------------------
# `update-record` subcommand
# ---------------------------------------------------------------------------


class TestUpdateRecord(unittest.TestCase):
    def test_update_decision_status(self):
        with TempStateDir() as t:
            path = t.state_dir / "decisions.json"
            path.write_text(json.dumps([
                {"id": "D-1", "title": "Storage", "status": "open",
                 "decision": "TBD"},
            ]))
            rc, out, err = run_state(
                "update-record", str(path),
                "--match", "id=D-1",
                "status=closed",
                "decision=Use local FS",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(path.read_text())
            self.assertEqual(data[0]["status"], "closed")
            self.assertEqual(data[0]["decision"], "Use local FS")

    def test_update_phase_status(self):
        with TempStateDir() as t:
            # FU-13 scenario: flip phase from pending to in_progress
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=2",
                "status=in_progress",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "phases.json").read_text())
            phase2 = next(p for p in data if p["id"] == 2)
            self.assertEqual(phase2["status"], "in_progress")

    def test_update_step_notes(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "steps.json"),
                "--match", "step=2",
                "notes=Reordered after dep-probe.",
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "steps.json").read_text())
            step2 = next(s for s in data if s["step"] == 2)
            self.assertEqual(step2["notes"], "Reordered after dep-probe.")

    def test_no_match(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=999",
                "status=complete",
            )
            self.assertEqual(rc, 1)
            self.assertIn("no record", err)

    def test_multi_match_rejected(self):
        with TempStateDir() as t:
            # Inject a duplicate id to trigger multi-match.
            path = t.state_dir / "phases.json"
            data = json.loads(path.read_text())
            data.append({"id": 1, "title": "Dup", "regime": "build",
                         "dependencies": [], "status": "pending"})
            path.write_text(json.dumps(data))
            rc, out, err = run_state(
                "update-record", str(path),
                "--match", "id=1",
                "status=complete",
            )
            self.assertEqual(rc, 1)
            self.assertIn("match must be unique", err)

    def test_schema_rejection_preserves_file(self):
        with TempStateDir() as t:
            path = t.state_dir / "phases.json"
            before = path.read_text()
            rc, out, err = run_state(
                "update-record", str(path),
                "--match", "id=1",
                "status=bogus",
            )
            self.assertEqual(rc, 1)
            self.assertIn("VALIDATION FAILED", err)
            self.assertEqual(path.read_text(), before)

    def test_match_value_type_coerced(self):
        # phase ids are integers; --match must coerce "1" to 1 for equality.
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=1",
                "status=complete",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_missing_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "missing.json"),
                "--match", "id=1",
                "status=closed",
            )
            self.assertEqual(rc, 2)

    def test_missing_match_flag(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "status=complete",
            )
            # argparse exits 2 when required flag missing
            self.assertEqual(rc, 2)

    def test_no_updates(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=1",
            )
            # argparse: 'updates' is nargs="+" so missing → exit 2
            self.assertEqual(rc, 2)

    def test_rejects_non_array_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "project.json"),
                "--match", "phase=1",
                "blocked=true",
            )
            self.assertEqual(rc, 2)
            self.assertIn("JSON array file", err)


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


# ---------------------------------------------------------------------------
# FU-19: bare-filename auto-resolution to .state/<name>
# ---------------------------------------------------------------------------


class TestResolveStatePath(unittest.TestCase):
    """Bare schema filenames resolve to .state/<name> when CWD is a project root."""

    def _cd_to_root(self, t: TempStateDir) -> Path:
        """The project root is the parent of t.state_dir; chdir to it."""
        root = t.state_dir.parent
        self._prev_cwd = Path.cwd()
        os.chdir(root)
        return root

    def _restore_cwd(self):
        os.chdir(self._prev_cwd)

    def test_bare_filename_resolves_when_cwd_is_project_root(self):
        with TempStateDir() as t:
            self._cd_to_root(t)
            try:
                # Bare `project.json` → .state/project.json
                rc, out, err = run_state("set", "project.json", "state=review")
                self.assertEqual(rc, 0, msg=err)
                data = json.loads((t.state_dir / "project.json").read_text())
                self.assertEqual(data["state"], "review")
            finally:
                self._restore_cwd()

    def test_bare_filename_errors_when_no_state_dir(self):
        # Outside any project root the bare filename returns Path("project.json")
        # which doesn't exist; cmd_set surfaces the standard "does not exist" error.
        with tempfile.TemporaryDirectory() as tmp:
            prev = Path.cwd()
            os.chdir(tmp)
            try:
                rc, out, err = run_state("set", "project.json", "state=review")
                self.assertEqual(rc, 2)
                self.assertIn("does not exist", err)
            finally:
                os.chdir(prev)

    def test_explicit_state_path_still_works(self):
        with TempStateDir() as t:
            # Explicit `.state/project.json` (the pre-FU-19 examples) still
            # resolves and works exactly as before.
            self._cd_to_root(t)
            try:
                rc, out, err = run_state(
                    "set", ".state/project.json", "state=review",
                )
                self.assertEqual(rc, 0, msg=err)
            finally:
                self._restore_cwd()

    def test_explicit_absolute_path_still_works(self):
        with TempStateDir() as t:
            # Absolute paths bypass the resolver entirely (they already exist).
            rc, out, err = run_state(
                "set", str(t.state_dir / "project.json"), "state=review",
            )
            self.assertEqual(rc, 0, msg=err)

    def test_unknown_bare_filename_not_resolved(self):
        # `random.json` is not in SCHEMA_BY_FILENAME and not devlog.jsonl;
        # the resolver leaves it as-is and the standard "does not exist"
        # error surfaces.
        with TempStateDir() as t:
            self._cd_to_root(t)
            try:
                rc, out, err = run_state("set", "random.json", "x=1")
                self.assertEqual(rc, 2)
                # cmd_set's first check is path.exists(); failure is the
                # standard "does not exist" path.
                self.assertIn("does not exist", err)
            finally:
                self._restore_cwd()

    def test_bare_devlog_filename_resolves(self):
        # devlog.jsonl is whitelisted alongside SCHEMA_BY_FILENAME entries.
        with TempStateDir() as t:
            self._cd_to_root(t)
            try:
                entry = {
                    "phase": 1, "step": 2, "action": "execute",
                    "outcome": "complete", "summary": "via bare name",
                    "timestamp": "2026-06-06T10:00:00Z",
                }
                rc, out, err = run_state(
                    "append", "devlog.jsonl", json.dumps(entry),
                )
                self.assertEqual(rc, 0, msg=err)
                # File was the .state/ one; original empty devlog now has 1 line.
                lines = (t.state_dir / "devlog.jsonl").read_text().splitlines()
                self.assertEqual(len(lines), 1)
            finally:
                self._restore_cwd()


# ---------------------------------------------------------------------------
# FU-21: --from-file payload across the four payload-bearing subcommands
# ---------------------------------------------------------------------------


class TestFromFileAppend(unittest.TestCase):
    def test_round_trips_valid_jsonl_record(self):
        with TempStateDir() as t:
            entry = {
                "phase": 1, "step": 2, "action": "execute",
                "outcome": "complete", "summary": "loaded from file",
                "timestamp": "2026-06-06T10:00:00Z",
            }
            payload = t.state_dir.parent / "payload.json"
            payload.write_text(json.dumps(entry), encoding="utf-8")
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"),
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 0, msg=err)
            lines = (t.state_dir / "devlog.jsonl").read_text().splitlines()
            self.assertEqual(json.loads(lines[0])["summary"], "loaded from file")

    def test_inline_and_from_file_mutex(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "p.json"
            payload.write_text('{"x":1}', encoding="utf-8")
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"),
                '{"x":1}', "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("mutually exclusive", err)

    def test_missing_from_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"),
                "--from-file", str(t.state_dir.parent / "nope.json"),
            )
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err)

    def test_neither_inline_nor_from_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append", str(t.state_dir / "devlog.jsonl"),
            )
            self.assertEqual(rc, 2)
            self.assertIn("missing payload", err)


class TestFromFileAppendRecord(unittest.TestCase):
    def test_round_trips_valid_json_record(self):
        with TempStateDir() as t:
            record = {
                "phase": 1, "step": 3, "title": "Cleanup",
                "status": "pending",
            }
            payload = t.state_dir.parent / "step.json"
            payload.write_text(json.dumps(record), encoding="utf-8")
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "steps.json").read_text())
            self.assertEqual(data[-1]["title"], "Cleanup")

    def test_inline_and_from_file_mutex(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "s.json"
            payload.write_text('{"phase":1,"step":3,"title":"x","status":"pending"}',
                               encoding="utf-8")
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                '{"phase":1,"step":3,"title":"x","status":"pending"}',
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("mutually exclusive", err)

    def test_missing_from_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-record", str(t.state_dir / "steps.json"),
                "--from-file", str(t.state_dir.parent / "nope.json"),
            )
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err)


class TestFromFileUpdateRecord(unittest.TestCase):
    def test_round_trips_field_updates(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "updates.json"
            payload.write_text(json.dumps({"status": "in_progress"}),
                               encoding="utf-8")
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=2",
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "phases.json").read_text())
            phase2 = next(p for p in data if p["id"] == 2)
            self.assertEqual(phase2["status"], "in_progress")

    def test_positional_and_from_file_mutex(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "u.json"
            payload.write_text('{"status":"in_progress"}', encoding="utf-8")
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=2",
                "status=in_progress",
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("mutually exclusive", err)

    def test_from_file_must_be_json_object(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "bad.json"
            payload.write_text('[1, 2, 3]', encoding="utf-8")
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=2",
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("JSON object", err)

    def test_missing_from_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "update-record", str(t.state_dir / "phases.json"),
                "--match", "id=2",
                "--from-file", str(t.state_dir.parent / "nope.json"),
            )
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err)


class TestFromFileAppendGotcha(unittest.TestCase):
    def test_round_trips_plain_text(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "gotcha.txt"
            payload.write_text(
                "Watch out for the buffer-edge race", encoding="utf-8",
            )
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "project.json").read_text())
            self.assertEqual(len(data["gotchas"]), 1)
            self.assertIn("buffer-edge race", data["gotchas"][0])

    def test_dollar_laden_payload_round_trips(self):
        """The actual pilot bug: PowerShell ate `$defs` from inline strings.

        --from-file bypasses shell quoting entirely. The file content lands
        in project.json byte-for-byte (modulo strip()).
        """
        with TempStateDir() as t:
            payload = t.state_dir.parent / "gotcha.txt"
            text = (
                "JSON Schema authoring: remember $defs and $refs are "
                "schema-level reserved keys; nested types must declare "
                "$schema at top level."
            )
            payload.write_text(text, encoding="utf-8")
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 0, msg=err)
            data = json.loads((t.state_dir / "project.json").read_text())
            gotcha = data["gotchas"][-1]
            self.assertIn("$defs", gotcha)
            self.assertIn("$refs", gotcha)
            self.assertIn("$schema", gotcha)

    def test_inline_and_from_file_mutex(self):
        with TempStateDir() as t:
            payload = t.state_dir.parent / "g.txt"
            payload.write_text("from file", encoding="utf-8")
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "inline gotcha", "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("mutually exclusive", err)

    def test_missing_from_file(self):
        with TempStateDir() as t:
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "--from-file", str(t.state_dir.parent / "nope.txt"),
            )
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err)

    def test_empty_file_rejected(self):
        # Same as inline empty-string: rejected so we don't pollute gotchas.
        with TempStateDir() as t:
            payload = t.state_dir.parent / "empty.txt"
            payload.write_text("   \n  ", encoding="utf-8")
            rc, out, err = run_state(
                "append-gotcha", str(t.state_dir / "project.json"),
                "--from-file", str(payload),
            )
            self.assertEqual(rc, 2)
            self.assertIn("cannot be empty", err)


if __name__ == "__main__":
    unittest.main()

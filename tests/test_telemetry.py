"""Tests for the telemetry sidecar (.state/telemetry.jsonl).

Covers the schema, the generalized validated-JSONL append path, the pure
derivation helpers, runner integration (a valid row is written), and the
never-fatal rule (a telemetry failure can't change the iteration's exit code).
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from i2c import run_iteration as ri
from i2c import state
from i2c import telemetry as tel
from i2c import validate as v

# Reuse the runner's end-to-end fixture (copies initial_state + framework).
from test_run_iteration import TempProject, make_fake_invoker, run_iter, signal_block


def _valid_row(**overrides):
    row = tel.build_row(
        iteration=1, phase=2, action="execute", backend="claude",
        timestamp="2026-06-30T08:00:00+00:00",
    )
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class TestSchema(unittest.TestCase):
    def setUp(self):
        self.schema = v.load_schema(v.TELEMETRY_ENTRY_SCHEMA)

    def test_minimal_row_is_valid(self):
        v.validate_json_schema(_valid_row(), self.schema, label="row")

    def test_populated_row_is_valid(self):
        row = _valid_row(
            step=3, outcome="complete", exit_code=0, model="sonnet", tier="T1",
            tokens_in=1100, tokens_out=50, tokens_cached=800, cost_usd=0.07,
            cost_source="pricing:v1", wall_clock_s=12.5, tool_calls=4,
            start_commit="abc1234", end_commit="def5678",
            prompt_hash="sha256:" + "0" * 64, files_touched=2, loc_added=140,
            loc_removed=12, regime="build", leaf=True, tests_pass=True,
            tests_cmd="pytest -q", drift_flag=False,
            review_findings={"must": 0, "should": 1, "optional": 3},
        )
        v.validate_json_schema(row, self.schema, label="row")

    def test_unknown_field_rejected(self):
        with self.assertRaises(ValueError):
            v.validate_json_schema(_valid_row(surprise=1), self.schema)

    def test_missing_required_rejected(self):
        row = _valid_row()
        del row["backend"]
        with self.assertRaises(ValueError):
            v.validate_json_schema(row, self.schema)

    def test_bad_enum_rejected(self):
        with self.assertRaises(ValueError):
            v.validate_json_schema(_valid_row(mode="interactive"), self.schema)

    def test_bad_exit_code_rejected(self):
        with self.assertRaises(ValueError):
            v.validate_json_schema(_valid_row(exit_code=1), self.schema)

    def test_phase_zero_allowed(self):
        # First PLAN dispatches while project.phase is still 0.
        v.validate_json_schema(_valid_row(phase=0, action="plan"), self.schema)


# ---------------------------------------------------------------------------
# Generalized validated JSONL append
# ---------------------------------------------------------------------------


class TestAppendValidatedJsonl(unittest.TestCase):
    def test_valid_telemetry_appends(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "telemetry.jsonl"
            state.append_validated_jsonl(
                path, _valid_row(), schema_name=v.TELEMETRY_ENTRY_SCHEMA
            )
            lines = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(json.loads(lines[0])["action"], "execute")

    def test_invalid_telemetry_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "telemetry.jsonl"
            with self.assertRaises(ValueError):
                state.append_validated_jsonl(
                    path, _valid_row(mode="bogus"),
                    schema_name=v.TELEMETRY_ENTRY_SCHEMA,
                )
            self.assertFalse(path.exists())

    def test_cmd_append_routes_telemetry_filename(self):
        # `i2c state append telemetry.jsonl <json>` resolves + validates.
        with tempfile.TemporaryDirectory() as d:
            state_dir = Path(d) / ".state"
            state_dir.mkdir()
            (state_dir / "telemetry.jsonl").write_text("", encoding="utf-8")
            import os
            prev = Path.cwd()
            os.chdir(d)
            try:
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = state.main(["append", "telemetry.jsonl", json.dumps(_valid_row())])
                self.assertEqual(rc, 0)
            finally:
                os.chdir(prev)
            lines = [
                l for l in (state_dir / "telemetry.jsonl").read_text(
                    encoding="utf-8").splitlines() if l.strip()
            ]
            self.assertEqual(len(lines), 1)


# ---------------------------------------------------------------------------
# Pure derivation helpers
# ---------------------------------------------------------------------------


class TestHelpers(unittest.TestCase):
    def test_prompt_hash_shape(self):
        h = tel.prompt_hash("hello")
        self.assertTrue(h.startswith("sha256:"))
        self.assertEqual(len(h), len("sha256:") + 64)
        # Deterministic.
        self.assertEqual(h, tel.prompt_hash("hello"))

    def test_phase_meta(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d) / ".state"
            sd.mkdir()
            (sd / "phases.json").write_text(json.dumps([
                {"id": 3, "title": "t", "status": "pending", "regime": "build",
                 "dependencies": []},
                {"id": 4, "title": "u", "status": "pending", "regime": "refine",
                 "dependencies": ["x"]},
            ]), encoding="utf-8")
            self.assertEqual(tel.phase_meta(Path(d), 3), ("build", True))
            self.assertEqual(tel.phase_meta(Path(d), 4), ("refine", False))
            self.assertEqual(tel.phase_meta(Path(d), 99), (None, None))

    def test_phase_meta_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(tel.phase_meta(Path(d), 1), (None, None))

    def test_devlog_tail_since(self):
        with tempfile.TemporaryDirectory() as d:
            sd = Path(d) / ".state"
            sd.mkdir()
            entries = [
                {"phase": 1, "step": 1, "action": "execute", "outcome": "complete",
                 "summary": "s", "timestamp": "2026-06-30T00:00:00Z"},
                {"phase": 1, "step": 2, "action": "execute", "outcome": "partial",
                 "summary": "s", "timestamp": "2026-06-30T00:01:00Z"},
            ]
            (sd / "devlog.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
            # Only the line appended after prev_count=1 is considered.
            self.assertEqual(tel.devlog_tail_since(Path(d), 1, "execute"), (2, "partial"))
            # Nothing new after prev_count=2.
            self.assertEqual(tel.devlog_tail_since(Path(d), 2, "execute"), (None, None))
            # No matching action among the new lines.
            self.assertEqual(tel.devlog_tail_since(Path(d), 1, "review"), (None, None))

    def test_head_commit_none_outside_repo(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(tel.head_commit(Path(d)))


# ---------------------------------------------------------------------------
# Runner integration
# ---------------------------------------------------------------------------


class TestRunnerWritesTelemetry(unittest.TestCase):
    def _telemetry_rows(self, root: Path):
        path = root / ".state" / "telemetry.jsonl"
        if not path.is_file():
            return []
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_row_written_on_happy_path(self):
        with TempProject() as p:
            # Fake claude returns JSON with a usage block so tokens populate.
            raw = json.dumps({
                "type": "result",
                "result": signal_block(exit_code=0, reason="ok"),
                "usage": {
                    "input_tokens": 100, "output_tokens": 50,
                    "cache_read_input_tokens": 800,
                    "cache_creation_input_tokens": 200,
                },
            })
            rc, _, err = run_iter(invoker=make_fake_invoker(raw))
            self.assertEqual(rc, 0, msg=err)
            rows = self._telemetry_rows(p.root)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            # Schema-valid.
            v.validate_jsonl(
                p.root / ".state" / "telemetry.jsonl", v.TELEMETRY_ENTRY_SCHEMA)
            # Envelope basics (fixture is phase 2, EXECUTE, claude).
            self.assertEqual(row["schema_version"], 1)
            self.assertEqual(row["iteration"], 1)
            self.assertEqual(row["phase"], 2)
            self.assertEqual(row["action"], "execute")
            self.assertEqual(row["mode"], "autonomous")
            self.assertEqual(row["backend"], "claude")
            self.assertEqual(row["model"], "sonnet")
            self.assertEqual(row["exit_code"], 0)
            # Tokens from the usage block (gross input = 100+800+200).
            self.assertEqual(row["tokens_in"], 1100)
            self.assertEqual(row["tokens_out"], 50)
            self.assertEqual(row["tokens_cached"], 800)
            # No git repo in the temp fixture → commit fields null.
            self.assertIsNone(row["start_commit"])
            self.assertIsNone(row["end_commit"])
            # Prompt hash present.
            self.assertTrue(row["prompt_hash"].startswith("sha256:"))
            self.assertIsNotNone(row["wall_clock_s"])

    def test_codex_model_left_null(self):
        with TempProject() as p:
            def fake_codex(prompt, *, cwd):
                jsonl = json.dumps({
                    "type": "item.completed",
                    "item": {"type": "agent_message",
                             "text": signal_block(exit_code=0, reason="ok")},
                }) + "\n"
                return 0, jsonl, signal_block(exit_code=0, reason="ok")

            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = ri.run_iteration(
                    backend="codex", model="sonnet", max_budget_usd=5.0,
                    codex_invoker=fake_codex,
                )
            self.assertEqual(rc, 0, msg=err.getvalue())
            rows = self._telemetry_rows(p.root)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["backend"], "codex")
            self.assertIsNone(rows[0]["model"])

    def test_telemetry_failure_is_non_fatal(self):
        with TempProject() as p:
            def boom(*a, **k):
                raise RuntimeError("disk full")

            orig = tel.record_iteration
            tel.record_iteration = boom
            try:
                rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            finally:
                tel.record_iteration = orig
            # Worker's exit code is preserved; only a NOTE is emitted.
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("telemetry skipped", err)
            self.assertEqual(self._telemetry_rows(p.root), [])

    def test_no_row_on_state_machine_exit(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary")
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            self.assertEqual(rc, 0, msg=err)
            # EXIT short-circuits before any worker invocation → no telemetry.
            self.assertEqual(self._telemetry_rows(p.root), [])


if __name__ == "__main__":
    unittest.main()

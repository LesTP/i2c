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
from i2c import config as cfg
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

    def test_refine_action_row_valid(self):
        # Refine-tier telemetry: action="refine" with the optional fu/kind
        # columns (D-refine-8, Q-refine-3).
        row = _valid_row(
            phase=0, action="refine", regime="refine", fu="FU-42", kind="prose",
        )
        v.validate_json_schema(row, self.schema, label="row")


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
            # Increment 2: sonnet is priced in the bundled table.
            self.assertEqual(row["tier"], "T1")
            self.assertAlmostEqual(row["cost_usd"], 0.00189, places=6)
            self.assertTrue(row["cost_source"].startswith("pricing:"))
            # Oracle off by default → tests_pass null.
            self.assertIsNone(row["tests_pass"])

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


# ---------------------------------------------------------------------------
# Increment 2: pricing / cost / tier
# ---------------------------------------------------------------------------


class TestPricing(unittest.TestCase):
    PRICING = {
        "version": "x",
        "models": {"sonnet": {"tier": "T1", "in": 3.0, "cached": 0.3, "out": 15.0}},
    }

    def test_cost_and_tier_priced(self):
        usage = {"input": 1100, "output": 50, "cached": 800}
        cost, source, tier = tel.cost_and_tier(usage, "sonnet", self.PRICING)
        # fresh = 1100 - 800 = 300; (300*3 + 800*0.3 + 50*15)/1e6 = 0.00189
        self.assertAlmostEqual(cost, 0.00189, places=6)
        self.assertEqual(source, "pricing:x")
        self.assertEqual(tier, "T1")

    def test_unpriced_model_keeps_cost_none(self):
        usage = {"input": 100, "output": 10, "cached": 0}
        cost, source, tier = tel.cost_and_tier(usage, "ghost", self.PRICING)
        self.assertIsNone(cost)
        self.assertEqual(source, "unpriced")
        self.assertIsNone(tier)

    def test_no_usage_still_reports_tier(self):
        cost, source, tier = tel.cost_and_tier(None, "sonnet", self.PRICING)
        self.assertIsNone(cost)
        self.assertIsNone(source)
        self.assertEqual(tier, "T1")

    def test_load_pricing_bundled_and_override(self):
        p = tel.load_pricing()
        self.assertIn("sonnet", p["models"])
        self.assertEqual(p["models"]["sonnet"]["tier"], "T1")
        p2 = tel.load_pricing(
            overrides={"mymodel": {"tier": "T9", "in": 1.0, "cached": 0.0, "out": 2.0}}
        )
        self.assertIn("mymodel", p2["models"])
        self.assertIn("sonnet", p2["models"])  # bundled retained


# ---------------------------------------------------------------------------
# Increment 2: [telemetry] config
# ---------------------------------------------------------------------------


class TestTelemetryConfig(unittest.TestCase):
    def test_loads_test_cmd_and_pricing(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "i2c.toml").write_text(
                '[telemetry]\n'
                'test_cmd = "pytest -q"\n'
                '[telemetry.pricing.foo]\n'
                'tier = "T9"\nin = 1.0\ncached = 0.0\nout = 2.0\n',
                encoding="utf-8",
            )
            tc = cfg.load_telemetry_config(Path(d))
            self.assertEqual(tc.test_cmd, "pytest -q")
            self.assertEqual(tc.pricing["foo"]["tier"], "T9")

    def test_absent_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            tc = cfg.load_telemetry_config(Path(d))
            self.assertIsNone(tc.test_cmd)
            self.assertEqual(tc.pricing, {})

    def test_bad_test_cmd_type_raises(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "i2c.toml").write_text(
                "[telemetry]\ntest_cmd = 123\n", encoding="utf-8")
            with self.assertRaises(cfg.ConfigError):
                cfg.load_telemetry_config(Path(d))


# ---------------------------------------------------------------------------
# Increment 2: tests oracle (opt-in) wired through the runner
# ---------------------------------------------------------------------------


class TestTestsOracle(unittest.TestCase):
    def _rows(self, root: Path):
        path = root / ".state" / "telemetry.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_oracle_records_pass(self):
        with TempProject() as p:
            (p.root / "ok.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            (p.root / "i2c.toml").write_text(
                '[telemetry]\ntest_cmd = "python ok.py"\n', encoding="utf-8")
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            self.assertEqual(rc, 0, msg=err)
            row = self._rows(p.root)[0]
            self.assertTrue(row["tests_pass"])
            self.assertEqual(row["tests_cmd"], "python ok.py")

    def test_oracle_records_fail(self):
        with TempProject() as p:
            (p.root / "bad.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
            (p.root / "i2c.toml").write_text(
                '[telemetry]\ntest_cmd = "python bad.py"\n', encoding="utf-8")
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            # Worker succeeded; the oracle failing does NOT change the run's exit.
            self.assertEqual(rc, 0, msg=err)
            self.assertFalse(self._rows(p.root)[0]["tests_pass"])

    def test_oracle_off_warns_not_silent(self):
        # FU-52: no test_cmd -> tests_pass null, but the runner must warn so a
        # null oracle across a run is never silent.
        with TempProject() as p:
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            self.assertEqual(rc, 0, msg=err)
            self.assertIsNone(self._rows(p.root)[0]["tests_pass"])
            self.assertIn("telemetry oracle off", err)

    def test_oracle_phase_interpolation_scoped_pass(self):
        # FU-44: {phase} targets the frozen acceptance suite; tests_cmd records
        # the interpolated command actually run (fixture is phase 2).
        with TempProject() as p:
            acc = p.root / "tests" / "acceptance" / "phase_2"
            acc.mkdir(parents=True)
            (acc / "ok.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
            (p.root / "i2c.toml").write_text(
                '[telemetry]\n'
                'test_cmd = "python tests/acceptance/phase_{phase}/ok.py"\n',
                encoding="utf-8")
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            self.assertEqual(rc, 0, msg=err)
            row = self._rows(p.root)[0]
            self.assertTrue(row["tests_pass"])
            self.assertEqual(
                row["tests_cmd"], "python tests/acceptance/phase_2/ok.py")

    def test_oracle_scoped_skips_when_suite_absent(self):
        # FU-44: a {phase}-scoped oracle skips (tests_pass=None) when the frozen
        # acceptance dir does not exist yet — not a false red.
        with TempProject() as p:
            (p.root / "i2c.toml").write_text(
                '[telemetry]\n'
                'test_cmd = "python tests/acceptance/phase_{phase}/ok.py"\n',
                encoding="utf-8")
            rc, _, err = run_iter(invoker=make_fake_invoker(signal_block()))
            self.assertEqual(rc, 0, msg=err)
            self.assertIsNone(self._rows(p.root)[0]["tests_pass"])


if __name__ == "__main__":
    unittest.main()

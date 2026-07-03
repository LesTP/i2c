"""Tests for tools/cli.py — the i2c console dispatcher over control."""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import cli  # noqa: E402
from i2c import control  # noqa: E402
from i2c import run_iteration  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = cli.main(list(argv))
            if rc is None:
                rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


class ChdirFixture:
    """Chdir into the read-only fixture (or a given root) for the test body."""

    def __init__(self, root: Path):
        self.root = root

    def __enter__(self):
        self._prev = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev)


class TempProject:
    """Copy the fixture into a temp dir and chdir into it (for write tests)."""

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_cli_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        self._prev = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev)
        self._tmp.cleanup()

    def patch_project(self, **kwargs) -> None:
        path = self.root / ".state" / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(kwargs)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def read_project(self) -> dict:
        path = self.root / ".state" / "project.json"
        return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Read commands (against the read-only fixture)
# ---------------------------------------------------------------------------


class TestReadCommands(unittest.TestCase):
    def test_status_text(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("status")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("event_store", out)
        self.assertIn("execute", out)
        self.assertIn("Phase:", out)

    def test_status_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("status", "--json")
        self.assertEqual(rc, 0, msg=err)
        data = json.loads(out)
        self.assertEqual(data["phase"], 2)
        self.assertEqual(data["state"], "execute")
        self.assertEqual(data["module"], "event_store")
        self.assertEqual(len(data["steps"]), 4)
        self.assertEqual([d["id"] for d in data["open_decisions"]], ["D-2"])

    def test_next_action_text(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("next-action")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("ACTION: EXECUTE", out)
        self.assertIn("NEXT: execute", out)

    def test_next_action_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("next-action", "--json")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(
            json.loads(out), {"action": "EXECUTE", "next_state": "execute"}
        )

    def test_phase_summary_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("phase-summary", "--phase", "2", "--json")
        self.assertEqual(rc, 0, msg=err)
        data = json.loads(out)
        self.assertEqual(data["module"], "event_store")
        self.assertEqual(len(data["steps"]), 4)
        self.assertEqual([e["phase"] for e in data["devlog"]], [2])

    def test_phase_summary_requires_phase(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("phase-summary")
        # argparse error → exit 2.
        self.assertEqual(rc, 2)

    def test_decisions_all_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("decisions", "--json")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual([d["id"] for d in json.loads(out)], ["D-1", "D-2"])

    def test_decisions_phase_filter_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("decisions", "--phase", "2", "--json")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(json.loads(out), [])  # fixture decisions untagged

    def test_devlog_text(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("devlog", "--phase", "2")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("Append-only", out)

    def test_devlog_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("devlog", "--phase", "1", "--json")
        self.assertEqual(rc, 0, msg=err)
        data = json.loads(out)
        self.assertEqual([e["step"] for e in data], [1, 2, None])

    def test_devlog_all_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("devlog", "--json")
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(len(json.loads(out)), 4)

    def test_escalation_fixture_json(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("escalation", "--json")
        self.assertEqual(rc, 0, msg=err)
        data = json.loads(out)
        self.assertEqual(data["phase"], 2)
        self.assertFalse(data["is_escalated"])
        self.assertIsNone(data["entry"])

    def test_logs_empty_text(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("logs")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("no iterations logged", out)


# ---------------------------------------------------------------------------
# logs + escalation (FU-34)
# ---------------------------------------------------------------------------


class TestLogsAndEscalationCli(unittest.TestCase):
    @staticmethod
    def _seed_logs(p: TempProject) -> None:
        log_dir = p.root / "logs" / "loop"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "summary.log").write_text(
            '2026-06-25T04:03:35+00:00 | iter=1 | backend=claude | '
            'action=EXECUTE | exit=0 | reason="step done"\n',
            encoding="utf-8",
        )
        (log_dir / "iteration_001.txt").write_text("transcript X", encoding="utf-8")

    def test_logs_index_json(self):
        with TempProject() as p:
            self._seed_logs(p)
            rc, out, err = run_cli("logs", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(data[0]["iter"], 1)
            self.assertIsNone(data[0]["transcript"])

    def test_logs_transcript_text(self):
        with TempProject() as p:
            self._seed_logs(p)
            rc, out, err = run_cli("logs", "--iter", "1")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("transcript X", out)

    def test_logs_unknown_iter_exits_2(self):
        with TempProject() as p:
            self._seed_logs(p)
            rc, out, err = run_cli("logs", "--iter", "99")
            self.assertEqual(rc, 2)
            self.assertIn("ERROR", err)

    def test_escalation_detected_text(self):
        with TempProject() as p:
            p.patch_project(state="audit_escalation")
            rc, out, err = run_cli("escalation")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("Escalated: yes", out)


class TestDiagnoseCli(unittest.TestCase):
    def test_diagnose_clean_fixture_text(self):
        # Out-of-repo temp copy: the in-repo fixture would show real git drift.
        with TempProject():
            rc, out, err = run_cli("diagnose")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("Classification: none", out)

    def test_diagnose_json_workflow_drift(self):
        with TempProject() as p:
            steps = json.loads(
                (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
            )
            for s in steps:
                if s["phase"] == 2:
                    s["status"] = "complete"
                    s.setdefault("commit", "abc1234")
            (p.root / ".state" / "steps.json").write_text(
                json.dumps(steps, indent=2) + "\n", encoding="utf-8"
            )
            rc, out, err = run_cli("diagnose", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(data["classification"], "workflow-drift")
            self.assertTrue(data["reconcilable"])

    def test_diagnose_outside_project_exits_2(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_diag_noroot_") as tmp:
            with ChdirFixture(Path(tmp)):
                rc, out, err = run_cli("diagnose")
        self.assertEqual(rc, 2)
        self.assertIn("ERROR", err)


class TestReconcileCli(unittest.TestCase):
    @staticmethod
    def _complete_phase2(p: TempProject) -> None:
        steps = json.loads(
            (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
        )
        for s in steps:
            if s["phase"] == 2:
                s["status"] = "complete"
                s.setdefault("commit", "abc1234")
        (p.root / ".state" / "steps.json").write_text(
            json.dumps(steps, indent=2) + "\n", encoding="utf-8"
        )

    def test_dry_run_does_not_write(self):
        with TempProject() as p:
            self._complete_phase2(p)
            rc, out, err = run_cli("reconcile")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("DRY-RUN", out)
            self.assertEqual(p.read_project()["state"], "execute")

    def test_apply_writes(self):
        with TempProject() as p:
            self._complete_phase2(p)
            rc, out, err = run_cli("reconcile", "--apply", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertTrue(data["applied"])
            self.assertEqual(p.read_project()["state"], "review")


class TestPortfolioCli(unittest.TestCase):
    def test_portfolio_json(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_cli_") as tmp:
            root = Path(tmp)
            shutil.copytree(FIXTURE, root / "a")
            shutil.copytree(FIXTURE, root / "b")
            rc, out, err = run_cli("portfolio", "--root", str(root), "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(len(data["projects"]), 2)

    def test_portfolio_text_empty(self):
        with tempfile.TemporaryDirectory(prefix="i2c_pf_cli2_") as tmp:
            rc, out, err = run_cli("portfolio", "--root", tmp)
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("No i2c projects found", out)


# ---------------------------------------------------------------------------
# clear-boundary (write; temp copy)
# ---------------------------------------------------------------------------


class TestClearBoundary(unittest.TestCase):
    def test_advance(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            rc, out, err = run_cli("clear-boundary", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(data, {"outcome": "advanced", "phase": 3, "state": "plan"})
            on_disk = p.read_project()
            self.assertEqual((on_disk["phase"], on_disk["state"]), (3, "plan"))

    def test_terminate(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            rc, out, err = run_cli("clear-boundary", "--terminate", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(data, {"outcome": "terminated", "phase": 2, "state": "done"})
            self.assertEqual(p.read_project()["state"], "done")

    def test_non_boundary_exits_2(self):
        with TempProject():  # fixture state is execute, not audit_boundary
            rc, out, err = run_cli("clear-boundary")
            self.assertEqual(rc, 2)
            self.assertIn("ERROR", err)
            self.assertIn("audit_boundary", err)


# ---------------------------------------------------------------------------
# run (delegation; no real backend)
# ---------------------------------------------------------------------------


class TestRun(unittest.TestCase):
    def test_run_forwards_args(self):
        captured: dict = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return 0

        original = control.run_iteration
        control.run_iteration = fake
        try:
            rc, out, err = run_cli(
                "run", "--backend", "codex", "--model", "x", "--max-budget-usd", "1.5"
            )
        finally:
            control.run_iteration = original
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(captured["backend"], "codex")
        self.assertEqual(captured["model"], "x")
        self.assertEqual(captured["max_budget_usd"], 1.5)

    def test_run_returns_runner_exit_code(self):
        original = control.run_iteration
        control.run_iteration = lambda **kwargs: 2
        try:
            rc, out, err = run_cli("run")
        finally:
            control.run_iteration = original
        self.assertEqual(rc, 2)

    def test_run_recovery_action_target_forwarded(self):
        captured: dict = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return 0

        original = control.run_iteration
        control.run_iteration = fake
        try:
            with TempProject():
                rc, out, err = run_cli(
                    "run", "--action", "reconcile", "--target", "5"
                )
        finally:
            control.run_iteration = original
        self.assertEqual(rc, 0, msg=err)
        self.assertEqual(captured["action_override"], "reconcile")
        self.assertEqual(captured["target"], 5)

    def _run_capture(self, *argv: str) -> dict:
        captured: dict = {}

        def fake(**kwargs):
            captured.update(kwargs)
            return 0

        original = control.run_iteration
        control.run_iteration = fake
        try:
            rc, out, err = run_cli(*argv)
            self.assertEqual(rc, 0, msg=err)
        finally:
            control.run_iteration = original
        return captured

    def test_toml_supplies_defaults(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_toml_") as tmp:
            (Path(tmp) / "i2c.toml").write_text(
                '[run]\nbackend = "codex"\nmodel = "opus"\nmax_budget_usd = 2.5\n',
                encoding="utf-8",
            )
            with ChdirFixture(Path(tmp)):
                cap = self._run_capture("run")
        # No --backend override: [run].backend flows as the runner's default,
        # and the explicit override is None (resolution happens in the runner).
        self.assertIsNone(cap["backend"])
        self.assertEqual(cap["default_backend"], "codex")
        self.assertEqual(cap["model"], "opus")
        self.assertEqual(cap["max_budget_usd"], 2.5)

    def test_toml_backends_map_forwarded(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_bmap_") as tmp:
            (Path(tmp) / "i2c.toml").write_text(
                '[run]\nbackend = "claude"\n'
                '[run.backends]\nexecute = "codex"\n',
                encoding="utf-8",
            )
            with ChdirFixture(Path(tmp)):
                cap = self._run_capture("run")
        self.assertEqual(cap["backend_map"], {"execute": "codex"})
        self.assertEqual(cap["default_backend"], "claude")

    def test_cli_flag_overrides_toml(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_toml2_") as tmp:
            (Path(tmp) / "i2c.toml").write_text(
                '[run]\nbackend = "codex"\n', encoding="utf-8"
            )
            with ChdirFixture(Path(tmp)):
                cap = self._run_capture("run", "--backend", "claude")
        self.assertEqual(cap["backend"], "claude")

    def test_builtin_defaults_without_toml(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_notoml_") as tmp:
            with ChdirFixture(Path(tmp)):
                cap = self._run_capture("run")
        self.assertIsNone(cap["backend"])
        self.assertEqual(cap["default_backend"], "claude")
        self.assertEqual(cap["backend_map"], {})
        self.assertEqual(cap["model"], run_iteration.DEFAULT_MODEL)
        self.assertEqual(cap["max_budget_usd"], run_iteration.DEFAULT_MAX_BUDGET_USD)

    def test_malformed_toml_exits_2(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_badtoml_") as tmp:
            (Path(tmp) / "i2c.toml").write_text("[run\n", encoding="utf-8")
            with ChdirFixture(Path(tmp)):
                rc, out, err = run_cli("run")
        self.assertEqual(rc, 2)
        self.assertIn("ERROR", err)


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


class TestErrors(unittest.TestCase):
    def test_outside_project_exits_2(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_noroot_") as tmp:
            with ChdirFixture(Path(tmp)):
                rc, out, err = run_cli("status")
        self.assertEqual(rc, 2)
        self.assertIn("ERROR", err)


# ---------------------------------------------------------------------------
# Passthrough subcommands: state + assemble
# ---------------------------------------------------------------------------


class TestStatePassthrough(unittest.TestCase):
    def test_state_complete_marks_step(self):
        with TempProject() as p:
            rc, out, err = run_cli(
                "state", "complete", "steps.json",
                "--phase", "2", "--step", "2", "--commit", "abcd123",
            )
            self.assertEqual(rc, 0, msg=err)
            steps = json.loads(
                (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
            )
            rec = next(s for s in steps if s["phase"] == 2 and s["step"] == 2)
            self.assertEqual(rec["status"], "complete")
            self.assertEqual(rec["commit"], "abcd123")

    def test_state_set_changes_state(self):
        with TempProject() as p:
            rc, out, err = run_cli("state", "set", "project.json", "state=review")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(p.read_project()["state"], "review")

    def test_state_invalid_returns_nonzero(self):
        with TempProject():
            # Schema-invalid value → state.main returns 1 (validation failed).
            rc, out, err = run_cli("state", "set", "project.json", "state=bogus")
            self.assertNotEqual(rc, 0)

    def test_state_no_args_shows_usage(self):
        with TempProject():
            rc, out, err = run_cli("state")
            # argparse requires a subcommand → exit 2.
            self.assertEqual(rc, 2)


class TestAssemblePassthrough(unittest.TestCase):
    def test_assemble_section_architecture(self):
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli("assemble", "--section", "architecture")
        # Fixture has no ARCHITECTURE.md → placeholder, but the section is valid
        # and forwards through the passthrough (rc 0).
        self.assertEqual(rc, 0, msg=err)

    def test_assemble_action_emit_forwards(self):
        # Proves a leading `--action` (an option-like token) forwards through
        # the passthrough to the assembler. The fixture has no ARCH_event_store.md,
        # so the assembler exits 1 with its own "module contract missing" error —
        # which itself proves the args reached assemble_context, not our dispatcher.
        with ChdirFixture(FIXTURE):
            rc, out, err = run_cli(
                "assemble", "--action", "execute", "--phase", "2", "--emit", "user"
            )
        self.assertIn("module contract missing", err)

    def test_assemble_removed_operator_sections_rejected(self):
        # Operator-derived sections were removed in Phase 3a (FU-39); they are
        # no longer valid --section choices. Operators use `i2c status` /
        # `i2c phase-summary` / `i2c devlog` instead.
        with ChdirFixture(FIXTURE):
            for section in ("status", "phase-summary", "devlog"):
                rc, out, err = run_cli("assemble", "--section", section)
                self.assertEqual(rc, 2, msg=f"{section}: {out}{err}")
                self.assertIn("invalid choice", err)


class TestInitEjectCli(unittest.TestCase):
    def test_init_and_eject_in_help(self):
        rc, out, err = run_cli("--help")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("init", out)
        self.assertIn("eject", out)

    def test_init_smoke(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_init_") as tmp:
            with ChdirFixture(Path(tmp)):
                rc, out, err = run_cli("init", "--name", "Demo")
                self.assertEqual(rc, 0, msg=err)
                self.assertTrue((Path(tmp) / ".state" / "project.json").is_file())
                self.assertTrue((Path(tmp) / "CLAUDE.md").is_file())

    def test_init_refuses_existing(self):
        with tempfile.TemporaryDirectory(prefix="i2c_cli_init2_") as tmp:
            with ChdirFixture(Path(tmp)):
                self.assertEqual(run_cli("init")[0], 0)
                rc, out, err = run_cli("init")
                self.assertEqual(rc, 2)
                self.assertIn("ERROR", err)

    def test_eject_list(self):
        rc, out, err = run_cli("eject", "--list")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("WORKER_SPEC.md", out)


class TestMigrateCli(unittest.TestCase):
    def test_migrate_in_help(self):
        rc, out, err = run_cli("--help")
        self.assertEqual(rc, 0, msg=err)
        self.assertIn("migrate", out)

    @staticmethod
    def _make_legacy(p: TempProject) -> None:
        """Turn the (current) fixture copy into a legacy project.json."""
        path = p.root / ".state" / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop("schema_version", None)
        data["blocked"] = False
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def test_migrate_upgrades_legacy(self):
        with TempProject() as p:
            self._make_legacy(p)
            rc, out, err = run_cli("migrate")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("migrated", out)
            data = p.read_project()
            self.assertEqual(data["schema_version"], 1)
            self.assertNotIn("blocked", data)
            # Re-running is a no-op.
            rc2, out2, err2 = run_cli("migrate")
            self.assertEqual(rc2, 0, msg=err2)
            self.assertIn("already", out2)

    def test_migrate_check_exit_codes(self):
        with TempProject() as p:
            self._make_legacy(p)
            rc, out, err = run_cli("migrate", "--check")
            self.assertEqual(rc, 1)
            self.assertIn("migration needed", out)
            # Migrate, then --check is clean.
            run_cli("migrate")
            rc2, out2, err2 = run_cli("migrate", "--check")
            self.assertEqual(rc2, 0, msg=err2)
            self.assertIn("up to date", out2)

    def test_migrate_dry_run_does_not_write(self):
        with TempProject() as p:
            self._make_legacy(p)
            rc, out, err = run_cli("migrate", "--dry-run")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("would migrate", out)
            data = p.read_project()
            self.assertIn("blocked", data)
            self.assertNotIn("schema_version", data)

    def test_migrate_current_reports_already(self):
        with TempProject():  # fixture is already at schema v1
            rc, out, err = run_cli("migrate")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("already", out)

    def test_migrate_newer_than_current_exits_2(self):
        with TempProject() as p:
            p.patch_project(schema_version=99)
            rc, out, err = run_cli("migrate")
            self.assertEqual(rc, 2)
            self.assertIn("ERROR", err)
            # --check on a newer project is also an error (exit 2), not exit 1.
            rc2, out2, err2 = run_cli("migrate", "--check")
            self.assertEqual(rc2, 2)

    def test_migrate_check_and_dry_run_mutually_exclusive(self):
        with TempProject():
            rc, out, err = run_cli("migrate", "--check", "--dry-run")
            # argparse rejects the combination → exit 2.
            self.assertEqual(rc, 2)


class TestFuCli(unittest.TestCase):
    """`i2c fu` — refine backlog CLI group (Proposal A step 4)."""

    def _backlog(self, root: Path):
        return json.loads(
            (root / ".state" / "followups.json").read_text(encoding="utf-8")
        )

    def test_add_creates_backlog_and_assigns_id(self):
        with TempProject() as t:
            rc, out, err = run_cli(
                "fu", "add", "--kind", "prose", "--title", "prose pass",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("added FU-1", out)
            data = self._backlog(t.root)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "FU-1")
            self.assertEqual(data[0]["kind"], "prose")
            self.assertEqual(data[0]["status"], "open")
            self.assertIn("opened", data[0])

    def test_add_auto_increments(self):
        with TempProject() as t:
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli("fu", "add", "--kind", "other", "--title", "two")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("added FU-2", out)
            self.assertEqual([r["id"] for r in self._backlog(t.root)],
                             ["FU-1", "FU-2"])

    def test_add_optional_fields(self):
        with TempProject() as t:
            rc, out, err = run_cli(
                "fu", "add", "--kind", "cli-ergonomics", "--title", "flag",
                "--context", "c", "--trigger", "tg",
                "--files", "a.py, b.py", "--refs", "D-1,9d39390",
            )
            self.assertEqual(rc, 0, msg=err)
            rec = self._backlog(t.root)[0]
            self.assertEqual(rec["files"], ["a.py", "b.py"])
            self.assertEqual(rec["refs"], ["D-1", "9d39390"])
            self.assertEqual(rec["context"], "c")

    def test_add_with_priority(self):
        with TempProject() as t:
            rc, out, err = run_cli(
                "fu", "add", "--kind", "prose", "--title", "one",
                "--priority", "next",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(self._backlog(t.root)[0]["priority"], "next")

    def test_add_invalid_priority_rejected(self):
        with TempProject():
            rc, out, err = run_cli(
                "fu", "add", "--kind", "prose", "--title", "x",
                "--priority", "high",
            )
            self.assertEqual(rc, 2)

    def test_prioritize(self):
        with TempProject() as t:
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli(
                "fu", "prioritize", "FU-1", "--priority", "immediate",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(self._backlog(t.root)[0]["priority"], "immediate")

    def test_list_filter_by_priority(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "one",
                    "--priority", "next")
            run_cli("fu", "add", "--kind", "other", "--title", "two",
                    "--priority", "icebox")
            rc, out, err = run_cli("fu", "list", "--priority", "next", "--json")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual([r["id"] for r in json.loads(out)], ["FU-1"])

    def test_add_invalid_kind_rejected(self):
        with TempProject():
            rc, out, err = run_cli(
                "fu", "add", "--kind", "bugfix", "--title", "x",
            )
            self.assertEqual(rc, 2)  # argparse choices rejection

    def test_list_json(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli("fu", "list", "--json")
            self.assertEqual(rc, 0, msg=err)
            data = json.loads(out)
            self.assertEqual(data[0]["id"], "FU-1")

    def test_list_filter_by_status(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            run_cli("fu", "add", "--kind", "other", "--title", "two")
            run_cli("fu", "close", "FU-1")
            rc, out, err = run_cli("fu", "list", "--status", "open", "--json")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual([r["id"] for r in json.loads(out)], ["FU-2"])

    def test_close_sets_status_resolution_date(self):
        with TempProject() as t:
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli(
                "fu", "close", "FU-1", "--resolution", "done in-session",
            )
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("closed FU-1", out)
            rec = self._backlog(t.root)[0]
            self.assertEqual(rec["status"], "closed")
            self.assertEqual(rec["resolution"], "done in-session")
            self.assertIn("closed", rec)

    def test_close_wontfix(self):
        with TempProject() as t:
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli("fu", "close", "FU-1", "--status", "wontfix")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(self._backlog(t.root)[0]["status"], "wontfix")

    def test_reopen(self):
        with TempProject() as t:
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            run_cli("fu", "close", "FU-1")
            rc, out, err = run_cli("fu", "reopen", "FU-1")
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(self._backlog(t.root)[0]["status"], "open")

    def test_close_unknown_id_fails(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "one")
            rc, out, err = run_cli("fu", "close", "FU-99")
            self.assertEqual(rc, 1)  # update-record no-match

    def test_show(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "prose pass")
            rc, out, err = run_cli("fu", "show", "FU-1")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("FU-1", out)

    def test_show_missing_fails(self):
        with TempProject():
            rc, out, err = run_cli("fu", "show", "FU-99")
            self.assertEqual(rc, 2)
            self.assertIn("no follow-up", err)

    def test_render(self):
        with TempProject():
            run_cli("fu", "add", "--kind", "prose", "--title", "prose pass")
            run_cli("fu", "add", "--kind", "other", "--title", "two")
            run_cli("fu", "close", "FU-2", "--resolution", "nope")
            rc, out, err = run_cli("fu", "render")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("## Follow-ups (open)", out)
            self.assertIn("| FU-1 |", out)
            self.assertIn("## Closed / decided", out)
            self.assertIn("| FU-2 |", out)


if __name__ == "__main__":
    unittest.main()

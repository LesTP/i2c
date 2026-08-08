"""Tests for i2c.surfaces.telegram_core — the transport-agnostic dispatch core.

No python-telegram-bot dependency is touched here (the core must be importable
and testable without the `telegram` extra).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c.surfaces import telegram_core as tc  # noqa: E402
from tests._fixtures import copy_fixture  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


def _make(root: Path, specs: dict) -> None:
    for name, overrides in specs.items():
        dst = root / name
        copy_fixture(dst)
        if overrides:
            pj = dst / ".state" / "project.json"
            data = json.loads(pj.read_text(encoding="utf-8"))
            data.update(overrides)
            pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _set_state(proj: Path, state: str) -> None:
    pj = proj / ".state" / "project.json"
    data = json.loads(pj.read_text(encoding="utf-8"))
    data["state"] = state
    pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _complete_phase2(proj: Path) -> None:
    """Mark every phase-2 step complete (leaving state=execute) to induce the
    execute_state_not_advanced workflow-drift signal."""
    sp = proj / ".state" / "steps.json"
    steps = json.loads(sp.read_text(encoding="utf-8"))
    for s in steps:
        if s["phase"] == 2:
            s["status"] = "complete"
            s.setdefault("commit", "abc1234")
    sp.write_text(json.dumps(steps, indent=2) + "\n", encoding="utf-8")


def _write_backlog(proj: Path, records) -> None:
    (proj / ".state" / "followups.json").write_text(
        json.dumps(records), encoding="utf-8"
    )


class _Counter:
    """Fake run_iteration_fn(proj, backend) -> rc; records the backends seen."""

    def __init__(self, rc: int = 0):
        self.n = 0
        self.rc = rc
        self.backends: list[str | None] = []

    def __call__(self, proj=None, backend=None) -> int:
        self.n += 1
        self.backends.append(backend)
        return self.rc


class _Progress:
    """Fake progress callback; records each heartbeat line."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        self.lines.append(text)


class TestHelpAndUnknown(unittest.TestCase):
    def test_commands_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = tc.dispatch("commands", [], is_admin=False, root=Path(tmp))
            self.assertIn("i2c bot commands", r.text)

    def test_start_aliases_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = tc.dispatch("start", [], is_admin=False, root=Path(tmp))
            self.assertIn("i2c bot commands", r.text)

    def test_unknown_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = tc.dispatch("frobnicate", [], is_admin=True, root=Path(tmp))
            self.assertFalse(r.ok)
            self.assertIn("Unknown command", r.text)

    def test_dropped_commands_are_unknown(self):
        # /status, /next, /projects, /use, /clearboundary no longer exist.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            for gone in ("status", "next", "projects", "use", "clearboundary"):
                r = tc.dispatch(gone, [], is_admin=True, root=root)
                self.assertFalse(r.ok, msg=gone)
                self.assertIn("Unknown command", r.text)


class TestPortfolioAndSetdir(unittest.TestCase):
    def test_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("portfolio", [], is_admin=False, root=root)
            self.assertIn("2 project(s)", r.text)

    def test_setdir_sets_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("setdir", ["b"], is_admin=False, root=root)
            self.assertEqual(r.set_current, "b")

    def test_setdir_unknown_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}})
            r = tc.dispatch("setdir", ["nope"], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIsNone(r.set_current)


class TestAudit(unittest.TestCase):
    def test_summary_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"alpha": {}, "beta": {}})
            r = tc.dispatch("audit", ["alpha"], is_admin=False, root=root)
            self.assertIn("State:", r.text)
            self.assertIn("event_store", r.text)

    def test_summary_sole_project_no_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("audit", [], is_admin=False, root=root)
            self.assertIn("State:", r.text)

    def test_summary_current_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("audit", [], is_admin=False, root=root, current="b")
            self.assertIn("State:", r.text)

    def test_ambiguous_project_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("audit", [], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIn("Specify a project", r.text)

    def test_phase_facet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self.assertFalse(
                tc.dispatch("audit", ["phase"], is_admin=False, root=root).ok
            )
            r = tc.dispatch("audit", ["phase", "2"], is_admin=False, root=root)
            self.assertIn("Phase 2 Summary", r.text)

    def test_decisions_and_devlog_facets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self.assertIn(
                "Append-only",
                tc.dispatch("audit", ["devlog", "2"], is_admin=False, root=root).text,
            )
            self.assertIn(
                "D-1",
                tc.dispatch("audit", ["decisions"], is_admin=False, root=root).text,
            )

    def test_logs_facet_index_and_transcript(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            log_dir = root / "only" / "logs" / "loop"
            log_dir.mkdir(parents=True)
            (log_dir / "summary.log").write_text(
                '2026-06-25T04:03:35+00:00 | iter=1 | backend=claude | '
                'action=EXECUTE | exit=0 | reason="done"\n',
                encoding="utf-8",
            )
            (log_dir / "iteration_001.txt").write_text("body", encoding="utf-8")
            self.assertIn(
                "iter 1",
                tc.dispatch("audit", ["logs"], is_admin=False, root=root).text,
            )
            r = tc.dispatch("audit", ["logs", "iter", "1"], is_admin=False, root=root)
            self.assertIn("body", r.text)

    def test_unknown_facet_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("audit", ["bogus"], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIn("Unknown /audit facet", r.text)

    def test_fu_facet_defaults_to_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _write_backlog(root / "only", [
                {"id": "FU-1", "title": "open one", "kind": "prose", "status": "open"},
                {"id": "FU-2", "title": "done", "kind": "other",
                 "status": "closed", "resolution": "x"},
            ])
            r = tc.dispatch("audit", ["fu"], is_admin=False, root=root)
            self.assertTrue(r.ok)
            self.assertIn("FU-1", r.text)
            self.assertNotIn("FU-2", r.text)  # closed excluded by default

    def test_fu_facet_all_shows_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _write_backlog(root / "only", [
                {"id": "FU-1", "title": "open one", "kind": "prose", "status": "open"},
                {"id": "FU-2", "title": "done", "kind": "other",
                 "status": "closed", "resolution": "x"},
            ])
            r = tc.dispatch("audit", ["fu", "all"], is_admin=False, root=root)
            self.assertIn("FU-1", r.text)
            self.assertIn("FU-2", r.text)

    def test_fu_facet_status_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _write_backlog(root / "only", [
                {"id": "FU-1", "title": "open one", "kind": "prose", "status": "open"},
                {"id": "FU-2", "title": "done", "kind": "other",
                 "status": "closed", "resolution": "x"},
            ])
            r = tc.dispatch("audit", ["fu", "closed"], is_admin=False, root=root)
            self.assertIn("FU-2", r.text)
            self.assertNotIn("FU-1", r.text)

    def test_fu_facet_kind_filter_spans_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _write_backlog(root / "only", [
                {"id": "FU-1", "title": "p", "kind": "prose", "status": "open"},
                {"id": "FU-2", "title": "d", "kind": "prose",
                 "status": "closed", "resolution": "x"},
                {"id": "FU-3", "title": "o", "kind": "other", "status": "open"},
            ])
            r = tc.dispatch("audit", ["fu", "prose"], is_admin=False, root=root)
            self.assertIn("FU-1", r.text)
            self.assertIn("FU-2", r.text)  # kind filter is status-agnostic
            self.assertNotIn("FU-3", r.text)

    def test_fu_facet_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("audit", ["fu"], is_admin=False, root=root)
            self.assertTrue(r.ok)
            self.assertIn("no follow-ups", r.text)


class TestAuth(unittest.TestCase):
    def test_mutating_refused_for_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            for cmd in (
                "run", "batch", "reconcile", "endphase", "refreeze",
            ):
                r = tc.dispatch(cmd, [], is_admin=False, root=root)
                self.assertFalse(r.ok)
                self.assertIn("requires admin", r.text)


class TestDiagnose(unittest.TestCase):
    def test_diagnose_read_only_for_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("diagnose", [], is_admin=False, root=root)
            self.assertTrue(r.ok)
            self.assertIn("Classification: none", r.text)

    def test_diagnose_reports_workflow_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _complete_phase2(root / "only")
            r = tc.dispatch("diagnose", [], is_admin=False, root=root)
            self.assertIn("Classification: workflow-drift", r.text)

    def test_diagnose_missing_target_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("diagnose", ["99"], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIn("Error", r.text)


class TestReconcile(unittest.TestCase):
    def test_reconcile_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _complete_phase2(root / "only")
            r = tc.dispatch("reconcile", [], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("DRY-RUN", r.text)
            self.assertEqual(
                tc.control.status(root / "only").state, "execute"  # unchanged
            )

    def test_reconcile_apply_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            _complete_phase2(root / "only")
            r = tc.dispatch("reconcile", ["apply"], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("RECONCILED", r.text)
            self.assertEqual(tc.control.status(root / "only").state, "review")


class TestRefreeze(unittest.TestCase):
    @staticmethod
    def _freeze_then_edit(proj: Path, phase: int) -> None:
        """Freeze a phase's acceptance suite, then edit it so the live digest
        drifts from the frozen marker (the D-tests-4 situation /refreeze fixes)."""
        from i2c import invariants as inv
        d = proj / "tests" / "acceptance" / f"phase_{phase}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "a.py").write_text("assert 1\n", encoding="utf-8")
        inv.record_tests_suite(
            proj, phase, tests_commit="abc",
            digest=inv.compute_acceptance_digest(proj, phase),
        )
        (d / "a.py").write_text("assert 2\n", encoding="utf-8")  # human fix

    def test_usage_when_phase_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("refreeze", ["apply", "no-phase"], is_admin=True, root=root)
            self.assertFalse(r.ok)
            self.assertIn("Usage:", r.text)

    def test_apply_with_default_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self._freeze_then_edit(root / "only", 2)
            r = tc.dispatch("refreeze", ["2", "apply"], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("REFROZE", r.text)
            from i2c import invariants as inv
            failures = [
                f for f in inv.check_post_action(root / "only", "close")
                if "acceptance suite" in f
            ]
            self.assertEqual(failures, [])  # drift cleared with no reason given
            devlog = (root / "only" / ".state" / "devlog.jsonl").read_text("utf-8")
            self.assertIn("no reason given", devlog)

    def test_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self._freeze_then_edit(root / "only", 2)
            r = tc.dispatch(
                "refreeze", ["2", "authorized", "fix"], is_admin=True, root=root
            )
            self.assertTrue(r.ok)
            self.assertIn("DRY-RUN", r.text)
            from i2c import invariants as inv
            failures = [
                f for f in inv.check_post_action(root / "only", "close")
                if "acceptance suite" in f
            ]
            self.assertTrue(failures)  # still drifted: dry-run wrote nothing

    def test_apply_refreezes_and_audits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self._freeze_then_edit(root / "only", 2)
            r = tc.dispatch(
                "refreeze",
                ["2", "apply", "D-1", "authorized", "fix"],
                is_admin=True, root=root,
            )
            self.assertTrue(r.ok)
            self.assertIn("REFROZE", r.text)
            from i2c import invariants as inv
            failures = [
                f for f in inv.check_post_action(root / "only", "close")
                if "acceptance suite" in f
            ]
            self.assertEqual(failures, [])  # drift cleared
            devlog = (root / "only" / ".state" / "devlog.jsonl").read_text("utf-8")
            self.assertIn("D-1 authorized fix", devlog)

    def test_no_suite_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch(
                "refreeze", ["7", "no", "suite"], is_admin=True, root=root
            )
            self.assertFalse(r.ok)
            self.assertIn("Error", r.text)


class TestRun(unittest.TestCase):
    def test_run_default_one_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            fake = _Counter(rc=0)
            r = tc.dispatch("run", [], is_admin=True, root=root, run_iteration_fn=fake)
            self.assertEqual(fake.n, 1)
            self.assertEqual(fake.backends, [None])  # no override → per-action map
            self.assertIn("exit=0", r.text)

    def test_run_count_and_backend_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})  # execute; fake doesn't advance → N caps it
            fake = _Counter(rc=0)
            tc.dispatch(
                "run", ["2", "codex"], is_admin=True, root=root, run_iteration_fn=fake
            )
            self.assertEqual(fake.n, 2)
            self.assertEqual(fake.backends, ["codex", "codex"])

    def test_run_stops_on_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            fake = _Counter(rc=2)
            r = tc.dispatch(
                "run", ["3"], is_admin=True, root=root, run_iteration_fn=fake
            )
            self.assertEqual(fake.n, 1)  # first non-zero halts the series
            self.assertFalse(r.ok)


class TestBatch(unittest.TestCase):
    def test_batch_runs_to_halt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})  # execute, pending steps
            proj = root / "only"
            seen: list[str | None] = []

            def fake(p, backend=None):
                seen.append(backend)
                if len(seen) >= 2:  # advance to a halt after 2 iterations
                    _set_state(p, "audit_boundary")
                return 0

            r = tc.dispatch("batch", [], is_admin=True, root=root, run_iteration_fn=fake)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen, [None, None])  # per-action map (no override)
            self.assertIn("audit_boundary", r.text)

    def test_batch_stops_at_halt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {"state": "audit_boundary"}})
            fake = _Counter(rc=0)
            r = tc.dispatch("batch", [], is_admin=True, root=root, run_iteration_fn=fake)
            self.assertEqual(fake.n, 0)  # already halted → nothing runs
            self.assertIn("0 iteration", r.text)


class TestProgressAndEnumeration(unittest.TestCase):
    def test_run_enumerates_without_full_and_no_heartbeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            fake = _Counter(rc=0)
            prog = _Progress()
            r = tc.dispatch(
                "run", ["2"], is_admin=True, root=root,
                run_iteration_fn=fake, progress=prog,
            )
            self.assertEqual(fake.n, 2)
            self.assertEqual(prog.lines, [])  # no heartbeat unless --full
            # concluding message enumerates the iterations
            self.assertIn("1. ", r.text)
            self.assertIn("2. ", r.text)

    def test_run_full_emits_one_heartbeat_per_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            fake = _Counter(rc=0)
            prog = _Progress()
            r = tc.dispatch(
                "run", ["2", "--full"], is_admin=True, root=root,
                run_iteration_fn=fake, progress=prog,
            )
            self.assertEqual(fake.n, 2)
            self.assertEqual(len(prog.lines), 2)
            self.assertTrue(all(ln.startswith("[") for ln in prog.lines))
            # --full streamed the steps live, so the closing does NOT re-enumerate
            self.assertNotIn("1. ", r.text)
            self.assertIn("Ran 2/2", r.text)

    def test_batch_full_emits_heartbeat_per_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            seen: list[str | None] = []
            prog = _Progress()

            def fake(p, backend=None):
                seen.append(backend)
                if len(seen) >= 2:
                    _set_state(p, "audit_boundary")
                return 0

            r = tc.dispatch(
                "batch", ["full"], is_admin=True, root=root,
                run_iteration_fn=fake, progress=prog,
            )
            self.assertEqual(len(seen), 2)
            self.assertEqual(len(prog.lines), 2)
            self.assertIn("audit_boundary", r.text)
            # --full streamed the steps live, so the closing does NOT re-enumerate
            self.assertNotIn("1. ", r.text)


class TestEndphase(unittest.TestCase):
    def test_endphase_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {"state": "audit_boundary"}})
            r = tc.dispatch("endphase", [], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("advanced", r.text)

    def test_endphase_last_terminates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {"state": "audit_boundary"}})
            r = tc.dispatch("endphase", ["last"], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("terminated", r.text)


class TestCommandMenu(unittest.TestCase):
    def test_menu_entries_are_valid(self):
        self.assertTrue(tc.COMMAND_MENU)
        for name, desc in tc.COMMAND_MENU:
            self.assertIn(name, tc.ALL_COMMANDS, msg=name)
            # Telegram command rules: lowercase, 1-32 chars, [a-z0-9_].
            self.assertTrue(name.islower() and name.isidentifier(), msg=name)
            self.assertLessEqual(len(name), 32, msg=name)
            self.assertTrue(0 < len(desc) <= 256, msg=name)


if __name__ == "__main__":
    unittest.main()

"""Tests for i2c.surfaces.telegram_core — the transport-agnostic dispatch core.

No python-telegram-bot dependency is touched here (the core must be importable
and testable without the `telegram` extra).
"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import control as c  # noqa: E402
from i2c.surfaces import telegram_core as tc  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


def _make(root: Path, specs: dict) -> None:
    for name, overrides in specs.items():
        dst = root / name
        shutil.copytree(FIXTURE, dst)
        if overrides:
            pj = dst / ".state" / "project.json"
            data = json.loads(pj.read_text(encoding="utf-8"))
            data.update(overrides)
            pj.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class _Counter:
    def __init__(self, rc: int = 0):
        self.n = 0
        self.rc = rc

    def __call__(self, proj=None) -> int:
        self.n += 1
        return self.rc


class TestHelpAndUnknown(unittest.TestCase):
    def test_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = tc.dispatch("help", [], is_admin=False, root=Path(tmp))
            self.assertIn("i2c bot commands", r.text)

    def test_unknown_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = tc.dispatch("frobnicate", [], is_admin=True, root=Path(tmp))
            self.assertFalse(r.ok)
            self.assertIn("Unknown command", r.text)


class TestReadCommands(unittest.TestCase):
    def test_portfolio(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("portfolio", [], is_admin=False, root=root)
            self.assertIn("2 project(s)", r.text)

    def test_projects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("projects", [], is_admin=False, root=root)
            self.assertIn("a", r.text)
            self.assertIn("b", r.text)

    def test_status_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"alpha": {}, "beta": {}})
            r = tc.dispatch("status", ["alpha"], is_admin=False, root=root)
            self.assertIn("State:", r.text)
            self.assertIn("event_store", r.text)

    def test_status_sole_project_no_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            r = tc.dispatch("status", [], is_admin=False, root=root)
            self.assertIn("State:", r.text)

    def test_status_current_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("status", [], is_admin=False, root=root, current="b")
            self.assertIn("State:", r.text)

    def test_ambiguous_project_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("status", [], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIn("Specify a project", r.text)

    def test_phasesummary_requires_phase(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self.assertFalse(
                tc.dispatch("phasesummary", [], is_admin=False, root=root).ok
            )
            r = tc.dispatch("phasesummary", ["2"], is_admin=False, root=root)
            self.assertIn("Phase 2 Summary", r.text)

    def test_devlog_and_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            self.assertIn(
                "Append-only",
                tc.dispatch("devlog", ["2"], is_admin=False, root=root).text,
            )
            self.assertIn(
                "D-1",
                tc.dispatch("decisions", [], is_admin=False, root=root).text,
            )

    def test_logs_index_and_transcript(self):
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
                "iter 1", tc.dispatch("logs", [], is_admin=False, root=root).text
            )
            r = tc.dispatch("logs", ["iter", "1"], is_admin=False, root=root)
            self.assertIn("body", r.text)


class TestUse(unittest.TestCase):
    def test_use_sets_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}, "b": {}})
            r = tc.dispatch("use", ["b"], is_admin=False, root=root)
            self.assertEqual(r.set_current, "b")

    def test_use_unknown_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"a": {}})
            r = tc.dispatch("use", ["nope"], is_admin=False, root=root)
            self.assertFalse(r.ok)
            self.assertIsNone(r.set_current)


class TestAuth(unittest.TestCase):
    def test_mutating_refused_for_non_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            for cmd in ("run", "batch", "clearboundary"):
                r = tc.dispatch(cmd, [], is_admin=False, root=root)
                self.assertFalse(r.ok)
                self.assertIn("requires admin", r.text)


class TestMutating(unittest.TestCase):
    def test_run_invokes_runner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})
            fake = _Counter(rc=0)
            r = tc.dispatch(
                "run", [], is_admin=True, root=root, run_iteration_fn=fake
            )
            self.assertEqual(fake.n, 1)
            self.assertIn("exit=0", r.text)

    def test_batch_runs_up_to_n(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {}})  # execute state, pending steps
            fake = _Counter(rc=0)
            r = tc.dispatch(
                "batch", ["3"], is_admin=True, root=root, run_iteration_fn=fake
            )
            # Fake doesn't advance state, so all 3 run.
            self.assertEqual(fake.n, 3)
            self.assertIn("3/3", r.text)

    def test_batch_stops_at_halt_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {"state": "audit_boundary"}})
            fake = _Counter(rc=0)
            r = tc.dispatch(
                "batch", ["3"], is_admin=True, root=root, run_iteration_fn=fake
            )
            self.assertEqual(fake.n, 0)  # already halted → nothing runs
            self.assertIn("0/3", r.text)

    def test_clearboundary_advances(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make(root, {"only": {"state": "audit_boundary"}})
            r = tc.dispatch("clearboundary", [], is_admin=True, root=root)
            self.assertTrue(r.ok)
            self.assertIn("advanced", r.text)


if __name__ == "__main__":
    unittest.main()

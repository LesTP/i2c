"""Tests for tools/control.py — the i2c.control command API."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import control as c  # noqa: E402
from i2c import validate as v  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


class TempProject:
    """Copy the fixture into a temp dir so write tests don't mutate it."""

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_control_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        return self

    def __exit__(self, *args):
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
# status()
# ---------------------------------------------------------------------------


class TestStatus(unittest.TestCase):
    def test_status_reports_phase_state_module_regime(self):
        report = c.status(FIXTURE)
        self.assertEqual(report.phase, 2)
        self.assertEqual(report.state, "execute")
        self.assertEqual(report.module, "event_store")
        self.assertEqual(report.regime, "build")
        self.assertEqual(report.dependencies, [])
        self.assertEqual(report.budget, {"steps_remaining": 3})

    def test_status_current_phase_steps(self):
        report = c.status(FIXTURE)
        # Phase 2 has steps 1..4, in step order.
        self.assertEqual([s.step for s in report.steps], [1, 2, 3, 4])
        self.assertEqual(report.steps[0].title, "Append-only writer")
        self.assertEqual(report.steps[0].status, "complete")
        self.assertEqual(report.steps[0].commit, "1234567")
        self.assertEqual(report.steps[1].status, "pending")
        self.assertIsNone(report.steps[1].commit)

    def test_status_gotchas(self):
        report = c.status(FIXTURE)
        self.assertEqual(len(report.gotchas), 1)
        self.assertIn("supervised", report.gotchas[0])

    def test_status_open_decisions(self):
        report = c.status(FIXTURE)
        self.assertEqual([d.id for d in report.open_decisions], ["D-2"])
        self.assertEqual(report.open_decisions[0].status, "open")

    def test_status_recent_activity_last_three_newest_first(self):
        report = c.status(FIXTURE)
        self.assertEqual(len(report.recent_activity), 3)
        # Newest entry (phase 2, step 1) first; phase-level close has step None.
        self.assertEqual(report.recent_activity[0].phase, 2)
        self.assertEqual(report.recent_activity[0].step, 1)
        self.assertEqual(report.recent_activity[0].action, "execute")
        self.assertIsNone(report.recent_activity[1].step)


# ---------------------------------------------------------------------------
# next_action()
# ---------------------------------------------------------------------------


class TestNextAction(unittest.TestCase):
    def test_next_action_execute(self):
        dispatch = c.next_action(FIXTURE)
        self.assertEqual(dispatch.action, "EXECUTE")
        self.assertEqual(dispatch.next_state, "execute")

    def test_invalid_state_raises_control_error(self):
        with TempProject() as p:
            (p.root / ".state" / "project.json").write_text(
                '{"phase": 2, "state": "bogus"}', encoding="utf-8"
            )
            with self.assertRaises(c.ControlError):
                c.next_action(p.root)


# ---------------------------------------------------------------------------
# phase_summary()
# ---------------------------------------------------------------------------


class TestPhaseSummary(unittest.TestCase):
    def test_phase_summary_header_and_steps(self):
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual(summary.phase, 2)
        self.assertEqual(summary.module, "event_store")
        self.assertEqual(summary.regime, "build")
        self.assertEqual(summary.title, "Core storage")
        self.assertEqual(summary.status, "pending")
        self.assertEqual([s.step for s in summary.steps], [1, 2, 3, 4])

    def test_phase_summary_devlog_filtered(self):
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual([e.phase for e in summary.devlog], [2])
        self.assertEqual(summary.devlog[0].summary[:11], "Append-only")

    def test_phase_summary_decisions_untagged_excluded(self):
        # Fixture decisions lack the optional `phase` field, so none are
        # attributed to phase 2.
        summary = c.phase_summary(FIXTURE, phase=2)
        self.assertEqual(summary.decisions, [])
        self.assertEqual(summary.open_items, [])

    def test_phase_summary_decisions_tagged(self):
        with TempProject() as p:
            path = p.root / ".state" / "decisions.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data[1]["phase"] = 2  # D-2 (open) now tagged to phase 2
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            summary = c.phase_summary(p.root, phase=2)
            self.assertEqual([d.id for d in summary.decisions], ["D-2"])
            self.assertEqual([d.id for d in summary.open_items], ["D-2"])


# ---------------------------------------------------------------------------
# decisions()
# ---------------------------------------------------------------------------


class TestDecisions(unittest.TestCase):
    def test_decisions_all(self):
        result = c.decisions(FIXTURE)
        self.assertEqual([d.id for d in result], ["D-1", "D-2"])

    def test_decisions_filter_by_phase(self):
        result = c.decisions(FIXTURE, phase=2)
        self.assertEqual(result, [])  # none tagged in the fixture

    def test_decisions_filter_matches_tagged(self):
        with TempProject() as p:
            path = p.root / ".state" / "decisions.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data[0]["phase"] = 1
            data[1]["phase"] = 2
            path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            self.assertEqual([d.id for d in c.decisions(p.root, phase=1)], ["D-1"])
            self.assertEqual([d.id for d in c.decisions(p.root, phase=2)], ["D-2"])


# ---------------------------------------------------------------------------
# clear_boundary()
# ---------------------------------------------------------------------------


class TestClearBoundary(unittest.TestCase):
    def test_advance_writes_next_phase_plan(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            result = c.clear_boundary(p.root, advance=True)
            self.assertEqual(result.outcome, "advanced")
            self.assertEqual(result.phase, 3)
            self.assertEqual(result.state, "plan")
            on_disk = p.read_project()
            self.assertEqual(on_disk["phase"], 3)
            self.assertEqual(on_disk["state"], "plan")

    def test_terminate_writes_done(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            result = c.clear_boundary(p.root, advance=False)
            self.assertEqual(result.outcome, "terminated")
            self.assertEqual(result.phase, 2)
            self.assertEqual(result.state, "done")
            on_disk = p.read_project()
            self.assertEqual(on_disk["phase"], 2)
            self.assertEqual(on_disk["state"], "done")

    def test_non_boundary_raises_invalid_state(self):
        with TempProject() as p:
            # Fixture is state=execute.
            with self.assertRaises(c.InvalidStateError):
                c.clear_boundary(p.root, advance=True)

    def test_result_revalidates_against_schema(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            c.clear_boundary(p.root, advance=True)
            # Should not raise — the written file is schema-valid.
            v.validate_state_file(p.root / ".state" / "project.json")


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrors(unittest.TestCase):
    def test_missing_state_raises_control_error_not_systemexit(self):
        with tempfile.TemporaryDirectory(prefix="i2c_control_empty_") as tmp:
            root = Path(tmp)  # no .state/ here
            with self.assertRaises(c.ControlError):
                c.status(root)

    def test_find_project_root_raises_not_found(self):
        with tempfile.TemporaryDirectory(prefix="i2c_control_noroot_") as tmp:
            with self.assertRaises(c.NotFoundError):
                c.find_project_root(Path(tmp))


if __name__ == "__main__":
    unittest.main()

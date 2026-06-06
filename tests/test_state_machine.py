"""Tests for tools/state_machine.py — dispatch decision matrix.

Read-only; exercises every cell of the matrix plus STOP_BEFORE_REVIEW and
the blocked short-circuit. Subprocess-level test verifies the script
walks up from a sub-directory CWD via find_project_root.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# Make tools/ importable.
I2C_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = I2C_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import state_machine as sm  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TempProject:
    """Temp project directory copied from the canonical .state fixture.

    Mutators (``set_state``, ``set_blocked``, ``set_steps``) edit JSON in
    place — no schema gymnastics, just enough to walk the matrix.
    """

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_sm_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        self._prev_cwd = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    # --- mutators -----------------------------------------------------------

    def _read(self, name: str) -> dict | list:
        return json.loads((self.root / ".state" / name).read_text(encoding="utf-8"))

    def _write(self, name: str, data) -> None:
        (self.root / ".state" / name).write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8",
        )

    def set_project(self, **kwargs) -> None:
        data = self._read("project.json")
        data.update(kwargs)
        self._write("project.json", data)

    def set_steps(self, steps: list[dict]) -> None:
        self._write("steps.json", steps)


def run_main(env: dict | None = None) -> tuple[int, str, str]:
    """Run state_machine.main() with patched environment; capture i/o."""
    out, err = io.StringIO(), io.StringIO()
    saved_env = os.environ.copy()
    try:
        if env is not None:
            for k, v in env.items():
                os.environ[k] = v
        # Clear cached env-derived values per call.
        with redirect_stdout(out), redirect_stderr(err):
            try:
                rc = sm.main([])
                if rc is None:
                    rc = 0
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 2
        return rc, out.getvalue(), err.getvalue()
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


def parse_output(stdout: str) -> tuple[str, str]:
    """Extract ACTION and NEXT from stdout. Raises if missing."""
    action = next_state = None
    for line in stdout.splitlines():
        if line.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
        elif line.startswith("NEXT:"):
            next_state = line.split(":", 1)[1].strip()
    if action is None or next_state is None:
        raise AssertionError(f"Missing ACTION/NEXT in stdout:\n{stdout}")
    return action, next_state


# ---------------------------------------------------------------------------
# Pure function tests — every cell of the matrix
# ---------------------------------------------------------------------------


class TestDecideMatrix(unittest.TestCase):
    """Pure tests against sm.decide() — no file I/O."""

    def _project(self, *, state: str, blocked: bool = False, phase: int = 1):
        return {"phase": phase, "state": state, "blocked": blocked}

    def _steps(self, phase: int, pending: int, total: int | None = None):
        """Synthesize steps.json with `pending` pending + remainder complete."""
        total = total if total is not None else pending
        out = []
        for i in range(total):
            status = "pending" if i < pending else "complete"
            rec = {"phase": phase, "step": i + 1, "title": f"s{i+1}", "status": status}
            if status == "complete":
                rec["commit"] = "abc1234"
            out.append(rec)
        return out

    # ---- blocked short-circuit --------------------------------------------

    def test_blocked_exits_with_current_state(self):
        proj = self._project(state="execute", blocked=True)
        action, nxt = sm.decide(proj, self._steps(1, 1))
        self.assertEqual(action, "EXIT")
        self.assertEqual(nxt, "execute")

    def test_blocked_exits_even_in_close(self):
        proj = self._project(state="close", blocked=True)
        action, nxt = sm.decide(proj, [])
        self.assertEqual((action, nxt), ("EXIT", "close"))

    # ---- plan -------------------------------------------------------------

    def test_plan_dispatches_plan(self):
        proj = self._project(state="plan")
        action, nxt = sm.decide(proj, [])
        self.assertEqual((action, nxt), ("PLAN", "execute"))

    # ---- execute: >1 pending → loop --------------------------------------

    def test_execute_with_many_pending_loops(self):
        proj = self._project(state="execute", phase=1)
        steps = self._steps(1, pending=3, total=4)
        action, nxt = sm.decide(proj, steps)
        self.assertEqual((action, nxt), ("EXECUTE", "execute"))

    # ---- execute: ==1 pending → transition to review ---------------------

    def test_execute_with_one_pending_transitions_to_review(self):
        proj = self._project(state="execute", phase=2)
        steps = self._steps(2, pending=1, total=4)
        action, nxt = sm.decide(proj, steps)
        self.assertEqual((action, nxt), ("EXECUTE", "review"))

    # ---- execute: 0 pending → REVIEW dispatch ----------------------------

    def test_execute_with_no_pending_dispatches_review(self):
        proj = self._project(state="execute", phase=2)
        steps = self._steps(2, pending=0, total=3)
        action, nxt = sm.decide(proj, steps)
        self.assertEqual((action, nxt), ("REVIEW", "close"))

    # ---- review -----------------------------------------------------------

    def test_review_dispatches_review(self):
        proj = self._project(state="review")
        action, nxt = sm.decide(proj, [])
        self.assertEqual((action, nxt), ("REVIEW", "close"))

    # ---- close ------------------------------------------------------------

    def test_close_dispatches_close(self):
        proj = self._project(state="close")
        action, nxt = sm.decide(proj, [])
        self.assertEqual((action, nxt), ("CLOSE", "plan"))

    # ---- STOP_BEFORE_REVIEW -----------------------------------------------

    def test_stop_before_review_short_circuits_review_state(self):
        proj = self._project(state="review")
        action, nxt = sm.decide(proj, [], stop_before_review=True)
        self.assertEqual((action, nxt), ("EXIT", "review"))

    def test_stop_before_review_short_circuits_execute_to_review(self):
        proj = self._project(state="execute", phase=2)
        steps = self._steps(2, pending=0, total=3)
        action, nxt = sm.decide(proj, steps, stop_before_review=True)
        self.assertEqual((action, nxt), ("EXIT", "review"))

    def test_stop_before_review_leaves_execute_alone_when_pending(self):
        # Pending > 0 → still dispatches EXECUTE; only REVIEW dispatches
        # are short-circuited.
        proj = self._project(state="execute", phase=2)
        steps = self._steps(2, pending=2, total=3)
        action, nxt = sm.decide(proj, steps, stop_before_review=True)
        self.assertEqual((action, nxt), ("EXECUTE", "execute"))

    # ---- unknown state ----------------------------------------------------

    def test_unknown_state_raises(self):
        proj = self._project(state="bogus")
        with self.assertRaises(ValueError):
            sm.decide(proj, [])

    # ---- pending-count helper --------------------------------------------

    def test_count_pending_steps_ignores_other_phases(self):
        steps = [
            {"phase": 1, "step": 1, "status": "pending", "title": "x"},
            {"phase": 2, "step": 1, "status": "pending", "title": "y"},
            {"phase": 2, "step": 2, "status": "in_progress", "title": "z"},
            {"phase": 2, "step": 3, "status": "complete", "title": "q", "commit": "abc1234"},
        ]
        self.assertEqual(sm.count_pending_steps(steps, 2), 1)
        self.assertEqual(sm.count_pending_steps(steps, 1), 1)
        self.assertEqual(sm.count_pending_steps(steps, 3), 0)


# ---------------------------------------------------------------------------
# CLI tests — file I/O via temp project
# ---------------------------------------------------------------------------


class TestStateMachineCli(unittest.TestCase):
    """End-to-end CLI tests: temp project → main() → parsed stdout."""

    def test_fixture_baseline_dispatches_execute(self):
        # Fixture: phase=2 execute, steps 2.1 complete, 2.2 in_progress,
        # 2.3 pending, 2.4 pending. Pending count for phase 2 = 2 → EXECUTE/execute.
        with TempProject():
            rc, out, err = run_main()
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(parse_output(out), ("EXECUTE", "execute"))

    def test_plan_state_dispatches_plan(self):
        with TempProject() as p:
            p.set_project(state="plan")
            rc, out, err = run_main()
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(parse_output(out), ("PLAN", "execute"))

    def test_close_state_dispatches_close(self):
        with TempProject() as p:
            p.set_project(state="close")
            rc, out, err = run_main()
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(parse_output(out), ("CLOSE", "plan"))

    def test_blocked_dispatches_exit(self):
        with TempProject() as p:
            p.set_project(blocked=True, state="close")
            rc, out, err = run_main()
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(parse_output(out), ("EXIT", "close"))

    def test_stop_before_review_env(self):
        # Mark all phase 2 steps complete so EXECUTE state with 0 pending
        # would dispatch REVIEW; STOP_BEFORE_REVIEW=true → EXIT/review.
        with TempProject() as p:
            steps = json.loads((p.root / ".state" / "steps.json").read_text())
            for s in steps:
                if s["phase"] == 2:
                    s["status"] = "complete"
                    s.setdefault("commit", "abc1234")
            p.set_steps(steps)
            rc, out, err = run_main(env={"STOP_BEFORE_REVIEW": "true"})
            self.assertEqual(rc, 0, msg=err)
            self.assertEqual(parse_output(out), ("EXIT", "review"))

    def test_step_budget_env_does_not_perturb_decision(self):
        # v1: STEP_BUDGET is forward-compat only; same inputs same decision
        # regardless of budget.
        with TempProject() as p:
            p.set_project(state="plan")
            rc_a, out_a, _ = run_main(env={"STEP_BUDGET": "1"})
            rc_b, out_b, _ = run_main(env={"STEP_BUDGET": "10"})
            self.assertEqual(rc_a, 0)
            self.assertEqual(rc_b, 0)
            self.assertEqual(parse_output(out_a), parse_output(out_b))

    def test_walks_up_from_subdirectory(self):
        """find_project_root walks up; CWD inside .state/ still finds project."""
        with TempProject() as p:
            subdir = p.root / "src" / "deep"
            subdir.mkdir(parents=True)
            os.chdir(subdir)
            rc, out, err = run_main()
            self.assertEqual(rc, 0, msg=err)
            # Same dispatch as the baseline run.
            self.assertEqual(parse_output(out), ("EXECUTE", "execute"))

    def test_schema_invalid_state_file_exits_2(self):
        with TempProject() as p:
            (p.root / ".state" / "project.json").write_text(
                '{"phase": 1, "state": "bogus", "blocked": false}',
                encoding="utf-8",
            )
            rc, _, err = run_main()
            self.assertEqual(rc, 2)
            self.assertIn("schema-invalid", err)


if __name__ == "__main__":
    unittest.main()

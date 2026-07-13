"""Tests for tools/invariants.py — post-action invariant checks (FU-22)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import invariants as inv
from tests._fixtures import copy_fixture

FIXTURE = I2C_ROOT / "examples" / "initial_state"


class TempProject:
    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_inv_")
        self.root = Path(self._tmp.name) / "project"
        copy_fixture(self.root)
        self._prev_cwd = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def patch_project(self, **kwargs) -> None:
        path = self.root / ".state" / "project.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(kwargs)
        # Defensive: remove any legacy 'blocked' field if a test sets one;
        # the schema no longer accepts it (DESIGN_state_lifecycle_v1).
        data.pop("blocked", None)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def patch_phase_status(self, phase_id: int, status: str) -> None:
        path = self.root / ".state" / "phases.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for p in data:
            if p["id"] == phase_id:
                p["status"] = status
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def run_cli(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = inv.main(list(argv))
            if rc is None:
                rc = 0
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Python-API tests (check_post_action)
# ---------------------------------------------------------------------------


class TestCheckPostAction(unittest.TestCase):
    # ---- close ------------------------------------------------------------

    def test_close_passes_when_state_is_audit_boundary_and_phase_complete(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            p.patch_phase_status(2, "complete")
            self.assertEqual(inv.check_post_action(p.root, "close"), [])

    def test_close_fails_when_state_not_audit_boundary(self):
        with TempProject() as p:
            p.patch_project(state="close", phase=2)
            p.patch_phase_status(2, "complete")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'audit_boundary'", failures[0])

    def test_close_fails_when_phase_not_complete(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            p.patch_phase_status(2, "pending")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'complete'", failures[0])

    def test_close_fails_when_both_invariants_violated(self):
        with TempProject() as p:
            p.patch_project(state="close", phase=2)
            p.patch_phase_status(2, "pending")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 2)

    def test_close_fails_when_no_matching_phase_record(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=99)
            failures = inv.check_post_action(p.root, "close")
            self.assertTrue(any("no phases.json record" in f for f in failures))

    # ---- review -----------------------------------------------------------

    def test_review_passes_when_state_is_close(self):
        with TempProject() as p:
            p.patch_project(state="close")
            self.assertEqual(inv.check_post_action(p.root, "review"), [])

    def test_review_passes_when_state_is_audit_escalation(self):
        with TempProject() as p:
            p.patch_project(state="audit_escalation")
            self.assertEqual(inv.check_post_action(p.root, "review"), [])

    def test_review_fails_when_state_not_close_or_escalation(self):
        with TempProject() as p:
            p.patch_project(state="execute")
            failures = inv.check_post_action(p.root, "review")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'close'", failures[0])

    # ---- plan -------------------------------------------------------------

    def test_plan_passes_when_state_is_execute(self):
        with TempProject() as p:
            p.patch_project(state="execute")
            self.assertEqual(inv.check_post_action(p.root, "plan"), [])

    def test_plan_passes_when_state_is_audit_escalation(self):
        with TempProject() as p:
            p.patch_project(state="audit_escalation")
            self.assertEqual(inv.check_post_action(p.root, "plan"), [])

    def test_plan_fails_when_state_not_execute_or_escalation(self):
        with TempProject() as p:
            p.patch_project(state="plan")
            failures = inv.check_post_action(p.root, "plan")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'execute'", failures[0])

    # ---- execute ----------------------------------------------------------

    def test_execute_passes_when_state_is_execute(self):
        with TempProject() as p:
            p.patch_project(state="execute")
            self.assertEqual(inv.check_post_action(p.root, "execute"), [])

    def test_execute_passes_when_state_is_review(self):
        with TempProject() as p:
            p.patch_project(state="review")
            self.assertEqual(inv.check_post_action(p.root, "execute"), [])

    def test_execute_passes_when_state_is_audit_escalation(self):
        with TempProject() as p:
            p.patch_project(state="audit_escalation")
            self.assertEqual(inv.check_post_action(p.root, "execute"), [])

    def test_execute_fails_when_state_is_plan(self):
        with TempProject() as p:
            p.patch_project(state="plan")
            failures = inv.check_post_action(p.root, "execute")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'execute'", failures[0])

    def test_execute_fails_when_state_is_close(self):
        with TempProject() as p:
            p.patch_project(state="close")
            failures = inv.check_post_action(p.root, "execute")
            self.assertEqual(len(failures), 1)

    # ---- bad inputs -------------------------------------------------------

    def test_unknown_action_raises(self):
        with TempProject() as p:
            with self.assertRaises(ValueError):
                inv.check_post_action(p.root, "bogus")

    def test_schema_invalid_state_surfaces_as_failure(self):
        with TempProject() as p:
            (p.root / ".state" / "project.json").write_text(
                '{"phase": 1, "state": "bogus"}',
                encoding="utf-8",
            )
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 1)
            self.assertIn("schema-invalid", failures[0])


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------


class TestInvariantsCli(unittest.TestCase):
    def test_cli_passes_with_OK_message(self):
        with TempProject() as p:
            p.patch_project(state="audit_boundary", phase=2)
            p.patch_phase_status(2, "complete")
            rc, out, err = run_cli("--action", "close")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("OK", out)
            self.assertIn("invariants pass", out)

    def test_cli_fails_with_structured_error(self):
        with TempProject() as p:
            p.patch_project(state="close", phase=2)
            p.patch_phase_status(2, "complete")
            rc, out, err = run_cli("--action", "close")
            self.assertEqual(rc, 1)
            self.assertIn("ERROR", err)
            self.assertIn("Detail:", err)
            self.assertIn("must be 'audit_boundary'", err)

    def test_cli_rejects_unknown_action(self):
        with TempProject():
            rc, out, err = run_cli("--action", "bogus")
            # argparse's choices= → exit 2 on invalid choice
            self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# Refine (sub-phase) invariant — check_post_refine (Proposal B, Q-B2)
# ---------------------------------------------------------------------------


def _devlog_count(root: Path) -> int:
    path = root / ".state" / "devlog.jsonl"
    if not path.is_file():
        return 0
    return len([ln for ln in path.read_text("utf-8").splitlines() if ln.strip()])


def _append_devlog(root: Path, row: dict) -> None:
    with open(root / ".state" / "devlog.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


_REFINE_ROW = {
    "phase": None, "step": None, "action": "refine", "kind": "prose",
    "outcome": "complete", "summary": "did it", "timestamp": "2026-07-13T08:00:00Z",
}


class TestCheckPostRefine(unittest.TestCase):
    def test_unchanged_with_refine_row_passes(self):
        with TempProject() as p:
            pre = inv.snapshot_phase_files(p.root)
            n = _devlog_count(p.root)
            _append_devlog(p.root, dict(_REFINE_ROW))
            failures = inv.check_post_refine(
                p.root, pre_files=pre, pre_devlog_count=n)
            self.assertEqual(failures, [])

    def test_detects_phase_file_change(self):
        with TempProject() as p:
            pre = inv.snapshot_phase_files(p.root)
            n = _devlog_count(p.root)
            _append_devlog(p.root, dict(_REFINE_ROW))
            p.patch_project(state="review")  # a lifecycle write refine must not do
            failures = inv.check_post_refine(
                p.root, pre_files=pre, pre_devlog_count=n)
            self.assertTrue(any("project.json changed" in f for f in failures))

    def test_detects_phases_file_change(self):
        with TempProject() as p:
            pre = inv.snapshot_phase_files(p.root)
            n = _devlog_count(p.root)
            _append_devlog(p.root, dict(_REFINE_ROW))
            p.patch_phase_status(2, "complete")  # touching phases.json is forbidden
            failures = inv.check_post_refine(
                p.root, pre_files=pre, pre_devlog_count=n)
            self.assertTrue(any("phases.json changed" in f for f in failures))

    def test_detects_missing_refine_row(self):
        with TempProject() as p:
            pre = inv.snapshot_phase_files(p.root)
            n = _devlog_count(p.root)
            # A non-refine devlog append does not satisfy the invariant.
            _append_devlog(p.root, {
                "phase": 2, "step": 1, "action": "execute", "outcome": "complete",
                "summary": "x", "timestamp": "2026-07-13T08:00:00Z",
            })
            failures = inv.check_post_refine(
                p.root, pre_files=pre, pre_devlog_count=n)
            self.assertTrue(any("action='refine'" in f for f in failures))


if __name__ == "__main__":
    unittest.main()

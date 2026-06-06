"""Tests for tools/invariants.py — post-action invariant checks (FU-22)."""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = I2C_ROOT / "tools"
sys.path.insert(0, str(TOOLS_DIR))

import invariants as inv  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


class TempProject:
    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_inv_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
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

    def test_close_passes_when_blocked_and_phase_complete(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=True, phase=2)
            p.patch_phase_status(2, "complete")
            self.assertEqual(inv.check_post_action(p.root, "close"), [])

    def test_close_fails_when_not_blocked(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=False, phase=2)
            p.patch_phase_status(2, "complete")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 1)
            self.assertIn("blocked must be true", failures[0])

    def test_close_fails_when_phase_not_complete(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=True, phase=2)
            p.patch_phase_status(2, "in_progress")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 1)
            self.assertIn("must be 'complete'", failures[0])

    def test_close_fails_when_both_invariants_violated(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=False, phase=2)
            p.patch_phase_status(2, "in_progress")
            failures = inv.check_post_action(p.root, "close")
            self.assertEqual(len(failures), 2)

    def test_close_fails_when_no_matching_phase_record(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=True, phase=99)
            failures = inv.check_post_action(p.root, "close")
            self.assertTrue(any("no phases.json record" in f for f in failures))

    # ---- review -----------------------------------------------------------

    def test_review_passes_when_state_is_close(self):
        with TempProject() as p:
            p.patch_project(state="close")
            self.assertEqual(inv.check_post_action(p.root, "review"), [])

    def test_review_fails_when_state_not_close(self):
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

    def test_plan_fails_when_state_not_execute(self):
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
                '{"phase": 1, "state": "bogus", "blocked": true}',
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
            p.patch_project(state="close", blocked=True, phase=2)
            p.patch_phase_status(2, "complete")
            rc, out, err = run_cli("--action", "close")
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("OK", out)
            self.assertIn("invariants pass", out)

    def test_cli_fails_with_structured_error(self):
        with TempProject() as p:
            p.patch_project(state="close", blocked=False, phase=2)
            p.patch_phase_status(2, "complete")
            rc, out, err = run_cli("--action", "close")
            self.assertEqual(rc, 1)
            self.assertIn("ERROR", err)
            self.assertIn("Detail:", err)
            self.assertIn("blocked must be true", err)

    def test_cli_rejects_unknown_action(self):
        with TempProject():
            rc, out, err = run_cli("--action", "bogus")
            # argparse's choices= → exit 2 on invalid choice
            self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

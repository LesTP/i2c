"""Tests for i2c/run_refine.py — the single-shot refine loop (DESIGN_refine_v1 §12).

The backend is mocked via the ``claude_invoker`` seam (like test_run_iteration);
the assembler + control + invariants run for real. Fakes simulate the worker's
side effects (append a refine devlog row, edit a file) so the sub-phase invariant
and the runner-owned commit are exercised end to end.
"""

from __future__ import annotations

import io
import json
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from i2c import control
from i2c import run_refine as rr
from i2c import validate as v
from tests.test_run_iteration import TempProject, signal_block


def write_backlog(root: Path, records) -> None:
    (root / ".state" / "followups.json").write_text(
        json.dumps(records), encoding="utf-8"
    )


def read_backlog(root: Path):
    return json.loads((root / ".state" / "followups.json").read_text("utf-8"))


_OPEN_FU = {"id": "FU-1", "title": "prose pass", "kind": "prose", "status": "open"}


def fake_worker(
    *,
    exit_code: int = 0,
    reason: str = "rewrote the passages",
    append_devlog: bool = True,
    edit_file: bool = True,
    touch_phase: bool = False,
    kind: str = "prose",
):
    """A fake invoke_claude that simulates the refine worker's side effects."""
    def fake(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None):
        root = Path(cwd)
        if edit_file:
            (root / "notes.txt").write_text("refined\n", encoding="utf-8")
        if touch_phase:  # simulate a lifecycle violation
            pj = root / ".state" / "project.json"
            data = json.loads(pj.read_text("utf-8"))
            data["state"] = "review"
            pj.write_text(json.dumps(data), encoding="utf-8")
        if append_devlog:
            row = {
                "phase": None, "step": None, "action": "refine", "kind": kind,
                "outcome": "complete", "summary": reason,
                "timestamp": "2026-07-13T08:00:00Z",
            }
            with open(root / ".state" / "devlog.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        return 0, signal_block(exit_code=exit_code, reason=reason)
    return fake


def run_refine_capture(fu_id, *, backend="claude", invoker):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = rr.run_refine(
                fu_id, backend=backend, model="sonnet", max_budget_usd=5.0,
                claude_invoker=invoker,
            )
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def last_telemetry(root: Path) -> dict:
    lines = [
        ln for ln in (root / ".state" / "telemetry.jsonl").read_text("utf-8").splitlines()
        if ln.strip()
    ]
    return json.loads(lines[-1])


def rate_limited_worker():
    """A fake claude invoker whose output is a 429 backend-error envelope."""
    def fake(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None):
        return 0, json.dumps(
            {"is_error": True, "api_error_status": 429, "result": "usage limit"}
        )
    return fake


class TestRunRefine(unittest.TestCase):
    def test_missing_fu_returns_2(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture("FU-99", invoker=fake_worker())
            self.assertEqual(rc, 2)
            self.assertIn("not found", err)

    def test_closed_fu_returns_2(self):
        with TempProject() as t:
            write_backlog(t.root, [
                {"id": "FU-2", "title": "done", "kind": "other",
                 "status": "closed", "resolution": "x"},
            ])
            rc, out, err = run_refine_capture("FU-2", invoker=fake_worker())
            self.assertEqual(rc, 2)

    def test_happy_path_closes_fu_and_logs_devlog(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture(
                "FU-1", invoker=fake_worker(reason="reason-first rewrite"))
            self.assertEqual(rc, 0, msg=err)
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "closed")
            self.assertEqual(fu["resolution"], "reason-first rewrite")
            devlog = (t.root / ".state" / "devlog.jsonl").read_text("utf-8")
            self.assertIn('"action": "refine"', devlog)

    def test_phase_files_unchanged_on_happy_path(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            before = (t.root / ".state" / "project.json").read_text("utf-8")
            rc, out, err = run_refine_capture("FU-1", invoker=fake_worker())
            self.assertEqual(rc, 0, msg=err)
            after = (t.root / ".state" / "project.json").read_text("utf-8")
            self.assertEqual(before, after)

    def test_worker_exit_2_leaves_fu_open(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture(
                "FU-1",
                invoker=fake_worker(exit_code=2, append_devlog=False, edit_file=False),
            )
            self.assertEqual(rc, 2)
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "open")

    def test_invariant_catches_phase_touch(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture(
                "FU-1", invoker=fake_worker(touch_phase=True))
            self.assertEqual(rc, 2)
            self.assertIn("post-REFINE invariant", err)
            # FU left open — the violation halted before close.
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "open")

    def test_invariant_catches_missing_devlog(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture(
                "FU-1", invoker=fake_worker(append_devlog=False))
            self.assertEqual(rc, 2)
            self.assertIn("action='refine'", err)
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "open")

    def test_commit_message_and_scope(self):
        with TempProject() as t:
            root = t.root
            _git(["init", "-q"], root)
            _git(["config", "user.email", "t@t.t"], root)
            _git(["config", "user.name", "t"], root)
            write_backlog(root, [_OPEN_FU])
            _git(["add", "-A"], root)
            _git(["commit", "-qm", "init"], root)

            rc, out, err = run_refine_capture(
                "FU-1", invoker=fake_worker(reason="tidy prose"))
            self.assertEqual(rc, 0, msg=err)

            msg = _git(["log", "-1", "--format=%s"], root).stdout.strip()
            self.assertEqual(msg, "refine(prose): FU-1 tidy prose")
            names = _git(["log", "-1", "--name-only", "--format="], root).stdout
            self.assertIn("notes.txt", names)
            self.assertIn(".state/followups.json", names)
            self.assertIn(".state/devlog.jsonl", names)
            # Sub-phase: lifecycle files are never in a refine commit.
            self.assertNotIn(".state/phases.json", names)
            self.assertNotIn(".state/steps.json", names)
            self.assertNotIn(".state/project.json", names)

    def test_happy_path_emits_valid_refine_telemetry(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture("FU-1", invoker=fake_worker())
            self.assertEqual(rc, 0, msg=err)
            row = last_telemetry(t.root)
            # The row must be schema-valid and carry the refine columns.
            v.validate_json_schema(row, v.load_schema(v.TELEMETRY_ENTRY_SCHEMA))
            self.assertEqual(row["action"], "refine")
            self.assertEqual(row["fu"], "FU-1")
            self.assertEqual(row["kind"], "prose")
            self.assertEqual(row["regime"], "refine")
            self.assertEqual(row["exit_code"], 0)

    def test_rate_limit_returns_3_with_valid_telemetry(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture("FU-1", invoker=rate_limited_worker())
            self.assertEqual(rc, 3)
            # FU untouched; nothing landed.
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "open")
            # A telemetry row is still written, with exit_code null (not 3, which
            # the schema enum forbids — regression guard for the dropped-row bug).
            row = last_telemetry(t.root)
            v.validate_json_schema(row, v.load_schema(v.TELEMETRY_ENTRY_SCHEMA))
            self.assertIsNone(row["exit_code"])
            self.assertEqual(row["action"], "refine")

    def test_malformed_signal_returns_2(self):
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])

            def no_signal(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None):
                return 0, "I did work but forgot the exit block."

            rc, out, err = run_refine_capture("FU-1", invoker=no_signal)
            self.assertEqual(rc, 2)
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "open")

    def test_json_scalar_resolution_still_closes(self):
        # A worker reason that looks like a JSON scalar must not be coerced to a
        # non-string (which would fail the followups schema and abort the close).
        with TempProject() as t:
            write_backlog(t.root, [_OPEN_FU])
            rc, out, err = run_refine_capture(
                "FU-1", invoker=fake_worker(reason="42"))
            self.assertEqual(rc, 0, msg=err)
            fu = next(f for f in read_backlog(t.root) if f["id"] == "FU-1")
            self.assertEqual(fu["status"], "closed")
            self.assertEqual(fu["resolution"], "42")


if __name__ == "__main__":
    unittest.main()

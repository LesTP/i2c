"""Tests for tools/run_iteration.py — single-iteration runner.

Mocks the ``claude -p`` subprocess via a `claude_invoker` seam so the
tests are deterministic and fast. The state_machine + assembler
subprocess calls are real — they're cheap and exercising them end-to-end
catches integration bugs that a pure-mock setup would miss.
"""

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

import run_iteration as ri  # noqa: E402

FIXTURE = I2C_ROOT / "examples" / "initial_state"


# ---------------------------------------------------------------------------
# Test fixture: copy initial_state + framework files into a temp project.
# ---------------------------------------------------------------------------


class TempProject:
    """Copy initial_state + WORKER_SPEC + adapter + instructions/ into a temp root.

    Mirrors test_assemble_context.TempProject's `with_framework=True` shape
    since the runner shells out to ``assemble_context.py`` which needs
    those files. Module contract for the fixture's phase-2 module
    (``event_store``) is also synthesized so the assembler doesn't bail.
    """

    def __init__(self):
        self._tmp = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_run_")
        self.root = Path(self._tmp.name) / "project"
        shutil.copytree(FIXTURE, self.root)
        for name in ("WORKER_SPEC.md", "CLAUDE.md", "CODEX.md"):
            shutil.copy2(I2C_ROOT / name, self.root / name)
        shutil.copytree(I2C_ROOT / "instructions", self.root / "instructions")
        # Stub ARCH_event_store.md to satisfy the module-contract requirement
        # for the fixture's current phase. Content is irrelevant to runner
        # tests; the assembler just needs the file to exist.
        (self.root / "ARCH_event_store.md").write_text(
            "# ARCH event_store\n\n## Contract\n\nStub for tests.\n",
            encoding="utf-8",
        )
        self._prev_cwd = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    # --- mutators ----------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Synthetic 5-line exit signals
# ---------------------------------------------------------------------------


def signal_block(
    *,
    exit_code: int = 0,
    reason: str = "did the thing",
    action_type: str = "EXECUTE",
    action_id: str = "2.2",
    steps_completed: int = 1,
) -> str:
    return (
        "I did some work.\n"
        "\n"
        f"EXIT: {exit_code}\n"
        f"REASON: {reason}\n"
        f"ACTION_TYPE: {action_type}\n"
        f"ACTION_ID: {action_id}\n"
        f"STEPS_COMPLETED: {steps_completed}\n"
    )


# ---------------------------------------------------------------------------
# Fake claude invokers (the test seam)
# ---------------------------------------------------------------------------


def make_fake_invoker(captured: str, rc: int = 0, *, capture=None):
    """Return a callable shaped like invoke_claude that emits `captured`.

    The optional `capture` list collects (cwd, prompt, model, budget) tuples
    so tests can assert what the runner sent to the worker.
    """
    def fake(prompt, *, cwd, model, max_budget_usd):
        if capture is not None:
            capture.append({
                "cwd": cwd, "prompt": prompt, "model": model,
                "max_budget_usd": max_budget_usd,
            })
        return rc, captured
    return fake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_iter(*, backend="claude", invoker=None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            rc = ri.run_iteration(
                backend=backend,
                model="sonnet",
                max_budget_usd=5.00,
                claude_invoker=invoker or make_fake_invoker(signal_block()),
            )
        except SystemExit as e:
            rc = e.code if isinstance(e.code, int) else 2
    return rc, out.getvalue(), err.getvalue()


def read_summary(root: Path) -> str:
    summary = root / "logs" / "loop" / "summary.log"
    if not summary.is_file():
        return ""
    return summary.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestStateMachineExit(unittest.TestCase):
    """When state_machine dispatches EXIT, no worker invocation happens."""

    def test_blocked_short_circuits_to_exit_zero(self):
        with TempProject() as p:
            p.patch_project(blocked=True, state="close")
            calls = []
            invoker = make_fake_invoker(signal_block(), capture=calls)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)
            # claude was NOT invoked.
            self.assertEqual(len(calls), 0)
            # summary.log has an iter=1 EXIT line.
            summary = read_summary(p.root)
            self.assertIn("iter=1", summary)
            self.assertIn("action=EXIT", summary)
            self.assertIn("exit=0", summary)


class TestHappyPath(unittest.TestCase):
    """Fixture is phase=2 execute with 2 pending steps → EXECUTE/execute."""

    def test_runs_end_to_end_and_writes_logs(self):
        with TempProject() as p:
            calls = []
            invoker = make_fake_invoker(signal_block(), capture=calls)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)
            # claude was invoked exactly once.
            self.assertEqual(len(calls), 1)
            # Prompt is non-trivial; it should at least contain a banner.
            self.assertIn("WORKER CONTRACT", calls[0]["prompt"])
            self.assertIn("ACTION CONTEXT", calls[0]["prompt"])
            # Logs were written.
            log_dir = p.root / "logs" / "loop"
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())
            self.assertTrue((log_dir / "iteration_001.txt").is_file())
            summary = read_summary(p.root)
            self.assertIn("iter=1", summary)
            self.assertIn("action=EXECUTE", summary)
            self.assertIn("exit=0", summary)
            self.assertIn("did the thing", summary)

    def test_iteration_counter_increments(self):
        with TempProject() as p:
            invoker = make_fake_invoker(signal_block())
            rc1, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc1, 0)
            rc2, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc2, 0)
            log_dir = p.root / "logs" / "loop"
            self.assertTrue((log_dir / "iteration_001_prompt.md").is_file())
            self.assertTrue((log_dir / "iteration_002_prompt.md").is_file())


class TestExitSignalParsing(unittest.TestCase):
    def test_missing_signal_treated_as_exit_2(self):
        with TempProject() as p:
            invoker = make_fake_invoker("No signal here, sorry.\n", rc=0)
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 2)
            summary = read_summary(p.root)
            self.assertIn("exit=2", summary)
            self.assertIn("exit signal missing", summary)

    def test_worker_exit_2_propagates(self):
        with TempProject() as p:
            invoker = make_fake_invoker(
                signal_block(exit_code=2, reason="escalating")
            )
            rc, _, _ = run_iter(invoker=invoker)
            self.assertEqual(rc, 2)
            self.assertIn("exit=2", read_summary(p.root))

    def test_parse_exit_signal_pure_helper(self):
        # Sanity: pure-function parser extracts every field correctly.
        text = signal_block(
            exit_code=1, reason="phase done", action_type="CLOSE",
            action_id="2.4", steps_completed=4,
        )
        signal = ri.parse_exit_signal(text)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["exit_code"], 1)
        self.assertEqual(signal["reason"], "phase done")
        self.assertEqual(signal["next_action"], "close")
        self.assertEqual(signal["action_id"], "2.4")
        self.assertEqual(signal["steps_completed"], 4)

    def test_parse_exit_signal_returns_none_when_missing(self):
        self.assertIsNone(ri.parse_exit_signal("nothing relevant"))


class TestCloseInvariantHalt(unittest.TestCase):
    """A CLOSE worker that doesn't set blocked=true must trip FU-22."""

    def test_close_without_blocked_halts_with_invariant_error(self):
        with TempProject() as p:
            # Drive state to "close" + phase 2 still pending, so the
            # state machine dispatches CLOSE.
            p.patch_project(state="close", blocked=False)
            # Worker emits a clean EXIT 0 signal but DOESN'T update
            # blocked/phase status. Runner must detect via invariants.
            invoker = make_fake_invoker(
                signal_block(
                    exit_code=0, reason="closed it",
                    action_type="CLOSE", action_id="2.4",
                )
            )
            rc, out, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("post-CLOSE invariants failed", err)
            summary = read_summary(p.root)
            self.assertIn("exit=2", summary)
            self.assertIn("post-CLOSE invariants failed", summary)

    def test_close_with_proper_invariants_passes(self):
        with TempProject() as p:
            # Pre-stage the post-CLOSE state correctly: state=close, then
            # the worker (our fake) "would have" set blocked=true and
            # marked phase complete. Simulate that by patching them up
            # front since the fake doesn't actually call state.py.
            p.patch_project(state="close", blocked=True, phase=2)
            p.patch_phase_status(2, "complete")
            invoker = make_fake_invoker(
                signal_block(
                    exit_code=0, reason="closed it cleanly",
                    action_type="CLOSE", action_id="2.4",
                )
            )
            rc, _, err = run_iter(invoker=invoker)
            self.assertEqual(rc, 0, msg=err)


class TestSummaryLog(unittest.TestCase):
    def test_summary_line_format(self):
        with TempProject() as p:
            invoker = make_fake_invoker(
                signal_block(exit_code=0, reason="ok")
            )
            run_iter(invoker=invoker)
            summary = read_summary(p.root).strip().splitlines()[-1]
            # Expected components per the plan:
            # YYYY-MM-DDTHH:MM:SS+00:00 | iter=N | backend=claude | action=X | exit=N | reason="..."
            self.assertIn("| iter=1 ", summary)
            self.assertIn("| backend=claude ", summary)
            self.assertIn("| action=EXECUTE ", summary)
            self.assertIn("| exit=0 ", summary)
            self.assertIn('reason="ok"', summary)


if __name__ == "__main__":
    unittest.main()

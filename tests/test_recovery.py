"""Tests for i2c/recovery.py — the deterministic drift audit.

Phase 1 covers the pure-``.state`` signals (``audit_state``). git/disk signals
(``audit_git``) are exercised in the Phase 2 section below once implemented.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent

from i2c import assemble_context as ac
from i2c import control
from i2c import recovery
from i2c import run_iteration as ri
from tests._fixtures import copy_fixture, write_adapters

FIXTURE = I2C_ROOT / "examples" / "initial_state"

ARCH_EVENT_STORE = """\
# ARCH: event_store

## Purpose

Append-only event storage with atomic writes.

## Interface

- `append(event) -> None`
- `read(since) -> list[Event]`
"""


class TempProject:
    """Copy the fixture into a temp dir and chdir into it."""

    def __init__(self):
        self._tmp: tempfile.TemporaryDirectory | None = None
        self.root: Path | None = None

    def __enter__(self) -> "TempProject":
        self._tmp = tempfile.TemporaryDirectory(prefix="i2c_recovery_")
        self.root = Path(self._tmp.name) / "project"
        copy_fixture(self.root)
        self._prev_cwd = Path.cwd()
        os.chdir(self.root)
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        self._tmp.cleanup()

    def _read(self, name: str):
        return json.loads(
            (self.root / ".state" / name).read_text(encoding="utf-8")
        )

    def _write(self, name: str, data) -> None:
        (self.root / ".state" / name).write_text(
            json.dumps(data, indent=2) + "\n", encoding="utf-8"
        )

    def patch_project(self, **kwargs) -> None:
        data = self._read("project.json")
        data.update(kwargs)
        self._write("project.json", data)

    def patch_phase_status(self, phase_id: int, status: str) -> None:
        data = self._read("phases.json")
        for p in data:
            if p["id"] == phase_id:
                p["status"] = status
        self._write("phases.json", data)

    def complete_all_steps(self, phase: int, *, commit: str = "abc1234") -> None:
        data = self._read("steps.json")
        for s in data:
            if s["phase"] == phase:
                s["status"] = "complete"
                s.setdefault("commit", commit)
        self._write("steps.json", data)

    def drop_commit(self, phase: int, step: int) -> None:
        data = self._read("steps.json")
        for s in data:
            if s["phase"] == phase and s["step"] == step:
                s.pop("commit", None)
        self._write("steps.json", data)

    def state(self) -> control.ProjectState:
        return control.load_state(self.root)

    # --- git helpers (Phase 2) --------------------------------------------

    def git(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args], cwd=str(self.root), capture_output=True, text=True
        )

    def git_init(self) -> None:
        self.git("init")
        self.git("config", "user.email", "t@example.test")
        self.git("config", "user.name", "Tester")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "-A")
        self.git("commit", "-m", "init")

    def git_commit_all(self, msg: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-m", msg)

    def clear_commits(self) -> None:
        data = self._read("steps.json")
        for s in data:
            s.pop("commit", None)
        self._write("steps.json", data)

    def set_commit(self, phase: int, step: int, commit: str) -> None:
        data = self._read("steps.json")
        for s in data:
            if s["phase"] == phase and s["step"] == step:
                s["commit"] = commit
        self._write("steps.json", data)

    def setup_assembly_assets(self) -> None:
        """Copy framework adapters + a fixed ARCH so a full prompt assembles."""
        write_adapters(self.root)
        (self.root / "ARCH_event_store.md").write_text(
            ARCH_EVENT_STORE, encoding="utf-8"
        )


def _signals(findings: list[recovery.DriftFinding]) -> list[str]:
    return [f.signal for f in findings]


class TestAuditStateClean(unittest.TestCase):
    def test_fixture_has_no_drift(self):
        with TempProject() as p:
            self.assertEqual(recovery.audit_state(p.state()), [])

    def test_audit_boundary_is_not_drift(self):
        # A phase complete + parked at the boundary is a normal halt, not drift.
        with TempProject() as p:
            p.patch_phase_status(2, "complete")
            p.patch_project(state="audit_boundary")
            self.assertEqual(recovery.audit_state(p.state()), [])


class TestExecuteNotAdvanced(unittest.TestCase):
    def test_all_steps_complete_but_state_execute(self):
        with TempProject() as p:
            p.complete_all_steps(2)  # state stays "execute" from the fixture
            findings = recovery.audit_state(p.state())
            self.assertEqual(_signals(findings), [recovery.SIG_EXECUTE_NOT_ADVANCED])
            f = findings[0]
            self.assertTrue(f.reconcilable)
            self.assertIsNotNone(f.proposal)
            self.assertEqual(f.proposal.op, "set")
            self.assertEqual(f.proposal.file, "project.json")
            self.assertEqual(f.proposal.payload, {"keys": {"state": "review"}})

    def test_pending_steps_means_no_finding(self):
        with TempProject() as p:
            # Fixture already has pending steps in phase 2 + state execute.
            self.assertNotIn(
                recovery.SIG_EXECUTE_NOT_ADVANCED,
                _signals(recovery.audit_state(p.state())),
            )


class TestCloseGateNotSet(unittest.TestCase):
    def test_phase_complete_state_still_close(self):
        with TempProject() as p:
            p.complete_all_steps(2)
            p.patch_phase_status(2, "complete")
            p.patch_project(state="close")
            findings = recovery.audit_state(p.state())
            self.assertIn(recovery.SIG_CLOSE_GATE_NOT_SET, _signals(findings))
            f = next(x for x in findings if x.signal == recovery.SIG_CLOSE_GATE_NOT_SET)
            self.assertTrue(f.reconcilable)
            self.assertEqual(f.proposal.payload, {"keys": {"state": "audit_boundary"}})

    def test_phase_pending_is_not_close_drift(self):
        with TempProject() as p:
            p.patch_project(state="close")  # phase 2 record still pending
            self.assertNotIn(
                recovery.SIG_CLOSE_GATE_NOT_SET,
                _signals(recovery.audit_state(p.state())),
            )


class TestSignalExclusivity(unittest.TestCase):
    def test_execute_not_advanced_yields_to_close_gate(self):
        # state=execute + 0 pending + phase record already complete: this is a
        # close-gate situation, not an advance-to-review one. The two checks must
        # not both fire (they'd propose conflicting project.state writes).
        with TempProject() as p:
            p.complete_all_steps(2)
            p.patch_phase_status(2, "complete")  # state stays "execute"
            sigs = _signals(recovery.audit_state(p.state()))
            self.assertIn(recovery.SIG_CLOSE_GATE_NOT_SET, sigs)
            self.assertNotIn(recovery.SIG_EXECUTE_NOT_ADVANCED, sigs)


class TestStepCompleteWithoutCommit(unittest.TestCase):
    def test_complete_step_missing_commit(self):
        with TempProject() as p:
            p.drop_commit(2, 1)  # phase 2 step 1 is complete in the fixture
            findings = recovery.audit_state(p.state())
            self.assertIn(
                recovery.SIG_STEP_COMPLETE_NO_COMMIT, _signals(findings)
            )
            f = next(
                x for x in findings if x.signal == recovery.SIG_STEP_COMPLETE_NO_COMMIT
            )
            self.assertFalse(f.reconcilable)
            self.assertEqual((f.phase, f.step), (2, 1))


class TestMessagesHelper(unittest.TestCase):
    def test_messages_flattens_to_strings(self):
        with TempProject() as p:
            p.complete_all_steps(2)
            findings = recovery.audit_state(p.state())
            msgs = recovery.messages(findings)
            self.assertTrue(all(isinstance(m, str) for m in msgs))
            self.assertEqual(len(msgs), len(findings))


# ---------------------------------------------------------------------------
# Phase 2: git/disk signals (audit_git)
# ---------------------------------------------------------------------------


class TestAuditGitNoRepo(unittest.TestCase):
    def test_non_repo_yields_no_findings(self):
        with TempProject() as p:
            self.assertFalse(recovery.is_git_repo(p.root))
            self.assertEqual(recovery.audit_git(p.root, p.state()), [])

    def test_git_off_path_is_tolerated(self):
        with TempProject() as p:
            original = recovery._git

            def boom(*a, **k):
                raise FileNotFoundError("git")

            recovery._git = boom
            try:
                self.assertFalse(recovery.is_git_repo(p.root))
                self.assertEqual(recovery.audit_git(p.root, p.state()), [])
            finally:
                recovery._git = original


class TestCommitWithoutStep(unittest.TestCase):
    def test_pending_step_with_matching_commit(self):
        with TempProject() as p:
            p.git_init()
            p.clear_commits()
            p.git_commit_all("cleanup: drop fixture commit hashes")
            # Step 2.2 is pending in the fixture; a commit lands for it.
            p.git("commit", "--allow-empty", "-m", "2.2: Reader API")
            findings = recovery.audit_git(p.root, p.state())
            self.assertEqual(_signals(findings), [recovery.SIG_COMMIT_WITHOUT_STEP])
            f = findings[0]
            self.assertTrue(f.reconcilable)
            self.assertEqual(f.proposal.op, "complete")
            self.assertEqual(f.proposal.file, "steps.json")
            self.assertEqual(f.proposal.payload["phase"], 2)
            self.assertEqual(f.proposal.payload["step"], 2)
            self.assertRegex(f.proposal.payload["commit"], r"^[0-9a-f]{7}$")

    def test_combined_audit_includes_git_signal(self):
        with TempProject() as p:
            p.git_init()
            p.clear_commits()
            p.git_commit_all("cleanup")
            p.git("commit", "--allow-empty", "-m", "2.2: Reader API")
            sigs = [f.signal for f in recovery.audit(p.root)]
            self.assertIn(recovery.SIG_COMMIT_WITHOUT_STEP, sigs)

    def test_step_not_matched_by_longer_step_number(self):
        # Anchor disambiguation: pending step 2.2 must NOT match a "2.20:" commit.
        with TempProject() as p:
            p.git_init()
            p.clear_commits()
            p.git_commit_all("cleanup")
            p.git("commit", "--allow-empty", "-m", "2.20: unrelated longer step")
            sigs = _signals(recovery.audit_git(p.root, p.state()))
            self.assertNotIn(recovery.SIG_COMMIT_WITHOUT_STEP, sigs)


class TestReconcileCompleteOp(unittest.TestCase):
    def test_apply_marks_step_complete_with_commit(self):
        # End-to-end exercise of the `complete` reconcile op (git-backed).
        with TempProject() as p:
            p.git_init()
            p.clear_commits()
            p.git_commit_all("cleanup")
            p.git("commit", "--allow-empty", "-m", "2.2: Reader API")
            report = control.reconcile(p.root, apply=True)
            self.assertIn(
                recovery.SIG_COMMIT_WITHOUT_STEP, [i.signal for i in report.items]
            )
            steps = json.loads(
                (p.root / ".state" / "steps.json").read_text(encoding="utf-8")
            )
            rec = next(s for s in steps if s["phase"] == 2 and s["step"] == 2)
            self.assertEqual(rec["status"], "complete")
            self.assertRegex(rec["commit"], r"^[0-9a-f]{7}$")


class TestCommitAbsentFromGit(unittest.TestCase):
    def test_recorded_commit_not_in_history(self):
        with TempProject() as p:
            p.git_init()
            p.clear_commits()
            p.set_commit(2, 1, "deadbee")  # step 2.1 is complete; hash is bogus
            p.git_commit_all("set a bogus commit hash")
            findings = recovery.audit_git(p.root, p.state())
            self.assertEqual(_signals(findings), [recovery.SIG_COMMIT_ABSENT_FROM_GIT])
            self.assertFalse(findings[0].reconcilable)
            self.assertEqual((findings[0].phase, findings[0].step), (2, 1))


class TestDirtyTree(unittest.TestCase):
    def _setup_clean_repo(self, p: TempProject) -> None:
        (p.root / "foo.txt").write_text("line1\nline2\n", encoding="utf-8", newline="")
        p.git_init()
        p.clear_commits()
        p.git_commit_all("cleanup commits")

    def test_substantive_change_is_dirty(self):
        with TempProject() as p:
            self._setup_clean_repo(p)
            (p.root / "foo.txt").write_text(
                "line1\nCHANGED\n", encoding="utf-8", newline=""
            )
            self.assertTrue(recovery.working_tree_dirty(p.root))
            sigs = _signals(recovery.audit_git(p.root, p.state()))
            self.assertEqual(sigs, [recovery.SIG_STEP_COMPLETE_DIRTY_TREE])

    def test_crlf_only_change_is_not_dirty(self):
        with TempProject() as p:
            self._setup_clean_repo(p)
            # Rewrite identical content with CRLF line endings (NTFS false-pos).
            (p.root / "foo.txt").write_bytes(b"line1\r\nline2\r\n")
            self.assertFalse(recovery.working_tree_dirty(p.root))
            self.assertNotIn(
                recovery.SIG_STEP_COMPLETE_DIRTY_TREE,
                _signals(recovery.audit_git(p.root, p.state())),
            )

    def test_reindentation_is_dirty(self):
        # Leading-indentation change is semantic in Python: must register dirty
        # (guards against an over-broad --ignore-all-space).
        with TempProject() as p:
            (p.root / "mod.py").write_text(
                "def f():\n    return 1\n", encoding="utf-8", newline=""
            )
            p.git_init()
            p.clear_commits()
            p.git_commit_all("cleanup")
            (p.root / "mod.py").write_text(
                "def f():\n        return 1\n", encoding="utf-8", newline=""
            )
            self.assertTrue(recovery.working_tree_dirty(p.root))


# ---------------------------------------------------------------------------
# Phase 4: assembler recovery prompts + out-of-band dispatch
# ---------------------------------------------------------------------------


def _full_prompt(action: str, *, target: int | None = None) -> str:
    ns = argparse.Namespace(
        action=action, section=None, phase=2, mode="autonomous", module=None,
        backend="claude", target=target, step_budget=1, emit="full",
    )
    return ac.build_full_prompt(ac.build_context(ns))


class TestAssembleRecoveryPrompt(unittest.TestCase):
    def test_diagnose_prompt_shape(self):
        with TempProject() as p:
            p.setup_assembly_assets()
            prompt = _full_prompt("diagnose")
        self.assertIn("## Failure Context", prompt)
        self.assertIn("### Drift Audit", prompt)
        # Recovery actions emit no Next State.
        self.assertNotIn("## Next State:", prompt)
        # The diagnose instruction body (from its first H2) is included.
        self.assertIn("Read the Failure Context", prompt)

    def test_reconcile_prompt_surfaces_proposal(self):
        with TempProject() as p:
            p.setup_assembly_assets()
            p.complete_all_steps(2)  # drift: execute with 0 pending steps
            prompt = _full_prompt("reconcile")
        self.assertIn("## Failure Context", prompt)
        self.assertIn("proposed reconcile:", prompt)
        self.assertIn("state=review", prompt)


class TestRunnerDispatchOverride(unittest.TestCase):
    def test_action_override_bypasses_state_machine(self):
        with TempProject() as p:
            p.setup_assembly_assets()
            original_sm = ri.run_state_machine

            def boom(*a, **k):
                raise AssertionError("state machine must not run for recovery")

            captured: dict = {}

            def fake_claude(prompt, **kwargs):
                captured["called"] = True
                return 0, "EXIT: 0\nREASON: diagnosed"

            ri.run_state_machine = boom
            try:
                rc = ri.run_iteration(
                    backend="claude",
                    model="sonnet",
                    max_budget_usd=1.0,
                    action_override="diagnose",
                    target=None,
                    claude_invoker=fake_claude,
                )
            finally:
                ri.run_state_machine = original_sm
            self.assertEqual(rc, 0)
            self.assertTrue(captured.get("called"))
            summary = (p.root / "logs" / "loop" / "summary.log").read_text(
                encoding="utf-8"
            )
            self.assertIn("action=DIAGNOSE", summary)

    def test_runner_surfaces_drift_advisory(self):
        with TempProject() as p:
            p.setup_assembly_assets()
            # Complete all phase-2 steps but leave state=execute: the state
            # machine dispatches REVIEW, the fake worker doesn't advance state,
            # so post-action drift (execute_state_not_advanced) is present.
            p.complete_all_steps(2)
            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc = ri.run_iteration(
                    backend="claude",
                    model="sonnet",
                    max_budget_usd=1.0,
                    claude_invoker=lambda prompt, **k: (0, "EXIT: 0\nREASON: ok"),
                )
            self.assertEqual(rc, 0)
            self.assertIn("workflow drift detected", err_buf.getvalue())


if __name__ == "__main__":
    unittest.main()

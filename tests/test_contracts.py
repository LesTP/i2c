"""Action contracts (DESIGN_action_contracts_v1): the table, path scopes,
postconditions, the runner gate, `i2c check`, and the prompt section.

The runner-integration cases replay the FU-70 (pidev-spike, 2026-10-02) worker
failures with fake workers and assert each now ends exit 2 with no commit.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from i2c import assemble_context as ac
from i2c import cli
from i2c import contracts as c
from i2c import run_iteration as ri
from i2c import state

from test_run_iteration import TempProject, read_summary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)


def _init_repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    # Isolate from the operator's global excludes (which may ignore dist/,
    # __pycache__/ ...): the gate's own NOISE list is what is under test.
    empty = root / ".git" / "no_global_excludes"
    empty.write_text("", encoding="utf-8")
    _git(root, "config", "core.excludesFile", empty.as_posix())
    (root / ".gitignore").write_text("logs/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def _head(root: Path) -> str:
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def _set_project(root: Path, **kw) -> None:
    path = root / ".state" / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(kw)
    state.atomic_write_json(path, data)


def _set_phase_status(root: Path, phase: int, status: str) -> None:
    path = root / ".state" / "phases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for rec in data:
        if rec["id"] == phase:
            rec["status"] = status
    state.atomic_write_json(path, data)


def _complete_step(root: Path, phase: int, step: int) -> None:
    path = root / ".state" / "steps.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    for s in data:
        if s["phase"] == phase and s["step"] == step:
            s["status"] = "complete"
    state.atomic_write_json(path, data)


def _devlog(root: Path, action: str, phase: int = 2, step=None) -> None:
    state.atomic_append_jsonl(root / ".state" / "devlog.jsonl", {
        "phase": phase, "step": step, "action": action, "outcome": "complete",
        "summary": f"{action} done", "contracts": [],
        "timestamp": "2026-10-02T00:00:00Z",
    })


def _worker(effect, *, exit_code: int = 0, reason: str = "done"):
    """A claude-shaped fake worker that applies `effect(cwd)` then signals."""
    def fake(prompt, *, cwd, model, max_budget_usd, system_prompt_file=None, timeout=None):
        effect(Path(cwd))
        return 0, f"work\n\nEXIT: {exit_code}\nREASON: {reason}\n"
    return fake


def _run(invoker) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = ri.run_iteration(
            backend="claude", model="sonnet", max_budget_usd=1.0, claude_invoker=invoker,
        )
    return rc, out.getvalue(), err.getvalue()


def _last_telemetry(root: Path) -> dict:
    lines = (root / ".state" / "telemetry.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads([ln for ln in lines if ln.strip()][-1])


# ---------------------------------------------------------------------------
# The table + paths (pure)
# ---------------------------------------------------------------------------


class TestTable(unittest.TestCase):
    def test_every_lifecycle_action_has_a_contract(self):
        self.assertEqual(set(c.CONTRACTS), set(c.ACTIONS))
        for name in (n for k in c.CONTRACTS.values() for n in k.requires):
            self.assertIn(name, c.POSTCONDITIONS)

    def test_end_states(self):
        self.assertEqual(c.allowed_end_states("plan", "build"), ("tests", "audit_escalation"))
        self.assertEqual(c.allowed_end_states("plan", "refine"), ("execute", "audit_escalation"))
        self.assertEqual(c.allowed_end_states("plan", "explore"), ("execute", "audit_escalation"))
        self.assertEqual(c.allowed_end_states("close", "build"), ("audit_boundary",))
        self.assertIn("tests", c.allowed_end_states("tests", "build"))  # partial

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            c.get("bogus")


class TestGlob(unittest.TestCase):
    def test_glob_semantics(self):
        m = c.glob_match
        self.assertTrue(m("ARCH_calc.md", "ARCH_*.md"))
        self.assertFalse(m("docs/ARCH_calc.md", "ARCH_*.md"))  # * stays in a segment
        self.assertTrue(m("tests/acceptance/phase_2/a/b.py", "tests/acceptance/phase_2/**"))
        self.assertFalse(m("tests/acceptance/phase_20/b.py", "tests/acceptance/phase_2/**"))
        self.assertTrue(m("pkg/__pycache__/x.pyc", "**/__pycache__/**"))
        self.assertTrue(m("__pycache__/x.pyc", "**/__pycache__/**"))
        self.assertTrue(m("anything/at/all", "**"))
        self.assertTrue(m("tests\\acceptance\\phase_2\\t.py", "tests/acceptance/**"))


class TestClassifyPaths(unittest.TestCase):
    def test_plan_may_change_nothing(self):
        r = c.classify_paths("plan", 2, ["ARCHITECTURE.md", ".state/steps.json"])
        self.assertEqual(r.out_of_scope, ("ARCHITECTURE.md",))
        self.assertEqual(r.allowed, ())

    def test_tests_owns_its_phase_suite_only(self):
        r = c.classify_paths("tests", 2, [
            "tests/acceptance/phase_2/test_x.py", "tests/acceptance/__init__.py",
            "tests/__init__.py", "tests/acceptance/phase_1/test_old.py", "calc.py",
        ])
        self.assertEqual(set(r.allowed), {
            "tests/acceptance/phase_2/test_x.py", "tests/acceptance/__init__.py",
            "tests/__init__.py"})
        self.assertEqual(r.protected, ("tests/acceptance/phase_1/test_old.py",))
        self.assertEqual(r.out_of_scope, ("calc.py",))

    def test_execute_broad_scope_but_protected_set_wins(self):
        r = c.classify_paths("execute", 2, [
            "src/calc.py", "tests/test_calc.py", "tests/acceptance/phase_2/t.py",
            "i2c.toml", "PIDEV.md",
        ])
        self.assertEqual(set(r.allowed), {"src/calc.py", "tests/test_calc.py"})
        self.assertEqual(set(r.protected), {
            "tests/acceptance/phase_2/t.py", "i2c.toml", "PIDEV.md"})

    def test_close_fu70_iteration_3(self):
        # pidev-spike da7b577: CLOSE wrote a stub next-phase suite.
        r = c.classify_paths("close", 1, [
            "tests/__init__.py", "tests/acceptance/phase_2/__init__.py",
            "tests/acceptance/phase_2/test_phase_2.py", "ARCHITECTURE.md",
        ])
        self.assertEqual(r.allowed, ("ARCHITECTURE.md",))
        self.assertEqual(set(r.protected), {
            "tests/acceptance/phase_2/__init__.py",
            "tests/acceptance/phase_2/test_phase_2.py"})
        self.assertEqual(r.out_of_scope, ("tests/__init__.py",))

    def test_noise_never_counts(self):
        r = c.classify_paths("plan", 2, [
            "src/__pycache__/m.cpython-311.pyc", ".pytest_cache/v/x", "logs/loop/i.txt"])
        self.assertEqual(r.out_of_scope, ())
        self.assertEqual(len(r.noise), 3)


# ---------------------------------------------------------------------------
# Runner gate - FU-70 replays and compliant runs
# ---------------------------------------------------------------------------


class TestRunnerGate(unittest.TestCase):
    """Fixture: phase 2 (Build), state=execute, steps 2.2-2.4 pending."""

    def test_plan_claimed_success_without_steps_fails(self):
        # FU-70 iteration 4: steps written in the reply, never recorded.
        with TempProject(real_contract=True) as p:
            _set_project(p.root, state="plan")
            _init_repo(p.root)
            head = _head(p.root)
            rc, _, err = _run(_worker(lambda r: None, reason="Plan complete - 6 steps"))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("post-PLAN invariants failed", err)
            self.assertIn("(currently 'plan')", err)
            self.assertIn("no 'plan' devlog entry", err)
            self.assertEqual(_head(p.root), head)
            self.assertIn("exit=2", read_summary(p.root))
            row = _last_telemetry(p.root)
            self.assertEqual(row["exit_code"], 2)
            self.assertTrue(row["contract_violation"])

    def test_plan_compliant_passes(self):
        with TempProject(real_contract=True) as p:
            _set_project(p.root, state="plan")

            def plan(root):
                _set_project(root, state="tests")
                _devlog(root, "plan")

            rc, _, err = _run(_worker(plan))
            self.assertEqual(rc, 0, msg=err)
            self.assertIsNone(_last_telemetry(p.root)["contract_violation"])

    def test_review_without_devlog_fails(self):
        # FU-70 iteration 2: a real review that skipped the required devlog entry.
        with TempProject(real_contract=True) as p:
            _set_project(p.root, state="review")
            rc, _, err = _run(_worker(lambda r: _set_project(r, state="close")))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("no 'review' devlog entry", err)

    def test_close_writing_next_phase_suite_fails_and_commits_nothing(self):
        # FU-70 iteration 3 (pidev-spike da7b577), through the real runner.
        with TempProject(real_contract=True) as p:
            _set_project(p.root, state="close")
            _init_repo(p.root)
            head = _head(p.root)

            def close(root):
                _set_project(root, state="audit_boundary")
                _set_phase_status(root, 2, "complete")
                _devlog(root, "close")
                (root / "ARCHITECTURE.md").write_text("# Arch\n", encoding="utf-8")
                stub = root / "tests" / "acceptance" / "phase_3"
                stub.mkdir(parents=True)
                (stub / "test_phase_3.py").write_text(
                    "def test_phase_3():\n    assert True\n", encoding="utf-8")

            rc, _, err = _run(_worker(close))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("protected path(s) changed: tests/acceptance/phase_3/test_phase_3.py", err)
            self.assertEqual(_head(p.root), head)  # nothing committed
            self.assertTrue((p.root / "tests/acceptance/phase_3/test_phase_3.py").exists())

    def test_close_compliant_commits_docs_and_flags_out_of_scope(self):
        # build-a-stew p5: CLOSE also wrote build output -> flagged, not committed.
        with TempProject(real_contract=True) as p:
            _set_project(p.root, state="close")
            _init_repo(p.root)

            def close(root):
                _set_project(root, state="audit_boundary")
                _set_phase_status(root, 2, "complete")
                _devlog(root, "close")
                (root / "ARCHITECTURE.md").write_text("# Arch v2\n", encoding="utf-8")
                (root / "site").mkdir()
                (root / "site" / "index.html").write_text("<p>\n", encoding="utf-8")

            rc, _, err = _run(_worker(close))
            self.assertEqual(rc, 0, msg=err)
            self.assertIn("outside the CLOSE write scope left uncommitted: site/index.html", err)
            committed = _git(p.root, "log", "--name-only", "--pretty=format:").stdout
            self.assertIn("ARCHITECTURE.md", committed)
            self.assertNotIn("site/index.html", committed)
            self.assertIn("site/", _git(p.root, "status", "--porcelain").stdout)

    def test_execute_compliant_commits_code_but_not_noise(self):
        with TempProject(real_contract=True) as p:
            _init_repo(p.root)

            def execute(root):
                _complete_step(root, 2, 2)
                _devlog(root, "execute", step=2)
                (root / "src").mkdir()
                (root / "src" / "store.py").write_text("x = 1\n", encoding="utf-8")
                (root / "src" / "__pycache__").mkdir()
                (root / "src" / "__pycache__" / "store.cpython-311.pyc").write_bytes(b"\0")

            rc, _, err = _run(_worker(execute))
            self.assertEqual(rc, 0, msg=err)
            files = _git(p.root, "show", "--name-only", "--pretty=format:", "HEAD").stdout
            self.assertIn("src/store.py", files)
            self.assertNotIn("__pycache__", files)

    def test_execute_completing_two_steps_fails(self):
        with TempProject(real_contract=True) as p:
            def execute(root):
                _complete_step(root, 2, 2)
                _complete_step(root, 2, 3)
                _devlog(root, "execute", step=2)

            rc, _, err = _run(_worker(execute))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("exactly one phase 2 step", err)

    def test_execute_review_with_steps_pending_fails(self):
        with TempProject(real_contract=True) as p:
            def execute(root):
                _complete_step(root, 2, 2)
                _devlog(root, "execute", step=2)
                _set_project(root, state="review")

            rc, _, err = _run(_worker(execute))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("still pending", err)

    def test_execute_editing_frozen_suite_fails(self):
        with TempProject(real_contract=True) as p:
            suite = p.root / "tests" / "acceptance" / "phase_2"
            suite.mkdir(parents=True)
            (suite / "test_contract.py").write_text("def test(): assert 0\n", encoding="utf-8")
            _init_repo(p.root)

            def execute(root):
                _complete_step(root, 2, 2)
                _devlog(root, "execute", step=2)
                (suite / "test_contract.py").write_text("def test(): assert 1\n", encoding="utf-8")

            rc, _, err = _run(_worker(execute))
            self.assertEqual(rc, 2, msg=err)
            self.assertIn("protected path(s) changed: tests/acceptance/phase_2/test_contract.py", err)

    def test_escalation_is_not_a_contract_failure(self):
        with TempProject(real_contract=True) as p:
            def escalate(root):
                _set_project(root, state="audit_escalation")
                _devlog(root, "execute", step=2)

            rc, _, err = _run(_worker(escalate, exit_code=2, reason="need a human"))
            self.assertEqual(rc, 2)
            self.assertNotIn("invariants failed", err)
            self.assertIn("need a human", read_summary(p.root))

    def test_baseline_env_set_only_during_the_worker(self):
        seen = {}
        with TempProject(real_contract=True) as p:
            def effect(root):
                seen["env"] = os.environ.get(c.BASELINE_ENV)

            _run(_worker(effect))
            self.assertTrue(seen["env"] and Path(seen["env"]).is_file())
            self.assertNotIn(c.BASELINE_ENV, os.environ)
            base = c.Baseline.read(Path(seen["env"]))
            self.assertEqual((base.action, base.phase), ("execute", 2))


# ---------------------------------------------------------------------------
# `i2c check`
# ---------------------------------------------------------------------------


def _check(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(["check", *argv])
    return rc, out.getvalue(), err.getvalue()


class TestCheckCommand(unittest.TestCase):
    def _baseline(self, p, action: str) -> Path:
        path = p.root / "baseline.json"
        c.Baseline(action=action, phase=2, pre_dirty=[],
                   before=c.Snapshot.load(p.root)).write(path)
        return path

    def test_reports_failures_then_ok_with_baseline(self):
        with TempProject() as p:
            _set_project(p.root, state="plan")
            path = self._baseline(p, "plan")
            os.environ[c.BASELINE_ENV] = str(path)
            try:
                rc, out, _ = _check()
                self.assertEqual(rc, 1)
                self.assertIn("FAIL: post-PLAN invariant", out)
                self.assertIn("no 'plan' devlog entry", out)
                _set_project(p.root, state="tests")
                _devlog(p.root, "plan")
                rc, out, _ = _check()
                self.assertEqual(rc, 0, msg=out)
                self.assertIn("OK: PLAN contract satisfied", out)
            finally:
                os.environ.pop(c.BASELINE_ENV, None)

    def test_without_baseline_checks_end_state_only(self):
        with TempProject() as p:
            _set_project(p.root, state="close")
            rc, out, _ = _check("--action", "review")
            self.assertEqual(rc, 0, msg=out)
            self.assertIn("not checked without a runner baseline: devlog_appended", out)

    def test_requires_action_without_baseline(self):
        with TempProject():
            os.environ.pop(c.BASELINE_ENV, None)
            rc, _, err = _check()
            self.assertEqual(rc, 2)
            self.assertIn("pass --action", err)

    def test_json(self):
        with TempProject() as p:
            _set_project(p.root, state="plan")
            rc, out, _ = _check("--action", "plan", "--json")
            self.assertEqual(rc, 1)
            payload = json.loads(out)
            self.assertFalse(payload["ok"])
            self.assertFalse(payload["baseline"])


# ---------------------------------------------------------------------------
# Prompt section
# ---------------------------------------------------------------------------


class TestPromptSection(unittest.TestCase):
    def test_render_markdown_per_action(self):
        plan = c.render_markdown("plan", 2, "build")
        self.assertIn("**You may change:** nothing outside `.state/`.", plan)
        self.assertIn("`project.json.state` is `tests`", plan)
        self.assertIn("`i2c check --action plan`", plan)
        self.assertNotIn("Build phases", c.render_markdown("plan", 2, "refine"))
        tests = c.render_markdown("tests", 7, "build")
        self.assertIn("`tests/acceptance/phase_7/**`", tests)
        self.assertIn("which this action owns", tests)
        close = c.render_markdown("close", 3, "build")
        self.assertIn("`audit_boundary`", close)
        self.assertIn("phase 3 is marked `complete`", close)
        # CLOSE never escalates via state (close.md: stop with EXIT 2).
        self.assertNotIn("audit_escalation", close)
        self.assertIn("stop and emit `EXIT: 2`", close)
        self.assertIn("set `state=audit_escalation`", plan)

    def test_assembled_prompt_carries_contract_and_next_state(self):
        import argparse
        with TempProject() as p:
            _set_project(p.root, state="plan")
            ns = argparse.Namespace(action="plan", section=None, phase=2, mode=None,
                                    module=None, backend="claude")
            ctx = ac.build_context(ns)
            self.assertEqual(ac.render_next_state(ctx), "## Next State: tests")
            self.assertIn("## Action Contract", ac.render_action_contract(ctx))
            ctx.action = "close"
            self.assertEqual(ac.render_next_state(ctx), "## Next State: audit_boundary")


if __name__ == "__main__":
    unittest.main()

"""examples/bench_calc: the hidden grader is consistent with the spec, a fresh
project comes up dispatch-ready, and grade.py summarises telemetry."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from i2c import config as cfg
from i2c import control

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "bench_calc"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"bench_calc_{name}", FIXTURE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grade = _load("grade")
new_project = _load("new_project")
sweep = _load("sweep")

try:
    import pytest  # noqa: F401
    HAVE_PYTEST = True
except ImportError:
    HAVE_PYTEST = False


@unittest.skipUnless(HAVE_PYTEST, "pytest is needed to run the reference suite")
class TestReferenceGrader(unittest.TestCase):
    def test_reference_implementation_passes_every_case(self):
        r = grade.run_reference(FIXTURE / "reference" / "calc_reference.py")
        self.assertTrue(r["ran"])
        self.assertEqual((r["passed"], r["failed"], r["errors"]), (65, 0, 0), msg=r["detail"])

    def test_seed_code_fails(self):
        r = grade.run_reference(FIXTURE / "seed" / "calc.py")
        self.assertNotEqual(r["returncode"], 0)
        self.assertLess(r["passed"], 65)

    def test_missing_calc(self):
        r = grade.run_reference(FIXTURE / "nope.py")
        self.assertFalse(r["ran"])


@unittest.skipUnless(shutil.which("git"), "git is needed to create a project")
class TestNewProject(unittest.TestCase):
    def test_fresh_project_is_dispatch_ready_at_plan(self):
        with tempfile.TemporaryDirectory(prefix="bench_calc_") as tmp:
            dest = Path(tmp) / "calc-run"
            new_project.create(dest, backend="pidev", model="some/model", name="calc-run")

            self.assertEqual((dest / "calc.py").read_text(encoding="utf-8"),
                             (FIXTURE / "seed" / "calc.py").read_text(encoding="utf-8"))
            self.assertTrue((dest / "PIDEV.md").is_file())
            # The hidden grader must never reach the worker.
            self.assertEqual(list(dest.rglob("test_reference.py")), [])
            self.assertEqual(list(dest.rglob("calc_reference.py")), [])

            run = cfg.load_run_config(dest)
            self.assertEqual((run.backend, run.model, run.backends), ("pidev", "some/model", {}))

            dispatch = control.next_action(dest)
            self.assertEqual((dispatch.action, dispatch.next_state), ("PLAN", "tests"))
            rep = control.readiness(dest)
            self.assertTrue(rep.ready(), msg=[(f.name, f.status, f.detail) for f in rep.findings])

            def git(*a):
                return subprocess.run(["git", "-c", f"safe.directory={dest.as_posix()}", *a],
                                      cwd=dest, capture_output=True, text=True).stdout
            self.assertEqual(len(git("log", "--oneline").splitlines()), 1)
            self.assertEqual(git("status", "--porcelain").strip(), "")

    def test_refuses_non_empty_destination(self):
        with tempfile.TemporaryDirectory(prefix="bench_calc_") as tmp:
            (Path(tmp) / "x").write_text("keep me", encoding="utf-8")
            with self.assertRaises(SystemExit):
                new_project.create(Path(tmp), backend="pidev", model=None, name="x")


class TestGradeSummary(unittest.TestCase):
    def test_summarises_telemetry(self):
        rows = [
            {"iteration": 1, "action": "plan", "model": "m", "exit_code": 0,
             "tokens_in": 100, "tokens_out": 10, "cost_usd": 0.01, "wall_clock_s": 5.0,
             "tool_calls": 3, "contract_violation": None},
            {"iteration": 2, "action": "tests", "model": "m", "exit_code": 2,
             "tokens_in": 50, "tokens_out": 5, "cost_usd": None, "wall_clock_s": 2.5,
             "tool_calls": 1, "contract_violation": ["post-TESTS invariant: x"]},
        ]
        with tempfile.TemporaryDirectory(prefix="bench_calc_") as tmp:
            st = Path(tmp) / ".state"
            st.mkdir()
            (st / "project.json").write_text(json.dumps({"phase": 1, "state": "tests"}),
                                             encoding="utf-8")
            (st / "telemetry.jsonl").write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            s = grade.summarise(Path(tmp))
        self.assertEqual(s["iterations"], 2)
        self.assertFalse(s["reached_boundary"])
        self.assertEqual(s["exits"], {"0": 1, "2": 1})
        self.assertEqual(s["contract_violations"], 1)
        self.assertEqual((s["tokens_in"], s["tokens_out"]), (150, 15))
        self.assertAlmostEqual(s["cost_usd"], 0.01)
        self.assertEqual(s["wall_clock_s"], 7.5)


def _project_at(root: Path, state: str) -> Path:
    (root / ".state").mkdir(parents=True, exist_ok=True)
    _set_state(root, state)
    return root


def _set_state(root: Path, state: str) -> None:
    (root / ".state" / "project.json").write_text(
        json.dumps({"phase": 1, "state": state}), encoding="utf-8")


class TestSweepDrive(unittest.TestCase):
    """The per-run loop, with a stand-in for `i2c run`."""

    def _runner(self, root: Path, states: list[str], rcs: list[int] | None = None):
        calls = []

        def run_once() -> int:
            i = len(calls)
            calls.append(i)
            _set_state(root, states[min(i, len(states) - 1)])
            return (rcs or [0] * 99)[i]
        return run_once, calls

    def test_stops_at_the_phase_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_at(Path(tmp), "plan")
            run_once, calls = self._runner(root, ["tests", "execute", "review", "close",
                                                  "audit_boundary"])
            d = sweep.drive(root, max_iterations=12, run_once=run_once)
        self.assertEqual((d["stop_reason"], d["iterations_run"], len(calls)),
                         ("audit_boundary", 5, 5))

    def test_stops_on_first_non_zero_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_at(Path(tmp), "plan")
            run_once, calls = self._runner(root, ["tests", "tests"], rcs=[0, 2])
            d = sweep.drive(root, max_iterations=12, run_once=run_once)
        self.assertEqual((d["stop_reason"], d["iterations_run"], d["last_rc"]), ("exit 2", 2, 2))

    def test_iteration_cap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_at(Path(tmp), "plan")
            run_once, _ = self._runner(root, ["execute"])
            d = sweep.drive(root, max_iterations=3, run_once=run_once)
        self.assertEqual((d["stop_reason"], d["iterations_run"]), ("max iterations", 3))

    def test_cap_reached_exactly_at_the_gate_counts_as_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_at(Path(tmp), "plan")
            run_once, _ = self._runner(root, ["tests", "audit_boundary"])
            d = sweep.drive(root, max_iterations=2, run_once=run_once)
        self.assertEqual(d["stop_reason"], "audit_boundary")

    def test_budget_stops_before_the_next_iteration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _project_at(Path(tmp), "plan")
            run_once, calls = self._runner(root, ["tests"])
            d = sweep.drive(root, max_iterations=12, run_once=run_once,
                            over_budget=lambda: len(calls) >= 1)
        self.assertEqual((d["stop_reason"], d["iterations_run"]), ("budget", 1))


class TestSweepTables(unittest.TestCase):
    def _rec(self, model, run, *, passed, boundary, cost, failed=None, violations=0):
        return {"model": model, "run": run, "stop_reason": "audit_boundary" if boundary else "exit 2",
                "state": "audit_boundary" if boundary else "tests", "reached_boundary": boundary,
                "iterations": 8 if boundary else 2, "reference": {"passed": passed},
                "failed_action": failed, "contract_violations": violations,
                "tokens_in": 1000, "tokens_out": 100, "cost_usd": cost, "wall_clock_s": 60.0}

    def test_aggregate_and_markdown(self):
        records = [
            self._rec("a/model", 1, passed=65, boundary=True, cost=0.10),
            self._rec("a/model", 2, passed=0, boundary=False, cost=0.04, failed="tests",
                      violations=1),
            self._rec("b/model", 1, passed=65, boundary=True, cost=None),
            {"model": "b/model", "run": 2, "stop_reason": "budget (not started)", "skipped": True},
        ]
        agg = {a["model"]: a for a in sweep.aggregate(records)}
        a = agg["a/model"]
        self.assertEqual((a["runs"], a["reached_boundary"], a["reference_full"]), (2, 1, 1))
        self.assertEqual(a["reference_mean"], 32.5)
        self.assertAlmostEqual(a["cost_mean"], 0.07)
        self.assertEqual(a["failed_actions"], {"tests": 1})
        self.assertEqual(agg["b/model"]["runs"], 1)          # skipped run excluded
        self.assertIsNone(agg["b/model"]["cost_mean"])        # unknown cost stays unknown
        md = sweep.render_markdown(records)
        self.assertIn("| a/model | 2 | 1/2 | 32.5 | 1/2 |", md)
        self.assertIn("tests x1", md)
        self.assertIn("budget (not started)", md)

    def test_slug(self):
        self.assertEqual(sweep.slug("deepseek/deepseek-v4.1-flash"), "deepseek_deepseek-v4.1-flash")
        self.assertEqual(sweep.slug("x:free"), "x_free")

    def test_git_trust_env_appends_command_scope_safe_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            env = sweep.git_trust_env(p, {"PATH": "x"})
            self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
            self.assertEqual((env["GIT_CONFIG_KEY_0"], env["GIT_CONFIG_VALUE_0"]),
                             ("safe.directory", p.resolve().as_posix()))
            env2 = sweep.git_trust_env(p, {"GIT_CONFIG_COUNT": "2"})
            self.assertEqual((env2["GIT_CONFIG_COUNT"], env2["GIT_CONFIG_KEY_2"]),
                             ("3", "safe.directory"))


@unittest.skipUnless(HAVE_PYTEST and shutil.which("git"), "needs pytest and git")
class TestSweepEndToEnd(unittest.TestCase):
    """A real sweep (fresh projects via new_project) with `i2c run` faked: each
    fake iteration finishes the phase, writes a correct calc.py and costs $0.02."""

    def test_sweep_writes_tables_and_honours_the_budget(self):
        def fake_i2c_run(project: Path, log: Path) -> int:
            shutil.copy2(FIXTURE / "reference" / "calc_reference.py", project / "calc.py")
            _set_state(project, "audit_boundary")
            with (project / ".state" / "telemetry.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"iteration": 1, "action": "plan", "model": "fake/model",
                                    "exit_code": 0, "cost_usd": 0.02, "wall_clock_s": 1.0,
                                    "tokens_in": 10, "tokens_out": 1}) + "\n")
            return 0

        orig = sweep.i2c_run
        sweep.i2c_run = fake_i2c_run
        try:
            with tempfile.TemporaryDirectory(prefix="bench_sweep_") as tmp:
                out = Path(tmp) / "sweep"
                records = sweep.run_sweep(out, ["fake/model"], runs=2, backend="pidev",
                                          max_iterations=12, parallel=1, budget_usd=0.01)
                md = (out / "results.md").read_text(encoding="utf-8")
                saved = json.loads((out / "results.json").read_text(encoding="utf-8"))
                rebuilt = sweep.records_from_dir(out)
        finally:
            sweep.i2c_run = orig

        first, second = records
        self.assertEqual(first["stop_reason"], "audit_boundary")
        self.assertEqual(first["reference"]["passed"], 65)
        self.assertEqual(second["stop_reason"], "budget (not started)")  # $0.02 >= $0.01
        self.assertEqual(len(saved), 2)
        self.assertIn("| fake/model | 1 | 1/1 | 65 | 1/1 |", md)
        self.assertEqual([(r["model"], r["run"]) for r in rebuilt], [("fake/model", 1)])


if __name__ == "__main__":
    unittest.main()

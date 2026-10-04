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


if __name__ == "__main__":
    unittest.main()

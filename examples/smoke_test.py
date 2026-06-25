"""End-to-end smoke test for the i2c data foundation.

Copies examples/initial_state/.state/ into a temp directory and walks
through a realistic sequence of `i2c state` CLI calls (via
``python -m i2c.state``) — the kind a worker would make across one execute
step plus a phase close. Prints a transcript so the output is human-readable
and can be used to verify the tooling without a real project.

Exits 0 on success, non-zero on the first command that fails or produces an
unexpected state.

Run (requires `pip install -e .` so the `i2c` package is importable):
    python p:\\shared\\i2c\\examples\\smoke_test.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

I2C_ROOT = Path(__file__).resolve().parent.parent
INITIAL = Path(__file__).resolve().parent / "initial_state" / ".state"


def run(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "i2c.state", *args]
    print(f"\n$ i2c state {' '.join(args)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(f"  [stderr] {result.stderr.rstrip()}")
    return result


def assert_eq(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")
    print(f"  [OK] {label} = {expected!r}")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    if not INITIAL.exists():
        print(f"FAIL: initial state fixture missing at {INITIAL}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="i2c_smoke_") as tmp:
        work = Path(tmp) / ".state"
        shutil.copytree(INITIAL, work)
        print(f"Working state dir: {work}")
        project = work / "project.json"
        steps = work / "steps.json"
        phases = work / "phases.json"
        devlog = work / "devlog.jsonl"

        # --- 0. Baseline check ---
        print("\n--- 0. Baseline state ---")
        p = read_json(project)
        assert_eq(p["phase"], 2, "starting phase")
        assert_eq(p["state"], "execute", "starting state")
        assert_eq("blocked" not in p, True, "blocked field absent (dropped per DESIGN_state_lifecycle_v1)")
        s = read_json(steps)
        # Active step is the lowest-numbered pending step in the current phase
        # (in_progress dropped from schema; status is binary pending|complete).
        active = [x for x in s if x["phase"] == 2 and x["status"] == "pending"]
        assert_eq(len(active), 3, "pending step count in phase 2")
        assert_eq(min(x["step"] for x in active), 2, "next pending step number")

        # --- 1. Worker completes step 2.2 with commit ---
        print("\n--- 1. Mark step 2.2 complete ---")
        r = run("complete", str(steps), "--phase", "2", "--step", "2",
                "--commit", "9876abc")
        if r.returncode != 0:
            print("FAIL: complete step 2.2", file=sys.stderr)
            return 1
        step2 = next(x for x in read_json(steps) if x["step"] == 2 and x["phase"] == 2)
        assert_eq(step2["status"], "complete", "step 2.2 status")
        assert_eq(step2["commit"], "9876abc", "step 2.2 commit")

        # --- 2. Worker logs the step in devlog ---
        print("\n--- 2. Append devlog entry for step 2.2 ---")
        entry = {
            "phase": 2, "step": 2, "action": "execute",
            "outcome": "complete",
            "contracts": ["ARCH_event_store.md"],
            "summary": "Reader API with cursor-based iteration. 7 new tests pass.",
            "commit": "9876abc",
            "timestamp": "2026-06-04T04:30:00Z",
        }
        r = run("append", str(devlog), json.dumps(entry))
        if r.returncode != 0:
            print("FAIL: append devlog", file=sys.stderr)
            return 1
        lines = devlog.read_text().splitlines()
        assert_eq(len(lines), 5, "devlog line count")
        latest = json.loads(lines[-1])
        assert_eq(latest["step"], 2, "latest devlog step")

        # --- 3. Worker hits a problem; rejected by schema (validation gate) ---
        print("\n--- 3. Validation rejects a bad commit hash (typo) ---")
        r = run("complete", str(steps), "--phase", "2", "--step", "3",
                "--commit", "not-a-hash!")
        if r.returncode == 0:
            print("FAIL: bad commit hash should have been rejected", file=sys.stderr)
            return 1
        print("  [OK] bad commit rejected, exit code =", r.returncode)
        # Confirm file untouched.
        step3 = next(x for x in read_json(steps) if x["step"] == 3 and x["phase"] == 2)
        assert_eq(step3["status"], "pending", "step 2.3 unchanged")

        # --- 4. Worker promotes a learned gotcha to project.json ---
        print("\n--- 4. Append gotcha ---")
        r = run("append-gotcha", str(project),
                "Reader cursor must be reset after schema migration")
        if r.returncode != 0:
            print("FAIL: append-gotcha", file=sys.stderr)
            return 1
        p = read_json(project)
        assert_eq(len(p["gotchas"]), 2, "gotcha count after append")

        # --- 5. Worker marks phase 2 complete and transitions to close ---
        # Simulate end of phase: mark remaining steps complete, close the phase.
        print("\n--- 5. Close out phase 2 ---")
        for step_num, commit in [(3, "bbbb111"), (4, "cccc222")]:
            r = run("complete", str(steps), "--phase", "2", "--step", str(step_num),
                    "--commit", commit)
            if r.returncode != 0:
                return 1
        r = run("complete", str(phases), "--phase", "2")
        if r.returncode != 0:
            return 1
        phase2 = next(x for x in read_json(phases) if x["id"] == 2)
        assert_eq(phase2["status"], "complete", "phase 2 status")

        # --- 6. Worker transitions to audit_boundary at end of close ---
        # Per the lifecycle redesign (DESIGN_state_lifecycle_v1): CLOSE worker
        # always sets state=audit_boundary as its final act and never advances
        # `phase`. The human/wrapper later transitions out of audit_boundary by
        # setting `phase=N+1 state=plan` (advance) or `state=done` (terminus).
        print("\n--- 6. Transition project to audit_boundary (close worker's final write) ---")
        r = run("set", str(project), "state=audit_boundary")
        if r.returncode != 0:
            return 1
        p = read_json(project)
        assert_eq(p["state"], "audit_boundary", "project state")
        assert_eq(p["phase"], 2, "phase unchanged by close worker")

        # --- 7. Schema rejects invalid state value (the bug class i2c retires) ---
        print("\n--- 7. Validation rejects typo in state field ---")
        r = run("set", str(project), "state=excecute")
        if r.returncode == 0:
            print("FAIL: typo state value should have been rejected", file=sys.stderr)
            return 1
        print("  [OK] typo rejected, exit code =", r.returncode)
        p = read_json(project)
        assert_eq(p["state"], "audit_boundary", "state untouched by failed update")

        # --- 8. Full file validation (round-trip through the validate API) ---
        print("\n--- 8. Validate every state file end-to-end ---")
        from i2c import validate as v
        for fname in ["project.json", "phases.json", "steps.json", "decisions.json"]:
            v.validate_state_file(work / fname)
            print(f"  [OK] {fname} validates")
        v.validate_devlog_jsonl(devlog)
        print(f"  [OK] devlog.jsonl validates ({len(devlog.read_text().splitlines())} entries)")

        # --- 9. i2c status (control-backed snapshot) against the working state ---
        print("\n--- 9. Run i2c status ---")
        env = {**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, "-m", "i2c.cli", "status"],
            capture_output=True, text=True, cwd=str(work.parent), env=env,
        )
        if proc.returncode != 0:
            print(f"FAIL: i2c status (rc={proc.returncode})")
            print(f"  [stderr] {proc.stderr}")
            return 1
        for expected in ("Phase:", "State:", "Steps (phase"):
            if expected not in proc.stdout:
                print(f"FAIL: i2c status output missing {expected!r}", file=sys.stderr)
                return 1
        print("  [OK] status snapshot includes Phase, State, Steps")

        print("\n=== SMOKE TEST PASSED ===")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""End-to-end smoke test for the i2c data foundation.

Copies examples/initial_state/.state/ into a temp directory and walks
through a realistic sequence of state.py CLI calls — the kind a worker would
make across one execute step plus a phase close. Prints a transcript so the
output is human-readable and can be used to verify the tooling without a real
project.

Exits 0 on success, non-zero on the first command that fails or produces an
unexpected state.

Run:
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
STATE_PY = I2C_ROOT / "tools" / "state.py"
VALIDATE_PY = I2C_ROOT / "tools" / "validate.py"
ASSEMBLE_PY = I2C_ROOT / "tools" / "assemble_context.py"
INITIAL = Path(__file__).resolve().parent / "initial_state" / ".state"


def run(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(STATE_PY), *args]
    print(f"\n$ python state.py {' '.join(args)}")
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
        assert_eq(p["blocked"], False, "starting blocked")
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

        # --- 6. Worker transitions to close and sets blocked ---
        print("\n--- 6. Transition project to close + blocked=true ---")
        r = run("set", str(project), "state=close", "blocked=true", "phase=3")
        if r.returncode != 0:
            return 1
        p = read_json(project)
        assert_eq(p["state"], "close", "project state")
        assert_eq(p["blocked"], True, "project blocked")
        assert_eq(p["phase"], 3, "project phase advanced")

        # --- 7. Schema rejects invalid state value (the bug class i2c retires) ---
        print("\n--- 7. Validation rejects typo in state field ---")
        r = run("set", str(project), "state=excecute")
        if r.returncode == 0:
            print("FAIL: typo state value should have been rejected", file=sys.stderr)
            return 1
        print("  [OK] typo rejected, exit code =", r.returncode)
        p = read_json(project)
        assert_eq(p["state"], "close", "state untouched by failed update")

        # --- 8. Full file validation (round-trip through validate.py) ---
        print("\n--- 8. Validate every state file end-to-end ---")
        sys.path.insert(0, str(I2C_ROOT / "tools"))
        import validate as v
        for fname in ["project.json", "phases.json", "steps.json", "decisions.json"]:
            v.validate_state_file(work / fname)
            print(f"  [OK] {fname} validates")
        v.validate_devlog_jsonl(devlog)
        print(f"  [OK] devlog.jsonl validates ({len(devlog.read_text().splitlines())} entries)")

        # --- 9. Assembler --section status against the working state ---
        print("\n--- 9. Run assemble_context.py --section status ---")
        env = {**dict(__import__("os").environ), "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run(
            [sys.executable, str(ASSEMBLE_PY), "--section", "status"],
            capture_output=True, text=True, cwd=str(work.parent), env=env,
        )
        if proc.returncode != 0:
            print(f"FAIL: assembler --section status (rc={proc.returncode})")
            print(f"  [stderr] {proc.stderr}")
            return 1
        for expected in ("## Project Status", "## Current Phase Steps", "## Gotchas"):
            if expected not in proc.stdout:
                print(f"FAIL: assembler output missing {expected!r}", file=sys.stderr)
                return 1
        print("  [OK] status snapshot includes Project Status, Current Phase Steps, Gotchas")

        print("\n=== SMOKE TEST PASSED ===")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

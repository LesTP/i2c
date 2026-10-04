"""Grade a bench-calc project: hidden reference suite + loop summary.

    python examples/bench_calc/grade.py <project> [--json]

Runs reference/test_reference.py against the project's calc.py in a temp
directory (nothing is written to the project), and summarises
.state/telemetry.jsonl: iterations, models, exits, contract violations,
tokens, cost. Standard library only, apart from pytest for the suite.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
REFERENCE = HERE / "reference" / "test_reference.py"


def run_reference(calc_py: Path) -> dict:
    """Run the hidden suite against ``calc_py``; return counts + pytest's tail."""
    if not calc_py.is_file():
        return {"ran": False, "passed": 0, "failed": 0, "errors": 0,
                "detail": f"{calc_py} not found"}
    with tempfile.TemporaryDirectory(prefix="bench_calc_grade_") as tmp:
        shutil.copy2(calc_py, Path(tmp) / "calc.py")
        shutil.copy2(REFERENCE, Path(tmp) / "test_reference.py")
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
             "test_reference.py"],
            cwd=tmp, capture_output=True, text=True,
        )
    tail = (proc.stdout + proc.stderr).strip().splitlines()[-1:] or [""]
    counts = {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|error)", tail[0])}
    return {"ran": True, "passed": counts.get("passed", 0),
            "failed": counts.get("failed", 0), "errors": counts.get("error", 0),
            "returncode": proc.returncode, "detail": tail[0]}


def summarise(project: Path) -> dict:
    st = project / ".state"
    try:
        state = json.loads((st / "project.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    rows = []
    tele = st / "telemetry.jsonl"
    if tele.is_file():
        rows = [json.loads(ln) for ln in tele.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    return {
        "phase": state.get("phase"),
        "state": state.get("state"),
        "reached_boundary": state.get("state") == "audit_boundary",
        "iterations": len(rows),
        "exits": dict(Counter(str(r.get("exit_code")) for r in rows)),
        "models": dict(Counter(f"{r.get('action')}:{r.get('model')}" for r in rows)),
        "contract_violations": sum(1 for r in rows if r.get("contract_violation")),
        "tokens_in": sum(r.get("tokens_in") or 0 for r in rows),
        "tokens_out": sum(r.get("tokens_out") or 0 for r in rows),
        "cost_usd": round(sum(costs), 6) if costs else None,
        "wall_clock_s": round(sum(r.get("wall_clock_s") or 0 for r in rows), 1),
        "per_iteration": [
            {k: r.get(k) for k in ("iteration", "action", "step", "model", "exit_code",
                                   "cost_usd", "tool_calls", "contract_violation")}
            for r in rows
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("project", type=Path)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    project = args.project.resolve()
    result = {"project": str(project), "reference": run_reference(project / "calc.py"),
              "loop": summarise(project)}
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    ref, loop = result["reference"], result["loop"]
    print(f"project:   {project}")
    print(f"reference: {ref['passed']} passed, {ref['failed']} failed, "
          f"{ref['errors']} errors  ({ref['detail']})")
    print(f"loop:      phase {loop['phase']} / {loop['state']} "
          f"(boundary reached: {loop['reached_boundary']}), "
          f"{loop['iterations']} iterations, exits {loop['exits']}, "
          f"contract violations {loop['contract_violations']}")
    cost = "unknown" if loop["cost_usd"] is None else f"${loop['cost_usd']:.4f}"
    print(f"usage:     {loop['tokens_in']} in / {loop['tokens_out']} out tokens, "
          f"cost {cost}, {loop['wall_clock_s']} s")
    for it in loop["per_iteration"]:
        print(f"  #{it['iteration']} {it['action']:<8} {str(it['model']):<36} "
              f"exit={it['exit_code']} tools={it['tool_calls']} "
              f"{'VIOLATION ' if it['contract_violation'] else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

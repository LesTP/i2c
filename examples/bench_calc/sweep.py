"""Sweep models over the bench-calc task: k fresh runs per model, one table.

    python examples/bench_calc/sweep.py --out ~/workspace/bench/sweep-1 \
        --models deepseek/deepseek-v4.1-flash,qwen/qwen3-coder --runs 2 \
        [--backend pidev] [--max-iterations 12] [--parallel 2] [--budget-usd 3]
    python examples/bench_calc/sweep.py --out ~/workspace/bench/sweep-1 --report-only

Each run is a new project (new_project.py) driven with `i2c run` until it
reaches a halt state (normally audit_boundary), exits non-zero, hits
--max-iterations, or the sweep's spend reaches --budget-usd. Then grade.py
scores calc.py against the hidden reference suite and summarises telemetry.
Results land in <out>/results.md and <out>/results.json; each run's project
(with its per-step event logs under logs/loop/) and console log stay in <out>
for troubleshooting.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
HALT_STATES = ("audit_boundary", "audit_escalation", "done")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"bench_calc_{name}", HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


grade = _load("grade")
new_project = _load("new_project")


def slug(model: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model)


def read_state(project: Path) -> str | None:
    try:
        return json.loads((project / ".state" / "project.json").read_text(encoding="utf-8")).get("state")
    except (OSError, ValueError):
        return None


def project_cost(project: Path) -> float:
    return grade.summarise(project).get("cost_usd") or 0.0


def git_trust_env(project: Path, base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment that makes git trust ``project`` for this process tree only.

    Run folders on a network/NTFS mount trip git's dubious-ownership check, and
    the runner's commits would fail. Command-scope config (GIT_CONFIG_COUNT) is
    honoured for safe.directory, so nothing is added to the global git config.
    """
    env = dict(os.environ if base is None else base)
    n = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
    env["GIT_CONFIG_COUNT"] = str(n + 1)
    env[f"GIT_CONFIG_KEY_{n}"] = "safe.directory"
    env[f"GIT_CONFIG_VALUE_{n}"] = project.resolve().as_posix()
    return env


def i2c_run(project: Path, log: Path) -> int:
    """One `i2c run` iteration; console output is appended to ``log``."""
    proc = subprocess.run([sys.executable, "-m", "i2c.cli", "run"], cwd=project,
                          env=git_trust_env(project),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    with log.open("a", encoding="utf-8") as f:
        f.write(proc.stdout + proc.stderr)
    return proc.returncode


def drive(project: Path, *, max_iterations: int, run_once: Callable[[], int],
          over_budget: Callable[[], bool] = lambda: False) -> dict:
    """Run iterations until a halt state, a non-zero exit, the cap, or the budget."""
    iterations = 0
    rc = 0
    reason = "max iterations"
    while iterations < max_iterations:
        state = read_state(project)
        if state in HALT_STATES:
            reason = state
            break
        if over_budget():
            reason = "budget"
            break
        rc = run_once()
        iterations += 1
        if rc != 0:
            reason = f"exit {rc}"
            break
    else:
        state = read_state(project)
        if state in HALT_STATES:
            reason = state
    return {"iterations_run": iterations, "stop_reason": reason, "last_rc": rc}


def record_for(project: Path, *, model: str, run: int, drove: dict | None = None) -> dict:
    loop = grade.summarise(project)
    failed = next((it for it in loop["per_iteration"] if it.get("exit_code") not in (0, None)), None)
    return {
        "model": model, "run": run, "project": str(project),
        **(drove or {}),
        "reference": grade.run_reference(project / "calc.py"),
        "failed_action": failed["action"] if failed else None,
        **{k: loop[k] for k in ("state", "reached_boundary", "iterations", "exits",
                                "contract_violations", "tokens_in", "tokens_out",
                                "cost_usd", "wall_clock_s", "per_iteration")},
    }


def run_sweep(out: Path, models: list[str], *, runs: int, backend: str,
              max_iterations: int, parallel: int, budget_usd: float | None) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(m, r) for m in models for r in range(1, runs + 1)]
    lock = threading.Lock()

    def spent() -> float:
        with lock:
            return sum(project_cost(p) for p in out.iterdir()
                       if (p / ".state" / "project.json").is_file())

    def over_budget() -> bool:
        return budget_usd is not None and spent() >= budget_usd

    def one(job: tuple[str, int]) -> dict:
        model, run = job
        project = out / f"{slug(model)}-r{run}"
        log = out / f"{slug(model)}-r{run}.log"
        if over_budget():
            return {"model": model, "run": run, "project": str(project),
                    "stop_reason": "budget (not started)", "skipped": True}
        try:
            new_project.create(project, backend=backend, model=model, name=project.name)
        except (Exception, SystemExit) as e:  # noqa: BLE001 - one bad run must not stop the sweep
            print(f"[error] {model} run {run}: setup failed: {e}", flush=True)
            return {"model": model, "run": run, "project": str(project),
                    "stop_reason": f"setup failed: {e}", "skipped": True}
        print(f"[start] {model} run {run} -> {project}", flush=True)
        drove = drive(project, max_iterations=max_iterations,
                      run_once=lambda: i2c_run(project, log), over_budget=over_budget)
        rec = record_for(project, model=model, run=run, drove=drove)
        ref = rec["reference"]
        print(f"[done]  {model} run {run}: {drove['stop_reason']}, "
              f"{rec['iterations']} iterations, reference {ref['passed']}/65, "
              f"cost {rec['cost_usd']}", flush=True)
        return rec

    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        records = list(pool.map(one, jobs))
    write_report(out, records)
    return records


def records_from_dir(out: Path) -> list[dict]:
    """Rebuild run records from the project folders in ``out`` (--report-only)."""
    recs = []
    for p in sorted(out.iterdir()):
        m = re.fullmatch(r"(.+)-r(\d+)", p.name)
        if not m or not (p / ".state" / "project.json").is_file():
            continue
        toml = (p / "i2c.toml").read_text(encoding="utf-8") if (p / "i2c.toml").is_file() else ""
        mm = re.search(r'^model = "([^"]+)"', toml, re.M)
        recs.append(record_for(p, model=mm[1] if mm else m[1], run=int(m[2])))
    return recs


def _mean(xs: list[float]) -> float | None:
    return round(statistics.mean(xs), 4) if xs else None


def aggregate(records: list[dict]) -> list[dict]:
    rows = []
    for model in dict.fromkeys(r["model"] for r in records):
        rs = [r for r in records if r["model"] == model and not r.get("skipped")]
        if not rs:
            continue
        rows.append({
            "model": model,
            "runs": len(rs),
            "reached_boundary": sum(1 for r in rs if r.get("reached_boundary")),
            "reference_mean": _mean([r["reference"]["passed"] for r in rs]),
            "reference_full": sum(1 for r in rs if r["reference"]["passed"] == 65),
            "iterations_mean": _mean([r["iterations"] for r in rs]),
            "cost_mean": _mean([r["cost_usd"] for r in rs if r.get("cost_usd") is not None]),
            "wall_mean_s": _mean([r["wall_clock_s"] for r in rs]),
            "contract_violations": sum(r["contract_violations"] for r in rs),
            "failed_actions": dict(Counter(r["failed_action"] for r in rs if r["failed_action"])),
        })
    return rows


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def render_markdown(records: list[dict]) -> str:
    out = ["# bench_calc sweep", "", "## Per model", "",
           "| Model | Runs | Reached gate | Reference mean /65 | Reference 65/65 | Iterations | Cost mean | Time mean (s) | Contract violations | Failed at |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for a in aggregate(records):
        failed = ", ".join(f"{k} x{v}" for k, v in a["failed_actions"].items()) or "-"
        cost = "-" if a["cost_mean"] is None else f"${a['cost_mean']:.4f}"
        out.append(f"| {a['model']} | {a['runs']} | {a['reached_boundary']}/{a['runs']} | "
                   f"{_fmt(a['reference_mean'])} | {a['reference_full']}/{a['runs']} | "
                   f"{_fmt(a['iterations_mean'])} | {cost} | {_fmt(a['wall_mean_s'])} | "
                   f"{a['contract_violations']} | {failed} |")
    out += ["", "## Per run", "",
            "| Model | Run | Stopped | State | Iterations | Reference | Failed at | Violations | Tokens in/out | Cost | Time (s) |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        if r.get("skipped"):
            out.append(f"| {r['model']} | {r['run']} | {r['stop_reason']} | - | - | - | - | - | - | - | - |")
            continue
        ref = r["reference"]
        cost = "-" if r.get("cost_usd") is None else f"${r['cost_usd']:.4f}"
        out.append(f"| {r['model']} | {r['run']} | {_fmt(r.get('stop_reason'))} | {_fmt(r['state'])} | "
                   f"{r['iterations']} | {ref['passed']}/65 | {_fmt(r['failed_action'])} | "
                   f"{r['contract_violations']} | {r['tokens_in']}/{r['tokens_out']} | {cost} | "
                   f"{r['wall_clock_s']} |")
    return "\n".join(out) + "\n"


def write_report(out: Path, records: list[dict]) -> None:
    (out / "results.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    (out / "results.md").write_text(render_markdown(records), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--out", type=Path, required=True, help="Sweep folder (one project per run).")
    p.add_argument("--models", help="Comma-separated model ids, each used for every action.")
    p.add_argument("--runs", type=int, default=1, help="Fresh runs per model (default 1).")
    p.add_argument("--backend", default="pidev", choices=("claude", "codex", "pidev"))
    p.add_argument("--max-iterations", type=int, default=12,
                   help="Per-run cap on `i2c run` calls (a clean phase takes about 8).")
    p.add_argument("--parallel", type=int, default=1, help="Runs in flight at once (default 1).")
    p.add_argument("--budget-usd", type=float,
                   help="Stop starting iterations once the sweep's recorded cost reaches this.")
    p.add_argument("--report-only", action="store_true",
                   help="Rebuild results.md/json from the runs already in --out.")
    args = p.parse_args(argv)
    out = args.out.expanduser().resolve()

    if args.report_only:
        records = records_from_dir(out)
        write_report(out, records)
    else:
        if not args.models:
            p.error("--models is required unless --report-only")
        if args.backend == "pidev" and not os.environ.get("OPENROUTER_API_KEY"):
            p.error("OPENROUTER_API_KEY is not set (pidev needs it in the environment)")
        models = [m.strip() for m in args.models.split(",") if m.strip()]
        records = run_sweep(out, models, runs=args.runs, backend=args.backend,
                            max_iterations=args.max_iterations, parallel=args.parallel,
                            budget_usd=args.budget_usd)
    print(f"\nwrote {out / 'results.md'} and results.json ({len(records)} runs)")
    print(render_markdown(records).split("## Per run")[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

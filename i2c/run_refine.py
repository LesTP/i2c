"""i2c refine loop — single-shot, sub-phase dispatch (DESIGN_refine_v1.md §12).

``i2c refine <fu-id>`` resolves one open backlog item from
``.state/followups.json``, assembles a *refine* prompt (WORKER_SPEC + adapter Tool
Rules + ``instructions/refine.md`` + the FU record + its declared files — no
phase/steps/decisions context), invokes the backend once, and — on ``EXIT:0`` —
closes the FU and commits ``refine(<kind>): <fu-id> <summary>``.

It deliberately **bypasses the state machine**: there is no lifecycle to advance
(the action is fixed as "refine this FU"). This is the same out-of-band shape as
the recovery ``diagnose``/``reconcile`` dispatch, and it reuses the run_iteration
pipeline wholesale — the backend invokers, the ``--emit`` system/user split, the
exit-signal parser, the worker-scoped git helpers, and the telemetry sidecar.

Sub-phase discipline is enforced structurally (Q-B2): before closing/committing,
``invariants.check_post_refine`` hard-asserts that ``project.json`` /
``phases.json`` / ``steps.json`` were byte-unchanged and that the worker appended
a ``devlog.jsonl`` row with ``action="refine"``. A violation halts (exit 2) and
nothing is closed or committed.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from i2c import assemble_context as ac
from i2c import config as cfg
from i2c import control
from i2c import invariants
from i2c import telemetry as tel
from i2c.run_iteration import (
    DEFAULT_MAX_BUDGET_USD,
    DEFAULT_MODEL,
    LOG_DIR_NAME,
    RunnerError,
    _last_devlog,
    _worker_dirty_paths,
    assemble_prompt,
    detect_rate_limit,
    invoke_claude,
    invoke_codex,
    next_iteration_number,
    parse_claude_output,
    parse_codex_usage,
    parse_exit_signal,
    validate_exit_signal,
    write_summary_line,
)

REFINE_ACTION = "refine"


# ---------------------------------------------------------------------------
# Runner-owned refine commit (FU-40 direction, extended to the refine tier)
# ---------------------------------------------------------------------------


def commit_refine(
    root: Path, *, fu_id: str, kind: str, summary: str, pre_dirty: set[str],
) -> tuple[bool, str | None, str]:
    """Commit the refine result as ``refine(<kind>): <fu-id> <summary>`` (Q-refine-5).

    Scoped to what the run produced: the worker's newly-dirtied code (current
    non-``.state`` dirty set minus ``pre_dirty``, so operator WIP is fenced off)
    plus the refine ``.state`` writes — the ``devlog.jsonl`` row and the closed
    ``followups.json``. Never commits ``project.json`` / ``phases.json`` /
    ``steps.json`` (the invariant already asserts those are unchanged). Returns
    ``(committed, short_hash, note)``. Best-effort; never raises.
    """
    try:
        changed = sorted(_worker_dirty_paths(root) - pre_dirty)
        # The refine .state writes: the worker's devlog row + the runner's FU
        # close. Staged wholesale (like commit_state at CLOSE) — `.state/` is the
        # runner's to manage, so any uncommitted refine .state is folded in.
        state_paths = [".state/devlog.jsonl", ".state/followups.json"]
        to_add = changed + [p for p in state_paths if (root / p).is_file()]
        if not to_add:
            return False, None, "nothing to commit"
        clean_summary = summary.replace("\n", " ").strip()
        msg = f"refine({kind}): {fu_id} {clean_summary}".strip()[:200]
        add = subprocess.run(
            ["git", "add", "--", *to_add],
            cwd=str(root), capture_output=True, text=True,
        )
        if add.returncode != 0:
            return False, None, "git add failed"
        cm = subprocess.run(
            ["git", "commit", "-m", msg, "--", *to_add],
            cwd=str(root), capture_output=True, text=True,
        )
        if cm.returncode != 0:
            detail = (cm.stderr or cm.stdout).strip().replace("\n", " ")[:200]
            return False, None, f"commit failed: {detail}"
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True,
        )
        short = rev.stdout.strip() if rev.returncode == 0 else None
        return True, short, msg
    except Exception as e:  # noqa: BLE001 - a commit failure must never break a run
        return False, None, f"error: {e}"


# ---------------------------------------------------------------------------
# Top-level refine driver
# ---------------------------------------------------------------------------


def run_refine(
    fu_id: str,
    *,
    backend: str | None = None,
    backend_map: dict[str, str] | None = None,
    default_backend: str = "claude",
    model: str,
    max_budget_usd: float,
    claude_invoker=invoke_claude,
    codex_invoker=invoke_codex,
    refine_committer=commit_refine,
) -> int:
    """Dispatch one refine worker against ``fu_id``. Returns the process exit code.

    0 = the worker made the change (FU closed, committed); 2 = missing/closed FU,
    an assembly/backend error, a malformed exit signal, a worker ``EXIT:2``, or a
    sub-phase invariant violation; 3 = backend rate-limited (retryable).
    """
    root = ac.find_project_root()

    # 1. Resolve the FU (open + exists) — the run_refine precondition.
    try:
        fu = control.resolve_followup(root, fu_id)
    except control.ControlError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    # 2. Resolve backend: explicit --backend > [run.backends].refine > default.
    backend = backend or (backend_map or {}).get(REFINE_ACTION, default_backend)

    # 3. Log paths.
    log_dir = root / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    iteration = next_iteration_number(log_dir)
    prompt_path = log_dir / f"iteration_{iteration:03d}_prompt.md"
    system_path = log_dir / f"iteration_{iteration:03d}_system.md"
    output_path = log_dir / f"iteration_{iteration:03d}.txt"
    jsonl_path = log_dir / f"iteration_{iteration:03d}.jsonl"

    # 4. Assemble the refine prompt (phase-less; the FU id selects the target).
    try:
        if backend == "claude":
            system_prompt = assemble_prompt(
                root, REFINE_ACTION, None, backend=backend, emit="system", fu=fu_id)
            stdin_prompt = assemble_prompt(
                root, REFINE_ACTION, None, backend=backend, emit="user", fu=fu_id)
        else:  # codex
            stdin_prompt = assemble_prompt(
                root, REFINE_ACTION, None, backend=backend, emit="full", fu=fu_id)
    except RunnerError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    prompt_path.write_text(stdin_prompt, encoding="utf-8")
    if backend == "claude":
        system_path.write_text(system_prompt, encoding="utf-8")

    # 5. Pre-invoke snapshots (telemetry + the sub-phase invariant).
    prompt_for_hash = (
        system_prompt + "\n" + stdin_prompt if backend == "claude" else stdin_prompt
    )
    start_commit = tel.head_commit(root)
    prev_devlog = tel.count_devlog_lines(root)
    pre_dirty = _worker_dirty_paths(root)
    pre_files = invariants.snapshot_phase_files(root)
    wall_start = time.monotonic()

    # 6. Invoke the backend once.
    jsonl_raw = ""
    try:
        if backend == "claude":
            worker_rc, captured = claude_invoker(
                stdin_prompt, cwd=root, model=model,
                max_budget_usd=max_budget_usd, system_prompt_file=system_path,
            )
        else:  # codex
            worker_rc, jsonl_raw, captured = codex_invoker(stdin_prompt, cwd=root)
            jsonl_path.write_text(jsonl_raw, encoding="utf-8")
    except FileNotFoundError as e:
        cli_name = e.filename or backend
        sys.stderr.write(
            f"ERROR: {backend} CLI not found on PATH (`{cli_name}`).\n"
        )
        return 2
    output_path.write_text(captured, encoding="utf-8")

    wall_clock_s = time.monotonic() - wall_start
    end_commit = tel.head_commit(root)

    # 7. Extract usage + parse the exit signal.
    if backend == "claude":
        signal_text, usage = parse_claude_output(captured)
    else:
        signal_text = captured
        usage = parse_codex_usage(jsonl_raw)

    signal = parse_exit_signal(signal_text)
    if signal is None:
        worker_exit = 2
        reason = "exit signal missing or malformed (2-line block not found)"
    else:
        errs = validate_exit_signal(signal)
        if errs:
            worker_exit = 2
            reason = f"exit signal failed schema validation: {'; '.join(errs)}"
        else:
            worker_exit = int(signal["exit_code"])
            reason = signal.get("reason", "(worker emitted no reason)")

    # Telemetry emit (best-effort, never fatal). action="refine"; carries fu/kind.
    model_used = model if backend == "claude" else None
    try:
        tele_cfg = cfg.load_telemetry_config(root)
    except cfg.ConfigError:
        tele_cfg = cfg.TelemetryConfig()
    try:
        pricing = tel.load_pricing(overrides=tele_cfg.pricing)
    except Exception:  # noqa: BLE001 - pricing is best-effort
        pricing = None

    def _emit_telemetry(final_exit: int) -> None:
        try:
            tel.record_iteration(
                root,
                iteration=iteration,
                phase=0,  # refine is phase-less
                action=REFINE_ACTION,
                backend=backend,
                model=model_used,
                usage=usage,
                exit_code=final_exit,
                wall_clock_s=wall_clock_s,
                start_commit=start_commit,
                end_commit=end_commit,
                prompt_text=prompt_for_hash,
                prev_devlog_count=prev_devlog,
                drift_flag=None,
                pricing=pricing,
                fu=fu_id,
                kind=fu.kind,
            )
        except Exception as e:  # noqa: BLE001 - telemetry must never break a run
            sys.stderr.write(f"NOTE: telemetry skipped ({e}).\n")

    # 7b. Backend rate-limit short-circuit (exit 3) — nothing landed.
    rl_reason = detect_rate_limit(
        backend, captured, jsonl_raw if backend == "codex" else None)
    if rl_reason is not None:
        line = write_summary_line(
            log_dir, iteration=iteration, backend=backend, action="REFINE",
            exit_code=3, reason=rl_reason, tokens=usage)
        sys.stdout.write(line + "\n")
        sys.stderr.write(f"ERROR: {rl_reason}\n")
        # Backend refused → nothing landed, so there is no worker exit_code. Emit
        # the telemetry row with exit_code=None (the schema enum is {0,2,null};
        # a literal 3 would be rejected and the row silently dropped).
        _emit_telemetry(None)
        return 3

    # 8. Worker error (EXIT:2 / malformed): leave the FU open, do not commit.
    if worker_exit != 0:
        line = write_summary_line(
            log_dir, iteration=iteration, backend=backend, action="REFINE",
            exit_code=2, reason=reason, tokens=usage)
        sys.stdout.write(line + "\n")
        sys.stderr.write(f"ERROR: refine {fu_id} exited 2: {reason}\n")
        _emit_telemetry(2)
        return 2

    # 9. Sub-phase invariant guard (Q-B2) — BEFORE close/commit, so a lifecycle
    #    violation or an unlogged run is surfaced and never committed.
    violations = invariants.check_post_refine(
        root, pre_files=pre_files, pre_devlog_count=prev_devlog)
    if violations:
        invariant_reason = "post-REFINE invariants failed: " + " | ".join(violations)
        line = write_summary_line(
            log_dir, iteration=iteration, backend=backend, action="REFINE",
            exit_code=2, reason=invariant_reason, tokens=usage)
        sys.stdout.write(line + "\n")
        sys.stderr.write(f"ERROR: {invariant_reason}\n")
        _emit_telemetry(2)
        return 2

    # 10. Close the FU (runner-owned, Q-B1) using the worker's reason. Broad
    #     except: the worker already did real (uncommitted) work, so an I/O error
    #     while closing must surface as a clean exit 2, never a traceback.
    try:
        control.close_followup(root, fu_id, resolution=reason)
    except Exception as e:  # noqa: BLE001 - close failure must not crash the run
        line = write_summary_line(
            log_dir, iteration=iteration, backend=backend, action="REFINE",
            exit_code=2, reason=f"fu close failed: {e}", tokens=usage)
        sys.stdout.write(line + "\n")
        sys.stderr.write(
            f"ERROR: could not close {fu_id}: {e}\n"
            f"NOTE: the worker's edits + refine devlog row remain UNCOMMITTED.\n")
        _emit_telemetry(2)
        return 2

    # 11. Commit the refine (code + the .state tail: devlog row + closed FU).
    entry = _last_devlog(root)
    summary = str(entry.get("summary", reason)) if entry else reason
    committed, _short, note = refine_committer(
        root, fu_id=fu_id, kind=fu.kind, summary=summary, pre_dirty=pre_dirty)
    if committed:
        sys.stdout.write(f"committed refine: {note}\n")
        end_commit = tel.head_commit(root)
    else:
        sys.stdout.write(f"NOTE: no refine commit ({note}).\n")

    line = write_summary_line(
        log_dir, iteration=iteration, backend=backend, action="REFINE",
        exit_code=0, reason=reason, tokens=usage)
    sys.stdout.write(line + "\n")
    _emit_telemetry(0)
    return 0


# ---------------------------------------------------------------------------
# CLI (operator path is `i2c refine`; this is the `python -m i2c.run_refine` form)
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_refine.py",
        description="Dispatch one refine worker against a followup (DESIGN_refine_v1 §12).",
    )
    parser.add_argument("fu_id", help="Followup id to refine, e.g. FU-42.")
    parser.add_argument("--backend", choices=("claude", "codex"), default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-budget-usd", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_cfg = cfg.load_run_config()
    except cfg.ConfigError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2
    model = args.model or run_cfg.model or DEFAULT_MODEL
    if args.max_budget_usd is not None:
        max_budget_usd = args.max_budget_usd
    elif run_cfg.max_budget_usd is not None:
        max_budget_usd = run_cfg.max_budget_usd
    else:
        max_budget_usd = DEFAULT_MAX_BUDGET_USD
    return run_refine(
        args.fu_id,
        backend=args.backend,
        backend_map=run_cfg.backends,
        default_backend=run_cfg.backend or "claude",
        model=model,
        max_budget_usd=max_budget_usd,
    )


if __name__ == "__main__":
    raise SystemExit(main())

"""i2c — operator-facing console dispatcher over ``i2c.control``.

A thin command-line surface: parse a subcommand, call exactly one ``control``
function, render the structured result for the terminal. Per
DESIGN_packaging_v1.md §7.1 the design rule is that **formatting lives in the
surface, not the core** — ``control`` returns dataclasses; this module turns
them into operator text (default) or JSON (``--json``). No business logic lives
here beyond flag-to-argument mapping and rendering.

Subcommands::

    i2c status                       # project snapshot
    i2c next-action                  # state-machine dispatch decision
    i2c phase-summary --phase N      # operator's boundary view of phase N
    i2c decisions [--phase N]        # decision records (optionally filtered)
    i2c devlog [--phase N]           # devlog entries (optionally filtered)
    i2c escalation [--phase N]       # current escalation signal
    i2c diagnose [--target N]        # diagnose a failed iteration (drift audit)
    i2c reconcile [--apply]          # apply deterministic workflow-drift fixes
    i2c logs [--iter N] [--limit N]  # iteration index, or one transcript via --iter
    i2c portfolio [--root PATH]       # cross-project 'which needs me?' view
    i2c dashboard [--root PATH] [--out FILE]  # self-contained HTML snapshot (--json for model)
    i2c serve telegram [--root PATH]  # run the Telegram surface (needs i2c[telegram])
    i2c clear-boundary [--terminate] # advance past / terminate an audit_boundary
    i2c run [--backend ...] ...      # one cold-start worker iteration
    i2c migrate [--check|--dry-run]  # upgrade .state/ to the current schema

This is the **minimal** Phase-2 CLI: it stays in the flat ``tools/`` layout and
is invoked as ``python tools/cli.py <subcommand>``. The importable ``i2c``
package + ``console_scripts`` entry point (so ``i2c …`` works after
``pip install``) and the ``i2c state`` worker tool-surface switch are separate,
later Phase-2 items. ``escalation`` / ``logs`` are the FU-34 projections.

Exit codes: ``0`` success; ``2`` on a ``ControlError`` (missing / invalid
``.state/``, unmet precondition) — surfaced as a structured ``ERROR:`` line on
stderr, never a traceback. ``run`` returns the runner's own exit code.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from typing import Any, Callable

# Sibling package modules.
from i2c import assemble_context
from i2c import config
from i2c import control
from i2c import migrate
from i2c import run_iteration
from i2c import scaffold
from i2c import state


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


def _to_jsonable(result: Any) -> Any:
    """Convert a control dataclass (or list of them) to JSON-serializable data."""
    if isinstance(result, list):
        return [asdict(item) for item in result]
    return asdict(result)


def _emit(result: Any, *, as_json: bool, renderer: Callable[[Any], str]) -> None:
    if as_json:
        sys.stdout.write(
            json.dumps(_to_jsonable(result), indent=2, ensure_ascii=False) + "\n"
        )
    else:
        sys.stdout.write(renderer(result) + "\n")


def _fail(error: Exception) -> int:
    sys.stderr.write(f"ERROR: {error}\n")
    return 2


# ---------------------------------------------------------------------------
# Text renderers — shared with other surfaces via i2c.render
# ---------------------------------------------------------------------------

from i2c.render import (  # noqa: E402
    _render_boundary,
    _render_decisions,
    _render_devlog_list,
    _render_diagnosis,
    _render_dispatch,
    _render_escalation,
    _render_followups,
    _render_followups_tables,
    _render_log_transcript,
    _render_logs,
    _render_phase_summary,
    _render_portfolio,
    _render_reconcile,
    _render_status,
)


# ---------------------------------------------------------------------------
# Subcommand handlers (each wraps exactly one control function)
# ---------------------------------------------------------------------------


def cmd_status(args: argparse.Namespace) -> int:
    try:
        report = control.status()
    except control.ControlError as e:
        return _fail(e)
    _emit(report, as_json=args.json, renderer=_render_status)
    return 0


def cmd_next_action(args: argparse.Namespace) -> int:
    try:
        dispatch = control.next_action()
    except control.ControlError as e:
        return _fail(e)
    _emit(dispatch, as_json=args.json, renderer=_render_dispatch)
    return 0


def cmd_phase_summary(args: argparse.Namespace) -> int:
    try:
        summary = control.phase_summary(phase=args.phase)
    except control.ControlError as e:
        return _fail(e)
    _emit(summary, as_json=args.json, renderer=_render_phase_summary)
    return 0


def cmd_decisions(args: argparse.Namespace) -> int:
    try:
        result = control.decisions(phase=args.phase)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_decisions)
    return 0


def cmd_devlog(args: argparse.Namespace) -> int:
    try:
        result = control.devlog(phase=args.phase)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_devlog_list)
    return 0


def cmd_escalation(args: argparse.Namespace) -> int:
    try:
        result = control.escalation(phase=args.phase)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_escalation)
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    try:
        if args.iter is not None:
            result: object = control.logs_transcript(iter=args.iter)
            renderer = _render_log_transcript
        else:
            result = control.logs(limit=args.limit)
            renderer = _render_logs
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=renderer)
    return 0


def cmd_diagnose(args: argparse.Namespace) -> int:
    try:
        result = control.diagnose(target=args.target)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_diagnosis)
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    try:
        result = control.reconcile(apply=args.apply)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_reconcile)
    return 0


def cmd_portfolio(args: argparse.Namespace) -> int:
    from pathlib import Path

    root = Path(args.root) if args.root else None
    try:
        report = control.portfolio(root=root)
    except control.ControlError as e:
        return _fail(e)
    _emit(report, as_json=args.json, renderer=_render_portfolio)
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from pathlib import Path

    from i2c import dashboard

    root = Path(args.root) if args.root else None
    try:
        model = control.dashboard_model(root=root)
    except control.ControlError as e:
        return _fail(e)
    # HTML is the primary output, so branch rather than route through `_emit`
    # (precedent: cmd_migrate). --json prints the allowlisted model instead.
    if args.json:
        sys.stdout.write(
            json.dumps(asdict(model), indent=2, ensure_ascii=False) + "\n"
        )
        return 0
    out = dashboard.write_html(model, Path(args.out))
    sys.stdout.write(f"wrote {out}\n")
    return 0


def cmd_clear_boundary(args: argparse.Namespace) -> int:
    try:
        result = control.clear_boundary(advance=not args.terminate)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_boundary)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # Resolve run settings. Backend precedence: --backend flag (forces a single
    # backend) > [run.backends][action] (per-action map, resolved in the runner)
    # > [run].backend > built-in claude. Model/budget: flag > i2c.toml > default.
    if args.target is not None and args.action is None:
        return _fail(
            ValueError("--target requires --action (diagnose/reconcile)")
        )
    try:
        cfg = config.load_run_config()
    except config.ConfigError as e:
        return _fail(e)
    model = args.model or cfg.model or run_iteration.DEFAULT_MODEL
    if args.max_budget_usd is not None:
        max_budget_usd = args.max_budget_usd
    elif cfg.max_budget_usd is not None:
        max_budget_usd = cfg.max_budget_usd
    else:
        max_budget_usd = run_iteration.DEFAULT_MAX_BUDGET_USD
    return control.run_iteration(
        backend=args.backend,
        backend_map=cfg.backends,
        default_backend=cfg.backend or "claude",
        model=model,
        max_budget_usd=max_budget_usd,
        action_override=args.action,
        target=args.target,
    )


def cmd_serve(args: argparse.Namespace) -> int:
    from pathlib import Path

    from i2c.surfaces import telegram as tg  # safe without the extra (lazy PTB import)

    root = Path(args.root) if args.root else None
    try:
        return tg.serve(root=root)
    except (tg.MissingDependency, tg.MissingToken) as e:
        return _fail(e)


def cmd_init(args: argparse.Namespace) -> int:
    from pathlib import Path

    root = Path.cwd()
    name = args.name or root.name
    backends = ("claude", "codex") if args.backend == "both" else (args.backend,)
    try:
        report = scaffold.init_project(
            root, name=name, backends=backends, force=args.force
        )
    except scaffold.ScaffoldError as e:
        return _fail(e)
    for line in report:
        sys.stdout.write(f"  {line}\n")
    sys.stdout.write(
        "\nNext steps: fill in PROJECT.md, ARCHITECTURE.md, and the adapter(s); "
        "add ARCH_<module>.md per module; then\n"
        "  i2c assemble --action plan --phase 1 --mode supervised\n"
    )
    return 0


def cmd_eject(args: argparse.Namespace) -> int:
    from pathlib import Path

    if args.list:
        sys.stdout.write("Ejectable assets:\n")
        for a in ("instructions", *scaffold.EJECTABLE):
            sys.stdout.write(f"  {a}\n")
        return 0
    if not args.asset:
        return _fail(scaffold.ScaffoldError("an asset is required (or use --list)"))
    try:
        written = scaffold.eject_asset(Path.cwd(), args.asset, force=args.force)
    except scaffold.ScaffoldError as e:
        return _fail(e)
    for path in written:
        sys.stdout.write(f"  ejected {path}\n")
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    try:
        root = control.find_project_root()
    except control.NotFoundError as e:
        return _fail(e)

    # --check and --dry-run both compute the plan without writing. migrate_project
    # raises MigrationError for a newer-than-current project (no forward step).
    if args.check or args.dry_run:
        try:
            result = migrate.migrate_project(root, dry_run=True)
        except migrate.MigrationError as e:
            return _fail(e)
        if args.check:
            if result.migrated:
                sys.stdout.write(
                    f"migration needed: schema v{result.from_version} -> "
                    f"v{result.to_version}\n"
                )
                return 1
            sys.stdout.write(f"up to date: schema v{result.to_version}\n")
            return 0
        # --dry-run
        if not result.migrated:
            sys.stdout.write(
                f"already at schema v{result.to_version}; nothing to do\n"
            )
            return 0
        sys.stdout.write(
            f"would migrate: schema v{result.from_version} -> v{result.to_version}\n"
        )
        for change in result.changes:
            sys.stdout.write(f"  - {change}\n")
        return 0

    try:
        result = migrate.migrate_project(root, dry_run=False)
    except migrate.MigrationError as e:
        return _fail(e)
    if not result.migrated:
        sys.stdout.write(f"already at schema v{result.to_version}\n")
        return 0
    sys.stdout.write(
        f"migrated: schema v{result.from_version} -> v{result.to_version}\n"
    )
    for change in result.changes:
        sys.stdout.write(f"  - {change}\n")
    return 0


def _render_import(report: Any) -> str:
    lines: list[str] = []
    header = "APPLIED" if report.applied else "DRY-RUN (no files written)"
    lines.append(f"i2c import — {header}")
    lines.append(f"  root: {report.root}")
    lines.append(f"  validation: {'ok' if report.validation_ok else 'FAILED'}")
    lines.append("  .state/ files:")
    for f in report.files:
        lines.append(f"    - {f}")
    if report.manual_review:
        lines.append("  manual review:")
        for m in report.manual_review:
            lines.append(f"    ! {m}")
    if report.warnings:
        lines.append("  notes:")
        for w in report.warnings:
            lines.append(f"    - {w}")
    if not report.applied:
        lines.append("  (re-run with --apply to write .state/)")
    return "\n".join(lines)


def _render_doctor(report: Any) -> str:
    marks = {"ok": "ok  ", "warn": "WARN", "fail": "FAIL"}
    lines = ["i2c doctor"]
    for c in report.checks:
        lines.append(f"  [{marks.get(c.status, c.status)}] {c.name}: {c.detail}")
        if c.remedy and c.status != "ok":
            lines.append(f"         -> {c.remedy}")
    lines.append("  " + ("all checks passed" if report.ok() else "FAILURES present"))
    return "\n".join(lines)


def cmd_doctor(args: argparse.Namespace) -> int:
    from i2c import doctor

    report = doctor.run_checks()
    _emit(report, as_json=args.json, renderer=_render_doctor)
    return 0 if report.ok() else 1


def cmd_import(args: argparse.Namespace) -> int:
    from pathlib import Path

    from i2c import import_e2e

    root = Path(args.path) if args.path else Path.cwd()
    try:
        report = import_e2e.import_project(
            root,
            apply=args.apply,
            port_history=args.port_history,
            force=args.force,
        )
    except import_e2e.ImportE2EError as e:
        return _fail(e)
    _emit(report, as_json=args.json, renderer=_render_import)
    return 0


# ---------------------------------------------------------------------------
# `fu` — refine backlog (Proposal A)
# ---------------------------------------------------------------------------


_FU_KINDS = (
    "prose", "dead-surface", "doc-reconciliation", "cli-ergonomics",
    "test-hardening", "structural-refactor", "experiment-log", "bugfix", "other",
)

_FU_HORIZONS = ("now", "next", "eventually", "icebox")


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [x.strip() for x in value.split(",") if x.strip()]


def cmd_fu_list(args: argparse.Namespace) -> int:
    try:
        result = control.followups(
            status=args.status, kind=args.kind, priority=args.priority,
        )
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_followups)
    return 0


def cmd_fu_show(args: argparse.Namespace) -> int:
    try:
        items = control.followups()
    except control.ControlError as e:
        return _fail(e)
    match = next((f for f in items if f.id == args.id), None)
    if match is None:
        return _fail(control.NotFoundError(f"no follow-up {args.id!r}"))
    _emit(match, as_json=args.json, renderer=lambda f: _render_followups([f]))
    return 0


def cmd_fu_render(args: argparse.Namespace) -> int:
    try:
        result = control.followups()
    except control.ControlError as e:
        return _fail(e)
    sys.stdout.write(_render_followups_tables(result) + "\n")
    return 0


def _fu_backlog_path():
    """Resolve the backlog file, creating an empty one if the project has a
    ``.state/`` but no ``followups.json`` yet. Raises ``control.NotFoundError``.
    """
    from pathlib import Path

    root = control._find_followups_root()
    path = Path(root) / ".state" / "followups.json"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[]\n", encoding="utf-8")
    return path


def _run_state_cmd(fn, ns: argparse.Namespace) -> int:
    """Call a ``state.cmd_*`` handler, suppressing its stdout 'OK:' line so the
    fu surface prints its own confirmation. Errors still reach stderr."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        return fn(ns)


def cmd_fu_add(args: argparse.Namespace) -> int:
    import datetime

    try:
        path = _fu_backlog_path()
        existing = control.followups(path.parent.parent)
    except control.ControlError as e:
        return _fail(e)
    nums = [
        int(f.id[3:]) for f in existing
        if f.id.startswith("FU-") and f.id[3:].isdigit()
    ]
    new_id = f"FU-{(max(nums) + 1) if nums else 1}"
    record: dict[str, Any] = {
        "id": new_id,
        "title": args.title,
        "kind": args.kind,
        "status": "open",
        "opened": datetime.date.today().isoformat(),
    }
    if args.context:
        record["context"] = args.context
    if args.trigger:
        record["trigger"] = args.trigger
    if args.priority:
        record["priority"] = args.priority
    files = _split_csv(args.files)
    refs = _split_csv(args.refs)
    if files:
        record["files"] = files
    if refs:
        record["refs"] = refs
    ns = argparse.Namespace(
        file=str(path), record=json.dumps(record), from_file=None
    )
    rc = _run_state_cmd(state.cmd_append_record, ns)
    if rc != 0:
        return rc
    sys.stdout.write(f"added {new_id}\n")
    return 0


def cmd_fu_close(args: argparse.Namespace) -> int:
    import datetime

    try:
        path = _fu_backlog_path()
    except control.ControlError as e:
        return _fail(e)
    updates = [
        f"status={args.status}",
        f"closed={datetime.date.today().isoformat()}",
    ]
    if args.resolution:
        updates.append(f"resolution={args.resolution}")
    ns = argparse.Namespace(
        file=str(path), match=f"id={args.id}", updates=updates, from_file=None
    )
    rc = _run_state_cmd(state.cmd_update_record, ns)
    if rc != 0:
        return rc
    sys.stdout.write(f"closed {args.id} ({args.status})\n")
    return 0


def cmd_fu_reopen(args: argparse.Namespace) -> int:
    try:
        path = _fu_backlog_path()
    except control.ControlError as e:
        return _fail(e)
    ns = argparse.Namespace(
        file=str(path), match=f"id={args.id}", updates=["status=open"],
        from_file=None,
    )
    rc = _run_state_cmd(state.cmd_update_record, ns)
    if rc != 0:
        return rc
    sys.stdout.write(f"reopened {args.id}\n")
    return 0


def cmd_fu_prioritize(args: argparse.Namespace) -> int:
    try:
        path = _fu_backlog_path()
    except control.ControlError as e:
        return _fail(e)
    ns = argparse.Namespace(
        file=str(path), match=f"id={args.id}",
        updates=[f"priority={args.priority}"], from_file=None,
    )
    rc = _run_state_cmd(state.cmd_update_record, ns)
    if rc != 0:
        return rc
    sys.stdout.write(f"prioritized {args.id} = {args.priority}\n")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    # Shared --json flag, attached to the structured-output subcommands via
    # `parents=` so usage reads naturally: `cli.py status --json`.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        help="Emit the structured result as JSON instead of operator text.",
    )

    parser = argparse.ArgumentParser(
        prog="i2c",
        description="Operator console over the i2c.control command API.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser(
        "status", parents=[json_parent], help="Project snapshot."
    )
    p_status.set_defaults(func=cmd_status)

    p_next = sub.add_parser(
        "next-action",
        parents=[json_parent],
        help="State-machine dispatch decision (ACTION / NEXT).",
    )
    p_next.set_defaults(func=cmd_next_action)

    p_ps = sub.add_parser(
        "phase-summary",
        parents=[json_parent],
        help="Operator's boundary view of one phase.",
    )
    p_ps.add_argument("--phase", type=int, required=True, help="Phase number.")
    p_ps.set_defaults(func=cmd_phase_summary)

    p_dec = sub.add_parser(
        "decisions",
        parents=[json_parent],
        help="Decision records, optionally filtered by phase.",
    )
    p_dec.add_argument(
        "--phase", type=int, default=None, help="Filter to decisions tagged this phase."
    )
    p_dec.set_defaults(func=cmd_decisions)

    p_devlog = sub.add_parser(
        "devlog",
        parents=[json_parent],
        help="Devlog entries, optionally filtered by phase.",
    )
    p_devlog.add_argument(
        "--phase", type=int, default=None, help="Filter to entries for this phase."
    )
    p_devlog.set_defaults(func=cmd_devlog)

    p_esc = sub.add_parser(
        "escalation",
        parents=[json_parent],
        help="Current escalation signal (audit_escalation + triggering devlog entry).",
    )
    p_esc.add_argument(
        "--phase", type=int, default=None, help="Phase to inspect (default: current)."
    )
    p_esc.set_defaults(func=cmd_escalation)

    p_diag = sub.add_parser(
        "diagnose",
        parents=[json_parent],
        help="Diagnose a failed/stuck iteration: run the drift audit and "
        "classify (workflow-drift / unknown / none). Read-only.",
    )
    p_diag.add_argument(
        "--target", type=int, default=None,
        help="Iteration to diagnose (default: latest in summary.log).",
    )
    p_diag.set_defaults(func=cmd_diagnose)

    p_rec = sub.add_parser(
        "reconcile",
        parents=[json_parent],
        help="Apply deterministic reconcile fixes for workflow drift "
        "(dry-run by default; --apply writes via i2c state).",
    )
    p_rec.add_argument(
        "--apply", action="store_true",
        help="Write the reconcile mutations (the human gate). Default: dry-run.",
    )
    p_rec.set_defaults(func=cmd_reconcile)

    p_logs = sub.add_parser(
        "logs",
        parents=[json_parent],
        help="Iteration index from logs/loop/summary.log, or one transcript via --iter.",
    )
    p_logs.add_argument(
        "--iter", type=int, default=None,
        help="Show this iteration's transcript instead of the index.",
    )
    p_logs.add_argument(
        "--limit", type=int, default=10,
        help="Index mode: keep the last N iterations (default 10).",
    )
    p_logs.set_defaults(func=cmd_logs)

    p_pf = sub.add_parser(
        "portfolio",
        parents=[json_parent],
        help="Cross-project view: every project under --root (default CWD), "
        "escalations/boundaries first.",
    )
    p_pf.add_argument(
        "--root", default=None,
        help="Parent folder to scan for projects. Default: current directory.",
    )
    p_pf.set_defaults(func=cmd_portfolio)

    p_dash = sub.add_parser(
        "dashboard",
        parents=[json_parent],
        help="Emit a self-contained HTML snapshot (portfolio by default, "
        "single-project when run inside a project). --json prints the model.",
    )
    p_dash.add_argument(
        "--root", default=None,
        help="Directory to render: a project dir shows that project; a parent "
        "folder shows the portfolio. Default: single-project when inside one, "
        "else portfolio of the current dir.",
    )
    p_dash.add_argument(
        "--out", default="dashboard.html",
        help="Output HTML path. Default: dashboard.html in the current dir.",
    )
    p_dash.set_defaults(func=cmd_dashboard)

    p_cb = sub.add_parser(
        "clear-boundary",
        parents=[json_parent],
        help="Clear an audit_boundary: advance to the next phase (default) "
        "or terminate.",
    )
    p_cb.add_argument(
        "--terminate",
        action="store_true",
        help="Terminate the project (state=done) instead of advancing.",
    )
    p_cb.set_defaults(func=cmd_clear_boundary)

    p_run = sub.add_parser(
        "run",
        help="Drive one cold-start worker iteration (delegates to run_iteration).",
    )
    p_run.add_argument(
        "--backend", choices=("claude", "codex"), default=None,
        help="Force a single backend for every action this run. Without it, "
        "the per-action [run.backends] map applies, then [run].backend, then "
        "claude.",
    )
    p_run.add_argument(
        "--model", default=None,
        help="Model passed to the backend. Precedence: this flag > i2c.toml "
        f"[run].model > {run_iteration.DEFAULT_MODEL}.",
    )
    p_run.add_argument(
        "--max-budget-usd", type=float, default=None,
        help="Cost cap (claude). Precedence: this flag > i2c.toml "
        f"[run].max_budget_usd > {run_iteration.DEFAULT_MAX_BUDGET_USD:.2f}.",
    )
    p_run.add_argument(
        "--action", choices=("diagnose", "reconcile"), default=None,
        help="Out-of-band recovery action dispatched against --target, bypassing "
        "the state machine. Omit for a normal state-machine-driven iteration.",
    )
    p_run.add_argument(
        "--target", type=int, default=None,
        help="Target iteration for the recovery --action (default: latest).",
    )
    p_run.set_defaults(func=cmd_run)

    p_serve = sub.add_parser(
        "serve",
        help="Run a chat surface over i2c.control (Telegram).",
    )
    p_serve.add_argument(
        "transport", choices=("telegram",), help="Which surface to run.",
    )
    p_serve.add_argument(
        "--root", default=None,
        help="Portfolio root to scan. Default: CWD (or [telegram].root in i2c.toml).",
    )
    p_serve.set_defaults(func=cmd_serve)

    p_init = sub.add_parser(
        "init", help="Scaffold a new i2c project in the current directory."
    )
    p_init.add_argument(
        "--name", default=None,
        help="Project name for templates. Default: current directory name.",
    )
    p_init.add_argument(
        "--backend", choices=("claude", "codex", "both"), default="both",
        help="Which adapter(s) to scaffold. Default: both.",
    )
    p_init.add_argument(
        "--force", action="store_true",
        help="Re-scaffold even if .state/project.json already exists.",
    )
    p_init.set_defaults(func=cmd_init)

    p_eject = sub.add_parser(
        "eject",
        help="Copy a packaged default (WORKER_SPEC.md / instructions) into the "
        "project for local override.",
    )
    p_eject.add_argument(
        "asset", nargs="?", default=None,
        help="Asset to eject: WORKER_SPEC.md, instructions/<action>.md, or "
        "'instructions' for all.",
    )
    p_eject.add_argument(
        "--list", action="store_true", help="List ejectable assets and exit.",
    )
    p_eject.add_argument(
        "--force", action="store_true", help="Overwrite an existing local copy.",
    )
    p_eject.set_defaults(func=cmd_eject)

    p_migrate = sub.add_parser(
        "migrate",
        help="Migrate this project's .state/ to the current framework schema.",
    )
    # --check and --dry-run are distinct contracts (a CI gate that can exit 1 vs.
    # an exit-0 preview); rather than silently pick one when both are passed,
    # let argparse reject the combination.
    migrate_mode = p_migrate.add_mutually_exclusive_group()
    migrate_mode.add_argument(
        "--check", action="store_true",
        help="Report whether a migration is needed; exit 1 if so, 0 if current "
        "(CI-friendly). A newer-than-current project exits 2. Never writes.",
    )
    migrate_mode.add_argument(
        "--dry-run", action="store_true",
        help="Report the changes that would be applied without writing.",
    )
    p_migrate.set_defaults(func=cmd_migrate)

    p_doctor = sub.add_parser(
        "doctor",
        parents=[json_parent],
        help="Check the i2c install/environment (PATH, deps, schemas, "
        "backends, project .state).",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_import = sub.add_parser(
        "import",
        parents=[json_parent],
        help="Migrate an e2e (prose-state) project to .state/ "
        "(dry-run by default).",
    )
    p_import.add_argument(
        "path", nargs="?", default=None,
        help="Project root to convert. Default: current directory.",
    )
    p_import.add_argument(
        "--apply", action="store_true",
        help="Write .state/ (default: dry-run, no files written).",
    )
    p_import.add_argument(
        "--port-history", action="store_true",
        help="Reserved: port DEVLOG/steps history instead of snapshot "
        "(not yet implemented).",
    )
    p_import.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing .state/project.json.",
    )
    p_import.set_defaults(func=cmd_import)

    p_fu = sub.add_parser(
        "fu", help="Refine backlog (followups.json) — Proposal A.",
    )
    fu_sub = p_fu.add_subparsers(dest="fu_cmd", required=True)

    p_fu_list = fu_sub.add_parser(
        "list", parents=[json_parent], help="List backlog items.",
    )
    p_fu_list.add_argument("--status", default=None, help="Filter by status.")
    p_fu_list.add_argument("--kind", default=None, help="Filter by kind.")
    p_fu_list.add_argument("--priority", default=None, help="Filter by priority.")
    p_fu_list.set_defaults(func=cmd_fu_list)

    p_fu_show = fu_sub.add_parser(
        "show", parents=[json_parent], help="Show one item by id.",
    )
    p_fu_show.add_argument("id", help="Follow-up id, e.g. FU-41.")
    p_fu_show.set_defaults(func=cmd_fu_show)

    p_fu_render = fu_sub.add_parser(
        "render",
        help="Regenerate the FOLLOWUPS markdown tables from state (drift-killer).",
    )
    p_fu_render.set_defaults(func=cmd_fu_render)

    p_fu_add = fu_sub.add_parser(
        "add", help="Add a backlog item (auto-assigns the next FU-N).",
    )
    p_fu_add.add_argument("--kind", required=True, choices=_FU_KINDS)
    p_fu_add.add_argument("--title", required=True, help="One-line title.")
    p_fu_add.add_argument("--context", default=None)
    p_fu_add.add_argument("--trigger", default=None)
    p_fu_add.add_argument(
        "--priority", choices=_FU_HORIZONS, default=None,
        help="Optional scheduling horizon.",
    )
    p_fu_add.add_argument(
        "--files", default=None, help="Comma-separated file hints.",
    )
    p_fu_add.add_argument(
        "--refs", default=None,
        help="Comma-separated refs (decisions / commits / other ids).",
    )
    p_fu_add.set_defaults(func=cmd_fu_add)

    p_fu_close = fu_sub.add_parser(
        "close", help="Close an item (sets status, resolution, closed date).",
    )
    p_fu_close.add_argument("id")
    p_fu_close.add_argument("--resolution", default=None)
    p_fu_close.add_argument(
        "--status", choices=("closed", "wontfix"), default="closed",
    )
    p_fu_close.set_defaults(func=cmd_fu_close)

    p_fu_reopen = fu_sub.add_parser(
        "reopen", help="Reopen an item (status=open).",
    )
    p_fu_reopen.add_argument("id")
    p_fu_reopen.set_defaults(func=cmd_fu_reopen)

    p_fu_prioritize = fu_sub.add_parser(
        "prioritize", help="Set an item's scheduling horizon.",
    )
    p_fu_prioritize.add_argument("id")
    p_fu_prioritize.add_argument(
        "--priority", required=True, choices=_FU_HORIZONS,
    )
    p_fu_prioritize.set_defaults(func=cmd_fu_prioritize)

    # Passthrough subcommands.
    # (forwarding raw argv to the tool's own argparse) — registered here only so
    # they appear in `i2c --help`. add_help=False so `i2c state -h` / `i2c
    # assemble -h` forward to the underlying tool's help, not argparse's.
    sub.add_parser(
        "state",
        add_help=False,
        help="Atomic state writes (passthrough to the state tool).",
    )
    sub.add_parser(
        "assemble",
        add_help=False,
        help="Assemble worker prompts / section snapshots (passthrough).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    # Output invariant: UTF-8 stdout so em dashes / arrows in rendered text
    # round-trip on Windows (cp1252 default). Mirrors the other tool mains.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (ValueError, AttributeError):  # pragma: no cover
            pass

    raw = list(sys.argv[1:] if argv is None else argv)
    # Passthrough subcommands: forward raw argv to the tool's own argparse.
    # Done before our parser so options in the tail (e.g. `--section status`)
    # aren't intercepted by this dispatcher.
    if raw and raw[0] == "state":
        return state.main(raw[1:])
    if raw and raw[0] == "assemble":
        return assemble_context.main(raw[1:])

    parser = build_parser()
    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

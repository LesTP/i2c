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
    i2c logs [--iter N] [--limit N]  # iteration index, or one transcript via --iter
    i2c portfolio [--root PATH]       # cross-project 'which needs me?' view
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
    _render_dispatch,
    _render_escalation,
    _render_log_transcript,
    _render_logs,
    _render_phase_summary,
    _render_portfolio,
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


def cmd_portfolio(args: argparse.Namespace) -> int:
    from pathlib import Path

    root = Path(args.root) if args.root else None
    try:
        report = control.portfolio(root=root)
    except control.ControlError as e:
        return _fail(e)
    _emit(report, as_json=args.json, renderer=_render_portfolio)
    return 0


def cmd_clear_boundary(args: argparse.Namespace) -> int:
    try:
        result = control.clear_boundary(advance=not args.terminate)
    except control.ControlError as e:
        return _fail(e)
    _emit(result, as_json=args.json, renderer=_render_boundary)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    # Resolve run settings with precedence: CLI flag > i2c.toml > built-in.
    try:
        cfg = config.load_run_config()
    except config.ConfigError as e:
        return _fail(e)
    backend = args.backend or cfg.backend or "claude"
    model = args.model or cfg.model or run_iteration.DEFAULT_MODEL
    if args.max_budget_usd is not None:
        max_budget_usd = args.max_budget_usd
    elif cfg.max_budget_usd is not None:
        max_budget_usd = cfg.max_budget_usd
    else:
        max_budget_usd = run_iteration.DEFAULT_MAX_BUDGET_USD
    return control.run_iteration(
        backend=backend, model=model, max_budget_usd=max_budget_usd
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
        help="Backend to invoke. Precedence: this flag > i2c.toml [run].backend "
        "> claude.",
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

    # Passthrough subcommands. Dispatch is handled by a short-circuit in main()
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

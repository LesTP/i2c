"""Transport-agnostic command dispatch for chat surfaces.

Pure logic: imports ``i2c.control`` + ``i2c.render`` and **never**
``python-telegram-bot``, so it is fully unit-testable without the ``telegram``
extra installed. The Telegram wiring shell (``i2c.surfaces.telegram``) is the
only thing that touches the transport library; it computes ``is_admin`` from the
sender, calls ``dispatch``, and sends the resulting ``Reply``.

``dispatch`` is synchronous and complete (it even runs ``/run`` and ``/batch``
to completion). The shell decides threading — it runs dispatch off the event
loop for long-running mutating commands so the bot stays responsive.

Command surface (DESIGN_surface_backends_v1.md §4):

- ``/audit [proj] [facet]`` — the read hub; ``facet`` ∈ {∅→summary, ``phase N``,
  ``decisions [N]``, ``devlog [N]``, ``escalation``, ``logs [N]`` /
  ``logs iter N``}.
- ``/diagnose [proj] [N]`` — read-only recovery diagnosis of iteration N
  (default: latest): runs the drift audit and classifies the failure.
- ``/portfolio`` — cross-project view.
- ``/setdir <proj>`` — set the chat's current project.
- ``/commands`` (``/start``) — this listing.
- ``/run [proj] [N] [backend]`` — up to N iterations (default 1) on a **single**
  backend (arg, else the project default), stopping at a halt or non-zero exit.
- ``/batch [proj]`` — loop to a halt using the **per-action** backend map.
- ``/reconcile [proj] [apply]`` — apply deterministic workflow-drift fixes; dry-run
  by default, ``apply`` writes them (via ``state.py``). Admin-gated.
- ``/endphase [proj] [last]`` — clear an ``audit_boundary`` (``last`` = terminate).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from i2c import control, render

READ_COMMANDS = frozenset({"audit", "diagnose", "portfolio", "setdir", "commands", "start"})
MUTATING_COMMANDS = frozenset({"run", "batch", "reconcile", "endphase"})
ALL_COMMANDS = READ_COMMANDS | MUTATING_COMMANDS

# Runaway guard for /batch (which has no explicit count). A phase always
# progresses to a halt (execute consumes steps → review → close → boundary),
# so this is only a safety net against a stuck loop.
_BATCH_MAX = 50

_BACKENDS = ("claude", "codex")

# Telegram slash-command menu (the "/" autocomplete + Menu button). Registered
# on startup via set_my_commands (see surfaces/telegram._set_command_menu), so it
# stays in sync with the code. Names must be Telegram-legal (lowercase, <=32).
COMMAND_MENU: list[tuple[str, str]] = [
    ("commands", "Show all commands"),
    ("portfolio", "Cross-project view — which project needs attention"),
    ("setdir", "Show or set the current project"),
    ("audit", "Audit a project: summary | phase N | decisions | devlog | escalation | logs"),
    ("run", "Admin: run N iterations (default 1); add 'full' for per-step progress"),
    ("batch", "Admin: run a full phase to a halt; add 'full' for per-step progress"),
    ("diagnose", "Diagnose a failed iteration: drift audit + classification (read-only)"),
    ("reconcile", "Admin: apply workflow-drift fixes (dry-run; 'apply' to write)"),
    ("endphase", "Admin: clear the audit_boundary (advance; 'last' to terminate)"),
]

_HELP = (
    "i2c bot commands\n"
    "\n"
    "Read:\n"
    " /audit [proj] [facet] — Project audit. facet: (none)=summary | phase N "
    "| decisions [N] | devlog [N] | escalation | logs [N] | logs iter N\n"
    " /diagnose [proj] [N] — Diagnose iteration N (default latest): drift audit "
    "+ classification (read-only)\n"
    " /portfolio — Cross-project view (which project needs me?)\n"
    " /setdir <proj> — Show or set the current project\n"
    "\n"
    "Admin:\n"
    " /run [proj] [N] [backend] [full] — Run N iterations (default 1) on one "
    "backend; add 'full' to stream a per-iteration heartbeat\n"
    " /batch [proj] [full] — Run a full phase to a halt; backend per action; "
    "add 'full' to stream a per-iteration heartbeat\n"
    " /reconcile [proj] [apply] — Apply workflow-drift fixes; dry-run unless 'apply'\n"
    " /endphase [proj] [last] — Clear the audit_boundary (advance; last = terminate)\n"
    "\n"
    "/start and /commands show this list. Most commands take an optional project "
    "name; otherwise the /setdir current project (or the only one) is used."
)


@dataclass
class Reply:
    """What the surface should send back. ``ok=False`` marks a refusal/error
    (the surface may style it differently). ``document`` is a long body to send
    as a file. ``set_current`` asks the surface to persist a new current project
    for the chat."""

    text: str
    ok: bool = True
    document: str | None = None
    set_current: str | None = None


def _int_arg(args: list[str]) -> int | None:
    for a in args:
        if a.isdigit():
            return int(a)
    return None


def _resolve_project(
    root: Path, args: list[str], current: str | None
) -> tuple[Path | None, list[str], str | None]:
    """Resolve the target project from a leading NAME arg, the chat's current
    project, or the sole project. Returns (root, remaining_args, error)."""
    projects = control.discover_projects(root)
    by_name = {p.name: p for p in projects}
    if args and args[0] in by_name:
        return by_name[args[0]], args[1:], None
    if current and current in by_name:
        return by_name[current], args, None
    if len(projects) == 1:
        return projects[0], args, None
    if not projects:
        return None, args, f"No i2c projects found under {root}."
    return None, args, "Specify a project: " + ", ".join(sorted(by_name))


def dispatch(
    command: str,
    args: list[str],
    *,
    is_admin: bool,
    root: Path,
    current: str | None = None,
    run_iteration_fn: Callable[[Path, str | None], int] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Reply:
    """Run one command and return a Reply. ``run_iteration_fn(proj, backend)``
    (the surface shells ``i2c run`` with the project as CWD; ``backend`` is an
    optional single-backend override, else the project's per-action map applies)
    backs ``/run`` and ``/batch``; it is injectable so tests can supply a fake."""
    command = command.lower().lstrip("/")

    if command in ("commands", "start"):
        return Reply(_HELP)

    if command not in ALL_COMMANDS:
        return Reply(f"Unknown command: /{command}\n\n{_HELP}", ok=False)

    if command in MUTATING_COMMANDS and not is_admin:
        return Reply(f"Not authorized: /{command} requires admin.", ok=False)

    # Root-scoped (no project resolution).
    if command == "portfolio":
        return Reply(render._render_portfolio(control.portfolio(root)))
    if command == "setdir":
        if not args:
            return Reply("Usage: /setdir <project>", ok=False)
        names = {p.name for p in control.discover_projects(root)}
        if args[0] not in names:
            return Reply(
                f"Unknown project {args[0]!r}. Known: {', '.join(sorted(names))}",
                ok=False,
            )
        return Reply(f"Current project set to {args[0]}.", set_current=args[0])

    # Project-scoped commands.
    proj, rest, err = _resolve_project(root, args, current)
    if err:
        return Reply(err, ok=False)
    try:
        return _dispatch_project(command, rest, proj, run_iteration_fn, progress)
    except control.ControlError as e:
        return Reply(f"Error: {e}", ok=False)


def _parse_run_args(rest: list[str]) -> tuple[int, str | None]:
    """From /run's args (after project): an integer count (default 1) and an
    optional backend token (claude/codex)."""
    n = 1
    backend: str | None = None
    for a in rest:
        if a.isdigit():
            n = int(a)
        elif a in _BACKENDS:
            backend = a
    return n, backend


def _has_full(rest: list[str]) -> bool:
    """True when the caller opted into the per-iteration heartbeat. Accepts the
    bare token ``full`` (documented) or ``--full`` (alias)."""
    return any(a.lower() in ("--full", "full") for a in rest)


def _iter_desc(action: str, step_pos: str, new_state: str, rc: int) -> str:
    """One-line description of a completed iteration (heartbeat + summary)."""
    return f"{action}{step_pos} \u2192 {new_state} (exit {rc})"


def _run_series(
    proj: Path,
    max_iters: int,
    backend: str | None,
    run_iteration_fn: Callable[[Path, str | None], int],
    progress: Callable[[str], None] | None,
    full: bool,
) -> tuple[list[tuple[int, str, str, str, int]], int]:
    """Run up to ``max_iters`` worker iterations, stopping at a halt (EXIT) or a
    non-zero exit. Returns ``(records, last_rc)`` where each record is
    ``(n, action, step_pos, new_state, rc)``. When ``full`` and ``progress`` is
    set, emits a heartbeat line after each iteration (best-effort; the surface
    bridges it to the chat)."""
    records: list[tuple[int, str, str, str, int]] = []
    last_rc = 0
    for _ in range(max_iters):
        na = control.next_action(proj)
        if na.action == "EXIT":
            break  # halt reached (boundary / escalation / done)
        action = na.action
        step_pos = ""
        if action == "EXECUTE":
            before = control.status(proj)
            pend = sorted(s.step for s in before.steps if s.status == "pending")
            if pend and before.steps:
                step_pos = f" step {pend[0]} of {len(before.steps)}"
        last_rc = run_iteration_fn(proj, backend)
        n = len(records) + 1
        new_state = control.status(proj).state
        records.append((n, action, step_pos, new_state, last_rc))
        if full and progress is not None:
            progress(f"[{n}] {_iter_desc(action, step_pos, new_state, last_rc)}")
        if last_rc != 0:
            break  # escalation / failure halts the series
    return records, last_rc


def _enumerate(records: list[tuple[int, str, str, str, int]]) -> str:
    """Numbered list of the iterations that ran, for the concluding message."""
    return "\n".join(
        f"{n}. {_iter_desc(a, sp, ns, rc)}" for (n, a, sp, ns, rc) in records
    )


def _audit(rest: list[str], proj: Path) -> Reply:
    """The /audit read hub — route by facet to a control projection."""
    if not rest:
        return Reply(render._render_status(control.status(proj)))
    facet, sub = rest[0].lower(), rest[1:]
    if facet == "phase":
        n = _int_arg(sub)
        if n is None:
            return Reply("Usage: /audit [proj] phase <N>", ok=False)
        return Reply(render._render_phase_summary(control.phase_summary(proj, phase=n)))
    if facet == "decisions":
        return Reply(render._render_decisions(control.decisions(proj, phase=_int_arg(sub))))
    if facet == "devlog":
        return Reply(render._render_devlog_list(control.devlog(proj, phase=_int_arg(sub))))
    if facet == "escalation":
        return Reply(render._render_escalation(control.escalation(proj)))
    if facet == "logs":
        if sub and sub[0] == "iter":
            n = _int_arg(sub[1:])
            if n is None:
                return Reply("Usage: /audit [proj] logs iter <n>", ok=False)
            rec = control.logs_transcript(proj, iter=n)
            text = render._render_log_transcript(rec)
            doc = rec.transcript if (rec.transcript and len(text) > 3500) else None
            return Reply(text, document=doc)
        return Reply(render._render_logs(control.logs(proj, limit=_int_arg(sub) or 10)))
    return Reply(
        f"Unknown /audit facet {facet!r}. "
        "Use: phase N | decisions | devlog | escalation | logs",
        ok=False,
    )


def _dispatch_project(
    command: str,
    rest: list[str],
    proj: Path,
    run_iteration_fn: Callable[[Path, str | None], int] | None,
    progress: Callable[[str], None] | None = None,
) -> Reply:
    if command == "audit":
        return _audit(rest, proj)

    if command == "diagnose":
        target = _int_arg(rest)
        return Reply(render._render_diagnosis(control.diagnose(proj, target=target)))

    if command == "reconcile":
        apply = any(a.lower() == "apply" for a in rest)
        report = control.reconcile(proj, apply=apply)
        return Reply(render._render_reconcile(report))

    if command == "endphase":
        terminate = "last" in rest
        result = control.clear_boundary(proj, advance=not terminate)
        return Reply(render._render_boundary(result))

    if command == "run":
        if run_iteration_fn is None:
            return Reply("run is not available on this surface.", ok=False)
        full = _has_full(rest)
        n, backend = _parse_run_args(rest)
        records, last_rc = _run_series(
            proj, n, backend, run_iteration_fn, progress, full
        )
        ran = len(records)
        state = control.status(proj).state
        be = backend or "project default/map"
        msg = f"Ran {ran}/{n} on {be}; now state={state} (last exit={last_rc})."
        if records and not full:  # with 'full', heartbeats already showed each step
            msg += "\n" + _enumerate(records)
        return Reply(msg, ok=(last_rc == 0))

    if command == "batch":
        if run_iteration_fn is None:
            return Reply("batch is not available on this surface.", ok=False)
        full = _has_full(rest)
        records, last_rc = _run_series(
            proj, _BATCH_MAX, None, run_iteration_fn, progress, full
        )  # None → per-action backend map
        ran = len(records)
        state = control.status(proj).state
        msg = (
            f"Batch done: {ran} iteration(s); now state={state} "
            f"(last exit={last_rc})."
        )
        if records and not full:  # with 'full', heartbeats already showed each step
            msg += "\n" + _enumerate(records)
        return Reply(msg, ok=(last_rc == 0))
    return Reply(f"Unhandled command: /{command}", ok=False)  # pragma: no cover

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
- ``/portfolio`` — cross-project view.
- ``/setdir <proj>`` — set the chat's current project.
- ``/commands`` (``/start``) — this listing.
- ``/run [proj] [N] [backend]`` — up to N iterations (default 1) on a **single**
  backend (arg, else the project default), stopping at a halt or non-zero exit.
- ``/batch [proj]`` — loop to a halt using the **per-action** backend map.
- ``/endphase [proj] [last]`` — clear an ``audit_boundary`` (``last`` = terminate).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from i2c import control, render

READ_COMMANDS = frozenset({"audit", "portfolio", "setdir", "commands", "start"})
MUTATING_COMMANDS = frozenset({"run", "batch", "endphase"})
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
    ("audit", "Audit a project: summary | phase N | decisions | devlog | escalation | logs"),
    ("portfolio", "Cross-project view — which project needs attention"),
    ("run", "Admin: run N iterations on one backend (default 1)"),
    ("batch", "Admin: run a full phase to a halt (backend per action)"),
    ("endphase", "Admin: clear the audit_boundary (advance; 'last' to terminate)"),
    ("setdir", "Show or set the current project"),
    ("commands", "Show all commands"),
]

_HELP = (
    "i2c bot commands\n"
    "\n"
    "Read:\n"
    " /audit [proj] [facet] — Project audit. facet: (none)=summary | phase N "
    "| decisions [N] | devlog [N] | escalation | logs [N] | logs iter N\n"
    " /portfolio — Cross-project view (which project needs me?)\n"
    " /setdir <proj> — Show or set the current project\n"
    "\n"
    "Admin:\n"
    " /run [proj] [N] [backend] — Run N iterations (default 1) on one backend\n"
    " /batch [proj] — Run a full phase to a halt; backend chosen per action\n"
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
        return _dispatch_project(command, rest, proj, run_iteration_fn)
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
) -> Reply:
    if command == "audit":
        return _audit(rest, proj)

    if command == "endphase":
        terminate = "last" in rest
        result = control.clear_boundary(proj, advance=not terminate)
        return Reply(render._render_boundary(result))

    if command == "run":
        if run_iteration_fn is None:
            return Reply("run is not available on this surface.", ok=False)
        n, backend = _parse_run_args(rest)
        ran, last_rc = 0, 0
        for _ in range(n):
            if control.next_action(proj).action == "EXIT":
                break  # reached a halt (boundary / escalation / done)
            last_rc = run_iteration_fn(proj, backend)
            ran += 1
            if last_rc != 0:
                break  # escalation / failure halts the series
        state = control.status(proj).state
        be = backend or "project default/map"
        msg = f"Ran {ran}/{n} on {be}; now state={state} (last exit={last_rc})."
        tail = control.logs(proj, limit=1)
        if tail:
            msg += f" {tail[-1].action} -> {tail[-1].reason}"
        return Reply(msg, ok=(last_rc == 0))

    if command == "batch":
        if run_iteration_fn is None:
            return Reply("batch is not available on this surface.", ok=False)
        ran, last_rc = 0, 0
        for _ in range(_BATCH_MAX):
            if control.next_action(proj).action == "EXIT":
                break  # halt state reached
            last_rc = run_iteration_fn(proj, None)  # None → per-action map
            ran += 1
            if last_rc != 0:
                break  # escalation / failure halts the batch
        state = control.status(proj).state
        return Reply(
            f"Batch done: {ran} iteration(s); now state={state} "
            f"(last exit={last_rc}).",
            ok=(last_rc == 0),
        )
    return Reply(f"Unhandled command: /{command}", ok=False)  # pragma: no cover

"""i2c state machine — dispatch decision (read-only).

Reads ``.state/`` (walked up from CWD) and decides which ACTION the next
worker invocation should perform, plus the NEXT state that worker must set
after completing the action. Emits the decision as two lines on stdout::

    ACTION: PLAN|EXECUTE|REVIEW|CLOSE|EXIT
    NEXT: plan|execute|review|close

Pure read + decision per D-r-4: never modifies ``.state/``. The worker
performs all state writes via ``tools/state.py`` per the action procedure.
The runner's FU-22 post-close invariant check (``tools/invariants.py``)
replaces the small ``blocked: true`` side-effect that e2e's
``state_machine.sh`` had on CLOSE dispatch.

Environment variables (mirroring e2e for forward compatibility):

- ``STEP_BUDGET`` (default ``1``) — number of steps the worker may take
  this invocation. Reserved for multi-step mode; v1 runner always passes
  ``1`` so this script never decrements anything.
- ``STOP_BEFORE_REVIEW`` (default ``false``) — when ``true``, a dispatch
  that would have been ``REVIEW`` becomes ``EXIT`` instead. NEXT is set to
  ``review`` so a follow-up invocation resumes there.

Exit codes:

- ``0`` — clean decision (including ``ACTION: EXIT``)
- ``2`` — missing or schema-invalid ``.state/`` files; structured error
  on stderr via ``assemble_context.error_exit``.

Decision matrix (per the plan):

============================  ==========================  ===========================  =======  ========
``project.json.blocked``      ``project.json.state``      pending steps for phase      ACTION   NEXT
============================  ==========================  ===========================  =======  ========
``true``                      (any)                       (any)                        EXIT     current
``false``                     ``plan``                    (any)                        PLAN     execute
``false``                     ``execute``                 > 1                          EXECUTE  execute
``false``                     ``execute``                 == 1                         EXECUTE  review
``false``                     ``execute``                 == 0                         REVIEW   close
``false``                     ``review``                  (any)                        REVIEW   close
``false``                     ``close``                   (any)                        CLOSE    plan
============================  ==========================  ===========================  =======  ========

``STOP_BEFORE_REVIEW=true`` short-circuits any REVIEW dispatch to
``ACTION: EXIT`` with ``NEXT: review``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# state_machine.py imports its sibling modules. Tools live in the same
# directory; tests prepend the tools dir to sys.path explicitly. When
# invoked as a script, Python adds the script's directory to sys.path
# automatically, so the sibling imports work in both contexts.
import assemble_context as ac
import validate as v


# ---------------------------------------------------------------------------
# State-transition table — see module docstring for the full matrix.
# ---------------------------------------------------------------------------


VALID_STATES = ("plan", "execute", "review", "close")


def _parse_bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes")


def _parse_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def count_pending_steps(steps: list[dict[str, Any]], phase: int) -> int:
    """Return the number of ``status='pending'`` steps in the given phase."""
    return sum(
        1
        for s in steps
        if s.get("phase") == phase and s.get("status") == "pending"
    )


def decide(
    project: dict[str, Any],
    steps: list[dict[str, Any]],
    *,
    stop_before_review: bool = False,
) -> tuple[str, str]:
    """Apply the dispatch matrix and return ``(ACTION, NEXT)``.

    Pure function: no I/O, no env lookups. Easy to test cell-by-cell.
    """
    blocked = bool(project.get("blocked", False))
    state = project.get("state", "")
    if state not in VALID_STATES:
        raise ValueError(
            f"project.json.state is {state!r}; expected one of {VALID_STATES}"
        )
    if blocked:
        return "EXIT", state

    phase = int(project.get("phase", 0))

    if state == "plan":
        return "PLAN", "execute"

    if state == "execute":
        pending = count_pending_steps(steps, phase)
        if pending == 0:
            if stop_before_review:
                return "EXIT", "review"
            return "REVIEW", "close"
        next_state = "review" if pending == 1 else "execute"
        return "EXECUTE", next_state

    if state == "review":
        if stop_before_review:
            return "EXIT", "review"
        return "REVIEW", "close"

    if state == "close":
        return "CLOSE", "plan"

    # Unreachable — VALID_STATES guards above. Defensive raise.
    raise ValueError(f"unreachable state {state!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # Output invariant: ASCII-only lines, but reconfigure stdout to UTF-8
    # for consistency with assemble_context.py.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (ValueError, AttributeError):  # pragma: no cover
            pass

    root = ac.find_project_root()

    try:
        project = v.validate_state_file(root / ".state" / "project.json")
        steps = v.validate_state_file(root / ".state" / "steps.json")
        # phases.json is read to satisfy the same required-state invariants
        # the assembler enforces; the decision matrix itself doesn't need it
        # in v1, but reading + validating early lets us fail fast.
        v.validate_state_file(root / ".state" / "phases.json")
    except ValueError as e:
        sys.stderr.write(f"ERROR: state file schema-invalid\nFile: {root}\nDetail: {e}\n")
        return 2

    stop_before_review = _parse_bool_env("STOP_BEFORE_REVIEW", default=False)
    # STEP_BUDGET is read for forward compatibility with multi-step mode
    # (D-r-4 / D-r-7). v1 single-iteration runner always passes 1 and we
    # never decrement here; honored so an operator can preview multi-step
    # decisions later without code changes.
    _ = _parse_int_env("STEP_BUDGET", default=1)

    try:
        action, next_state = decide(
            project, steps, stop_before_review=stop_before_review,
        )
    except ValueError as e:
        sys.stderr.write(f"ERROR: invalid state\nFile: {root / '.state' / 'project.json'}\nDetail: {e}\n")
        return 2

    sys.stdout.write(f"ACTION: {action}\n")
    sys.stdout.write(f"NEXT: {next_state}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

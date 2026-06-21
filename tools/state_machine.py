"""i2c state machine — dispatch decision (read-only).

Reads ``.state/`` (walked up from CWD) and decides which ACTION the next
worker invocation should perform, plus the NEXT state that worker must set
after completing the action. Emits the decision as two lines on stdout::

    ACTION: PLAN|EXECUTE|REVIEW|CLOSE|EXIT
    NEXT: plan|execute|review|close|audit_boundary|audit_escalation|done

Pure read + decision per D-r-4: never modifies ``.state/``. The worker
performs all state writes via ``tools/state.py`` per the action procedure.
The runner's FU-22 post-close invariant check (``tools/invariants.py``)
replaces the small ``state=audit_boundary`` side-effect that e2e's
``state_machine.sh`` had on CLOSE dispatch.

Exit codes:

- ``0`` — clean decision (including ``ACTION: EXIT``)
- ``2`` — missing or schema-invalid ``.state/`` files; structured error
  on stderr via ``assemble_context.error_exit``.

Decision matrix (per DESIGN_state_lifecycle_v1.md §4):

============================  ===========================  =======  ================
``project.json.state``        pending steps for phase      ACTION   NEXT
============================  ===========================  =======  ================
``plan``                      (any)                        PLAN     execute
``execute``                   > 1                          EXECUTE  execute
``execute``                   == 1                         EXECUTE  review
``execute``                   == 0                         REVIEW   close
``review``                    (any)                        REVIEW   close
``close``                     (any)                        CLOSE    audit_boundary
``audit_boundary``            (any)                        EXIT     audit_boundary
``audit_escalation``          (any)                        EXIT     audit_escalation
``done``                      (any)                        EXIT     done
============================  ===========================  =======  ================
"""

from __future__ import annotations

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


VALID_STATES = (
    "plan",
    "execute",
    "review",
    "close",
    "audit_boundary",
    "audit_escalation",
    "done",
)

# States that dispatch EXIT regardless of phase/steps content. The loop halts
# at these; humans or an autonomous wrapper transition out.
HALT_STATES = ("audit_boundary", "audit_escalation", "done")


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
) -> tuple[str, str]:
    """Apply the dispatch matrix and return ``(ACTION, NEXT)``.

    Pure function: no I/O, no env lookups. Easy to test cell-by-cell.
    """
    state = project.get("state", "")
    if state not in VALID_STATES:
        raise ValueError(
            f"project.json.state is {state!r}; expected one of {VALID_STATES}"
        )

    if state in HALT_STATES:
        return "EXIT", state

    phase = int(project.get("phase", 0))
    if state == "plan":
        return "PLAN", "execute"
    if state == "execute":
        pending = count_pending_steps(steps, phase)
        if pending == 0:
            return "REVIEW", "close"
        next_state = "review" if pending == 1 else "execute"
        return "EXECUTE", next_state
    if state == "review":
        return "REVIEW", "close"
    if state == "close":
        return "CLOSE", "audit_boundary"
    # Unreachable — VALID_STATES + HALT_STATES guards above. Defensive raise.
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

    try:
        action, next_state = decide(project, steps)
    except ValueError as e:
        sys.stderr.write(f"ERROR: invalid state\nFile: {root / '.state' / 'project.json'}\nDetail: {e}\n")
        return 2

    sys.stdout.write(f"ACTION: {action}\n")
    sys.stdout.write(f"NEXT: {next_state}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

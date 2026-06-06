"""i2c post-action invariant checks (FU-22).

After every action completes, certain structural invariants must hold in
``.state/``. The runner calls ``check_post_action`` after a worker returns
to detect silent drift — e.g., the CC Phase 1 close that left
``blocked: false`` because the worker missed step 11 of
``instructions/close.md``. Detecting this runner-side complements the
worker's exit-signal validation (``schemas/exit_signal.schema.json``) by
asserting that the worker actually *did* the writes its action required.

This module is reusable: ``check_post_action(project_root, action)``
returns a list of failure messages (empty list = pass). Supervised
workflows can call the same function after a manual action.

v1 invariants (per the plan):

================== =================================================================
Action             Invariant
================== =================================================================
``close``          ``project.json.blocked == true`` AND
                   ``phases.json[id == project.json.phase].status == "complete"``
``review``         ``project.json.state == "close"``
``plan``           ``project.json.state == "execute"``
``execute``        Either ``project.json.state == "execute"`` (more steps pending)
                   OR ``project.json.state == "review"`` (last step complete)
================== =================================================================

These are the trivial structural invariants the action procedures lock in.
The list grows as new patterns emerge from autonomous runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Sibling tool imports (same pattern as state_machine.py).
import assemble_context as ac
import validate as v


ACTIONS = ("plan", "execute", "review", "close")


# ---------------------------------------------------------------------------
# Public Python API
# ---------------------------------------------------------------------------


def check_post_action(project_root: Path, action: str) -> list[str]:
    """Return a list of invariant-failure messages for the given action.

    Empty list ⇒ all invariants pass. Each message is a short, structured
    sentence the caller can write to ``logs/loop/summary.log`` or surface
    in an error path. The function never raises on schema-valid input;
    schema-invalid state is itself an invariant failure and surfaces as
    a message.
    """
    if action not in ACTIONS:
        raise ValueError(
            f"Unknown action {action!r}; expected one of {ACTIONS}"
        )

    try:
        project = v.validate_state_file(project_root / ".state" / "project.json")
        phases = v.validate_state_file(project_root / ".state" / "phases.json")
    except ValueError as e:
        return [f"state file schema-invalid: {e}"]

    failures: list[str] = []
    if action == "close":
        failures.extend(_check_close(project, phases))
    elif action == "review":
        failures.extend(_check_review(project))
    elif action == "plan":
        failures.extend(_check_plan(project))
    elif action == "execute":
        failures.extend(_check_execute(project))
    return failures


# ---------------------------------------------------------------------------
# Per-action invariant checks
# ---------------------------------------------------------------------------


def _check_close(project: dict[str, Any], phases: list[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    if not bool(project.get("blocked", False)):
        failures.append(
            "post-CLOSE invariant: project.json.blocked must be true after CLOSE "
            "(worker likely skipped step 11 of instructions/close.md)"
        )
    phase_id = project.get("phase")
    record = next((p for p in phases if p.get("id") == phase_id), None)
    if record is None:
        failures.append(
            f"post-CLOSE invariant: no phases.json record with id == {phase_id}"
        )
    elif record.get("status") != "complete":
        failures.append(
            f"post-CLOSE invariant: phases.json[id={phase_id}].status must be "
            f"'complete' (currently {record.get('status')!r})"
        )
    return failures


def _check_review(project: dict[str, Any]) -> list[str]:
    state = project.get("state")
    if state != "close":
        return [
            f"post-REVIEW invariant: project.json.state must be 'close' "
            f"(currently {state!r})"
        ]
    return []


def _check_plan(project: dict[str, Any]) -> list[str]:
    state = project.get("state")
    if state != "execute":
        return [
            f"post-PLAN invariant: project.json.state must be 'execute' "
            f"(currently {state!r})"
        ]
    return []


def _check_execute(project: dict[str, Any]) -> list[str]:
    state = project.get("state")
    if state not in ("execute", "review"):
        return [
            f"post-EXECUTE invariant: project.json.state must be 'execute' "
            f"(more pending steps) or 'review' (last step complete); "
            f"currently {state!r}"
        ]
    return []


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invariants.py",
        description="Post-action invariant checks for i2c (FU-22).",
    )
    parser.add_argument(
        "--action",
        choices=ACTIONS,
        required=True,
        help="Which action just completed; selects the invariants to verify.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = ac.find_project_root()
    failures = check_post_action(root, args.action)
    if not failures:
        sys.stdout.write(f"OK: post-{args.action.upper()} invariants pass\n")
        return 0
    sys.stderr.write(f"ERROR: post-{args.action.upper()} invariant(s) failed\n")
    sys.stderr.write(f"File: {root / '.state'}\n")
    for msg in failures:
        sys.stderr.write(f"Detail: {msg}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

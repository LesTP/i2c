"""i2c post-action invariant checks (FU-22).

After every action completes, certain structural invariants must hold in
``.state/``. The runner calls ``check_post_action`` after a worker returns
to detect silent drift — e.g., the CC Phase 1 close that left
``state=close`` (no audit_boundary transition) because the worker missed the
final step of ``instructions/close.md``. Detecting this runner-side
complements the worker's exit-signal validation
(``schemas/exit_signal.schema.json``) by asserting that the worker actually
*did* the writes its action required.

This module is reusable: ``check_post_action(project_root, action)``
returns a list of failure messages (empty list = pass). Supervised
workflows can call the same function after a manual action.

v1 invariants (per the lifecycle redesign in DESIGN_state_lifecycle_v1.md):

================== =================================================================
Action             Invariant
================== =================================================================
``close``          ``project.json.state == "audit_boundary"`` AND
                   ``phases.json[id == project.json.phase].status == "complete"``
``review``         ``project.json.state in {"close", "audit_escalation"}``
``plan``           ``project.json.state in {"execute", "audit_escalation"}``
``execute``        ``project.json.state in {"execute", "review", "audit_escalation"}``
================== =================================================================

``audit_escalation`` is a valid post-state for plan/execute/review because
any of those actions may halt the loop with an escalation. ``close`` always
transitions to ``audit_boundary`` — conservative closure per D-state-3: the
close worker never sets ``done`` directly; the human/wrapper decides at the
boundary whether to advance to a new phase or terminate.

These are the trivial structural invariants the action procedures lock in.
The list grows as new patterns emerge from autonomous runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

# Sibling package modules.
from i2c import assemble_context as ac
from i2c import validate as v


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
    state = project.get("state")
    if state != "audit_boundary":
        failures.append(
            "post-CLOSE invariant: project.json.state must be 'audit_boundary' "
            f"after CLOSE (currently {state!r}); worker likely skipped the final "
            "close step that sets state=audit_boundary"
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
    if state not in ("close", "audit_escalation"):
        return [
            f"post-REVIEW invariant: project.json.state must be 'close' "
            f"or 'audit_escalation' (currently {state!r})"
        ]
    return []


def _check_plan(project: dict[str, Any]) -> list[str]:
    state = project.get("state")
    if state not in ("execute", "audit_escalation"):
        return [
            f"post-PLAN invariant: project.json.state must be 'execute' "
            f"or 'audit_escalation' (currently {state!r})"
        ]
    return []


def _check_execute(project: dict[str, Any]) -> list[str]:
    state = project.get("state")
    if state not in ("execute", "review", "audit_escalation"):
        return [
            f"post-EXECUTE invariant: project.json.state must be 'execute' "
            f"(more pending steps), 'review' (last step complete), or "
            f"'audit_escalation' (worker halted); currently {state!r}"
        ]
    return []


# ---------------------------------------------------------------------------
# Refine (sub-phase) invariant — DESIGN_refine_v1.md §12, Q-B2
# ---------------------------------------------------------------------------

# The lifecycle state files a refine run must NOT touch (refine is sub-phase).
_PHASE_FILES = ("project.json", "phases.json", "steps.json")


def snapshot_phase_files(project_root: Path) -> dict[str, str | None]:
    """Capture the current contents of the lifecycle state files (or ``None``
    when absent) so ``check_post_refine`` can assert a refine run left them
    byte-unchanged. Best-effort read; the raw text is compared, not parsed."""
    snap: dict[str, str | None] = {}
    for name in _PHASE_FILES:
        path = project_root / ".state" / name
        snap[name] = path.read_text(encoding="utf-8") if path.is_file() else None
    return snap


def check_post_refine(
    project_root: Path, *, pre_files: dict[str, str | None], pre_devlog_count: int
) -> list[str]:
    """Post-refine invariant (Proposal B). Returns failure messages (empty ⇒ pass).

    Refine is a sub-phase, single-shot action: it must NOT advance the lifecycle.
    This hard-asserts the two structural properties that keep it off the phase
    machine (Q-B2):

    1. ``project.json`` / ``phases.json`` / ``steps.json`` are byte-unchanged vs
       the pre-invoke snapshot — the worker touched no lifecycle state.
    2. The worker appended a ``devlog.jsonl`` entry with ``action == "refine"``
       (the sub-phase outcome record; D-refine-8).

    The runner calls this as a guard *before* closing the FU / committing, so a
    lifecycle-violating or unlogged run is surfaced (exit 2) and never committed.
    """
    failures: list[str] = []

    for name in _PHASE_FILES:
        path = project_root / ".state" / name
        now = path.read_text(encoding="utf-8") if path.is_file() else None
        if now != pre_files.get(name):
            failures.append(
                f"post-REFINE invariant: {name} changed during the refine run; "
                "refine is sub-phase and must not write lifecycle state"
            )

    devlog_path = project_root / ".state" / "devlog.jsonl"
    try:
        devlog = (
            v.validate_devlog_jsonl(devlog_path) if devlog_path.is_file() else []
        )
    except ValueError as e:
        failures.append(f"post-REFINE invariant: devlog schema-invalid: {e}")
        return failures

    appended = devlog[pre_devlog_count:]
    if not any(entry.get("action") == "refine" for entry in appended):
        failures.append(
            "post-REFINE invariant: worker did not append a devlog entry with "
            "action='refine' (the sub-phase outcome record)"
        )
    return failures


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

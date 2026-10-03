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

``check_post_action`` checks the end state only (no baseline). The allowed
end states per action come from ``contracts.CONTRACTS``; ``close`` also
requires ``phases.json[id == phase].status == "complete"`` and an unchanged
frozen acceptance suite. ``audit_escalation`` is a valid post-state for every
action except ``close``, which always ends at ``audit_boundary`` (D-state-3).

``check_iteration`` is the full per-iteration contract gate the runner and
``i2c check`` share: end state + iteration-relative postconditions (given a
baseline) + write scope (given the changed paths). See
DESIGN_action_contracts_v1.md.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

# Sibling package modules.
from i2c import assemble_context as ac
from i2c import contracts
from i2c import state as _state
from i2c import validate as v


ACTIONS = contracts.ACTIONS


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
        failures.extend(_check_acceptance_integrity(project_root, project))
    else:
        msg = contracts.state_failure(action, project, phases)
        if msg:
            failures.append(f"post-{action.upper()} invariant: {msg}")
    return failures


def check_iteration(
    project_root: Path,
    action: str,
    *,
    baseline: contracts.Baseline | None = None,
    changed_paths: list[str] | None = None,
    worker_exit: int = 0,
) -> contracts.Verdict:
    """The per-iteration contract gate (DESIGN_action_contracts_v1 §5.1).

    Schema-invalid state is a blocking failure. CLOSE also verifies the frozen
    acceptance suite (D-tests-4) whenever the worker claimed success.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action!r}; expected one of {ACTIONS}")
    try:
        project = v.validate_state_file(project_root / ".state" / "project.json")
        v.validate_state_file(project_root / ".state" / "phases.json")
        v.validate_state_file(project_root / ".state" / "steps.json")
    except ValueError as e:
        verdict = contracts.Verdict(action=action)
        verdict.failures.append(f"state file schema-invalid: {e}")
        return verdict
    verdict = contracts.evaluate(
        project_root,
        action,
        phase=baseline.phase if baseline else None,
        before=baseline.before if baseline else None,
        changed_paths=changed_paths,
        worker_exit=worker_exit,
    )
    if action == "close" and worker_exit == 0:
        verdict.failures.extend(_check_acceptance_integrity(project_root, project))
    return verdict


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


# ---------------------------------------------------------------------------
# Acceptance-suite integrity — hard CLOSE invariant (D-tests-4 part 2, FU-43)
# ---------------------------------------------------------------------------
#
# The `tests` action freezes a per-phase acceptance suite under
# tests/acceptance/phase_<N>/ as an independent oracle; EXECUTE must not change
# it (execute.md/review.md, the soft layer). The hard layer: at the N.tests
# commit the runner records a digest of the frozen suite in the runner-owned
# sidecar .state/tests_manifest.json; at CLOSE we recompute the digest and fail
# the iteration (exit 2) if it changed. Git-free by design — the digest is a
# filesystem hash, so this stays pure of git access and works on non-git
# fixtures (tests_commit may be null). No marker for a phase ⇒ skip (no TESTS
# action, or a project that predates FU-43): never a false fail.

_TESTS_MANIFEST = "tests_manifest.json"


def _acceptance_dir(project_root: Path, phase: int) -> Path:
    return project_root / "tests" / "acceptance" / f"phase_{phase}"


def compute_acceptance_digest(project_root: Path, phase: int) -> str | None:
    """Deterministic sha256 over the frozen acceptance suite for ``phase``.

    Returns ``None`` when ``tests/acceptance/phase_<N>/`` does not exist. Hashes
    every source file's POSIX-relative path and raw bytes in sorted path order,
    so the result is independent of filesystem ordering.

    Python bytecode (``__pycache__/`` and ``*.pyc`` / ``*.pyo``) is excluded: it
    is regenerated with fresh mtimes (and extra pytest-rewritten variants)
    whenever the suite runs between the TESTS freeze and CLOSE, which would
    otherwise trip the D-tests-4 integrity check even though the oracle source
    is unchanged.
    """
    d = _acceptance_dir(project_root, phase)
    if not d.is_dir():
        return None
    files = sorted(
        (
            p
            for p in d.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.relative_to(d).parts
            and p.suffix not in (".pyc", ".pyo")
        ),
        key=lambda p: p.relative_to(d).as_posix(),
    )
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(d).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def record_tests_suite(
    project_root: Path, phase: int, *, tests_commit: str | None, digest: str
) -> None:
    """Upsert the frozen-suite marker for ``phase`` into ``.state/tests_manifest.json``.

    Runner-owned sidecar (like telemetry.jsonl). Re-authoring a phase's suite
    (a ``partial`` TESTS re-run) replaces the phase's entry. Atomic write, then
    validate so a malformed manifest fails loud at write time rather than
    silently at CLOSE.
    """
    path = project_root / ".state" / _TESTS_MANIFEST
    manifest: dict[str, Any] = {"suites": []}
    if path.is_file():
        try:
            manifest = v.validate_state_file(path)
        except ValueError:
            manifest = {"suites": []}
    suites = [s for s in manifest.get("suites", []) if s.get("phase") != phase]
    suites.append(
        {"phase": phase, "tests_commit": tests_commit, "digest": digest}
    )
    suites.sort(key=lambda s: s.get("phase", 0))
    manifest["suites"] = suites
    manifest.setdefault("schema_version", 1)
    _state.atomic_write_json(path, manifest)
    v.validate_state_file(path)


def _check_acceptance_integrity(
    project_root: Path, project: dict[str, Any]
) -> list[str]:
    phase = project.get("phase")
    path = project_root / ".state" / _TESTS_MANIFEST
    if not path.is_file():
        return []  # no marker → no TESTS action / predates FU-43 → skip
    try:
        manifest = v.validate_state_file(path)
    except ValueError as e:
        return [f"post-CLOSE invariant: {_TESTS_MANIFEST} schema-invalid: {e}"]
    entry = next(
        (s for s in manifest.get("suites", []) if s.get("phase") == phase), None
    )
    if entry is None:
        return []  # this phase froze no acceptance suite → skip
    current = compute_acceptance_digest(project_root, phase)
    if current is None:
        return [
            "post-CLOSE invariant: acceptance suite "
            f"tests/acceptance/phase_{phase}/ was frozen at the N.tests commit "
            "but is now missing; the frozen oracle must not be deleted "
            "(restore it, or if the removal was human-authorized re-freeze with "
            f"`i2c tests refreeze --phase {phase} --reason <decision>`)"
        ]
    if current != entry.get("digest"):
        return [
            "post-CLOSE invariant: acceptance suite "
            f"tests/acceptance/phase_{phase}/ changed since it was frozen at the "
            "N.tests commit (D-tests-4). EXECUTE must not edit the frozen oracle: "
            "restore it and fix the implementation, or if a human authorized the "
            "correction re-freeze with "
            f"`i2c tests refreeze --phase {phase} --reason <decision>`"
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

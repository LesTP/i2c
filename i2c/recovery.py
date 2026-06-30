"""i2c recovery — deterministic drift audit + reconcile proposals (archive/DESIGN_recovery_v1.md).

Recovery is the one failure class i2c can *own*: **workflow-state drift** — the
``.state/`` bookkeeping disagreeing with reality (the loop died mid-iteration
before finishing its writes; a commit landed but its step stayed ``pending``;
all steps completed but ``project.state`` never advanced). The empirical sweep
(``archive/DESIGN_recovery_v1.md`` Appendix "Phase 0") established this is real and
recurring (~7-8% of iterations across e2e and i2c), while code / spec / env
failures are orthogonal — handled by REVIEW + human judgment, not by any state
format.

This module is the deterministic core. It **detects** drift (``audit_state`` for
pure-``.state`` signals; ``audit_git`` for git/disk signals) and **proposes**
the exact mechanical reconcile — never applies it (the human gate + the
sanctioned ``state.py`` write path live in the surfaces). Same return-shape
philosophy as ``invariants.check_post_action``: a flat list of findings the
runner / a surface can act on. Recovery extends i2c's existing *detect-and-halt*
post-action invariants into *detect-and-reconcile*.

Reuse, don't reinvent:
  - ``control.load_state`` — validated bundled ``.state/`` read.
  - ``state_machine.count_pending_steps`` — the state→action truth.
  - ``invariants._check_close`` — the post-CLOSE structural check.

What this module deliberately does NOT do: write ``.state/`` (reconcile goes
through ``state.py``), classify code/spec/env failures (that's the ``diagnose``
worker action + the LLM), or shell ``git`` outside the one helper below.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from i2c import control
from i2c import invariants
from i2c import state_machine


# ---------------------------------------------------------------------------
# Stable signal identifiers
#
# Each finding carries one of these as ``signal`` so surfaces / tests can match
# on a stable id rather than the human-readable message. Grouped by detector:
# [S] pure-.state, [G] needs git, [D] needs the working tree.
# ---------------------------------------------------------------------------

# [S] — pure .state signals (audit_state)
SIG_STEP_COMPLETE_NO_COMMIT = "step_complete_without_commit"
SIG_EXECUTE_NOT_ADVANCED = "execute_state_not_advanced"
SIG_CLOSE_GATE_NOT_SET = "close_gate_not_set"

# [G]/[D] — git/disk signals (audit_git)
SIG_COMMIT_ABSENT_FROM_GIT = "commit_absent_from_git"
SIG_COMMIT_WITHOUT_STEP = "commit_exists_step_pending"
SIG_STEP_COMPLETE_DIRTY_TREE = "step_complete_dirty_tree"


# ---------------------------------------------------------------------------
# Return types
# ---------------------------------------------------------------------------


@dataclass
class ReconcileAction:
    """A proposed deterministic ``.state/`` mutation that clears a finding.

    Mirrors one ``i2c state`` invocation so reconcile can apply it through the
    sanctioned, schema-validated write path (``state.py``) — recovery never
    writes ``.state/`` directly. ``op`` is one of ``set`` / ``complete`` /
    ``update-record``; ``payload`` carries the op-specific arguments:

    - ``set``:           ``{"keys": {"state": "review"}}``
    - ``complete``:      ``{"phase": N, "step": M, "commit": "<hash>"}``
    - ``update-record``: ``{"match": "id=D-3", "updates": {...}}``
    """

    op: str
    file: str
    payload: dict[str, Any]
    description: str


@dataclass
class DriftFinding:
    """One detected state-vs-reality inconsistency.

    ``signal`` is a stable id (see the ``SIG_*`` constants). ``message`` is the
    human-readable description. ``reconcilable`` is True when ``proposal``
    carries a deterministic fix recovery can apply behind the human gate; False
    for findings that need judgment (surfaced, never auto-applied).
    """

    signal: str
    message: str
    reconcilable: bool = False
    proposal: ReconcileAction | None = None
    # Where the drift lives, for surfaces that group by location. Optional.
    phase: int | None = None
    step: int | None = None


def messages(findings: list[DriftFinding]) -> list[str]:
    """Flatten findings to the ``list[str]`` shape ``invariants`` uses.

    Lets the runner wire the audit alongside ``check_post_action`` without
    caring about the richer structure (it just logs the strings)."""
    return [f.message for f in findings]


# ---------------------------------------------------------------------------
# Pure-.state drift checks ([S])
#
# One small checker per signal, mirroring invariants.py's _check_* layout.
# Each takes the bundled ProjectState and returns a list of findings.
# ---------------------------------------------------------------------------


_HEX_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_HALT_OR_TERMINAL = ("audit_boundary", "audit_escalation", "done")


def _check_step_complete_without_commit(st: control.ProjectState) -> list[DriftFinding]:
    """[S] A step marked ``complete`` with no (or malformed) ``commit`` hash.

    A completed step should record the commit its work landed in. The schema
    permits the field to be absent (it's optional and only enforces the hex
    pattern *when present*), so this is detectable on schema-valid state. It is
    not deterministically reconcilable from ``.state/`` alone — finding the true
    commit needs git (``audit_git`` upgrades this to a reconcilable finding when
    a matching commit exists). Surfaced here so a pure-state audit still reports
    it.
    """
    out: list[DriftFinding] = []
    for s in st.steps:
        if s.get("status") != "complete":
            continue
        commit = s.get("commit")
        if commit is not None and _HEX_RE.match(str(commit)):
            continue
        phase = s.get("phase")
        step = s.get("step")
        out.append(
            DriftFinding(
                signal=SIG_STEP_COMPLETE_NO_COMMIT,
                message=(
                    f"step {phase}.{step} is complete but records no valid commit "
                    f"(commit={commit!r}); the worker likely finished the work but "
                    "the bookkeeping write was cut off"
                ),
                reconcilable=False,
                phase=phase,
                step=step,
            )
        )
    return out


def _check_execute_not_advanced(st: control.ProjectState) -> list[DriftFinding]:
    """[S] ``project.state == execute`` but 0 pending steps remain in the phase.

    The state machine would dispatch REVIEW here (``decide`` returns REVIEW when
    pending == 0), so leaving the project in ``execute`` is drift: the worker
    completed the last step but never set ``state=review``. Deterministically
    reconcilable — advance the state.
    """
    if st.project.get("state") != "execute":
        return []
    phase = int(st.project.get("phase", 0))
    # If the phase record is already marked complete, this is a close-gate
    # situation (owned by _check_close_gate_not_set), not an advance-to-review
    # one. Bail so the two checks never both fire and propose conflicting
    # project.state writes (review vs audit_boundary) for the same phase.
    record = st.phase_record(phase)
    if record is not None and record.get("status") == "complete":
        return []
    pending = state_machine.count_pending_steps(st.steps, phase)
    if pending != 0:
        return []
    return [
        DriftFinding(
            signal=SIG_EXECUTE_NOT_ADVANCED,
            message=(
                f"project.state is 'execute' but phase {phase} has 0 pending steps; "
                "the last step completed without advancing state to 'review'"
            ),
            reconcilable=True,
            proposal=ReconcileAction(
                op="set",
                file="project.json",
                payload={"keys": {"state": "review"}},
                description="set project.json state=review (all phase steps complete)",
            ),
            phase=phase,
        )
    ]


def _check_close_gate_not_set(st: control.ProjectState) -> list[DriftFinding]:
    """[S] Current phase marked ``complete`` but the lifecycle isn't at the gate.

    The canonical post-CLOSE inconsistency (clankercourts): CLOSE flipped the
    phase record to ``complete`` but never set ``state=audit_boundary``, so the
    loop can't tell the phase is done. Reuses ``invariants._check_close`` to
    describe the violation, but only fires when the phase is *actually* complete
    and the state is still active — so a normal pre-close ``state=close`` (where
    the phase isn't complete yet) does not cry wolf. Deterministically
    reconcilable — set the gate.
    """
    project = st.project
    state = project.get("state")
    if state in _HALT_OR_TERMINAL:
        return []
    phase = int(project.get("phase", 0))
    record = st.phase_record(phase)
    if record is None or record.get("status") != "complete":
        return []
    # _check_close yields the "state must be audit_boundary" message (the phase
    # record is complete in this branch, so its second check stays silent).
    detail = next(
        (m for m in invariants._check_close(project, st.phases) if "audit_boundary" in m),
        "phase complete but project.state is not 'audit_boundary'",
    )
    return [
        DriftFinding(
            signal=SIG_CLOSE_GATE_NOT_SET,
            message=(
                f"phase {phase} is marked complete but project.state is {state!r}; "
                f"CLOSE did not set the gate. {detail}"
            ),
            reconcilable=True,
            proposal=ReconcileAction(
                op="set",
                file="project.json",
                payload={"keys": {"state": "audit_boundary"}},
                description=(
                    "set project.json state=audit_boundary (phase complete; "
                    "hand to the human boundary)"
                ),
            ),
            phase=phase,
        )
    ]


_STATE_CHECKS = (
    _check_step_complete_without_commit,
    _check_execute_not_advanced,
    _check_close_gate_not_set,
)


def audit_state(st: control.ProjectState) -> list[DriftFinding]:
    """Run every pure-``.state`` drift check ([S]) and return all findings.

    Pure function over an already-validated ``ProjectState``: no I/O, no git.
    This is the cheap deterministic prefilter ``diagnose`` runs first, and the
    detector the runner wires alongside ``invariants.check_post_action``.
    """
    out: list[DriftFinding] = []
    for check in _STATE_CHECKS:
        out.extend(check(st))
    return out


# ---------------------------------------------------------------------------
# git/disk helper ([G]/[D])
#
# The ONE place i2c shells git. Kept to read-only plumbing (rev-parse / log /
# status / diff) — recovery proposes mutations to .state/, never to git. Every
# helper degrades gracefully: a missing git binary or a non-repo directory means
# "can't audit git", which surfaces as an empty finding list, not an exception.
# ---------------------------------------------------------------------------


# Commit-message convention (instructions/execute.md): an EXECUTE step's work
# commits with a "<phase>.<step>: <title>" subject. The drift detector matches
# anchored on that prefix to map a commit back to its step.
def _step_grep(phase: int, step: int) -> str:
    return rf"^{phase}\.{step}:"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run a git subcommand in ``root``; capture text output.

    Never raises on a non-zero git exit (callers inspect ``returncode``);
    a missing git binary raises ``FileNotFoundError``, caught by ``is_git_repo``
    so the whole git audit can no-op cleanly off PATH-less environments.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def is_git_repo(root: Path) -> bool:
    """True iff ``root`` is inside a git work tree and git is on PATH."""
    try:
        cp = _git(root, "rev-parse", "--is-inside-work-tree")
    except (FileNotFoundError, OSError):
        return False
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def commit_exists(root: Path, commit: str) -> bool:
    """True iff ``commit`` resolves to a commit object in this repo."""
    cp = _git(root, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
    return cp.returncode == 0


def find_commit_for_step(root: Path, phase: int, step: int) -> str | None:
    """The newest commit whose subject starts ``<phase>.<step>:``, or None.

    Searches history reachable from HEAD (committed work), matching the EXECUTE
    commit-message convention. Returns the full hash. Note ``git log --grep``
    matches anywhere in the message; a stray ``<phase>.<step>:`` in a commit
    *body* would also match (low-probability, accepted for v1).
    """
    cp = _git(
        root, "log", "-E", f"--grep={_step_grep(phase, step)}",
        "--pretty=%H", "-n", "1",
    )
    if cp.returncode != 0:
        return None
    lines = [ln.strip() for ln in cp.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else None


def _has_substantive_diff(root: Path, path: str) -> bool:
    """True iff ``path`` has a non-cosmetic diff (worktree or index).

    Ignores CRLF-at-EOL and trailing-whitespace changes — the cosmetic NTFS
    false-positive class flagged in the Phase-0 sweep (diplomat #30/#31). It does
    **not** use ``--ignore-all-space``: i2c projects are Python, where leading
    indentation is semantics, so an all-space ignore would hide real changes (a
    de-indented statement) and miss a genuinely dirty tree. A reconcile that
    cries wolf on a CRLF-only diff won't be trusted; one that hides a real edit
    is worse.
    """
    for extra in ((), ("--cached",)):
        cp = _git(
            root, "diff", *extra,
            "--ignore-cr-at-eol", "--ignore-space-at-eol", "--", path,
        )
        if cp.stdout.strip():
            return True
    return False


def working_tree_dirty(root: Path) -> bool:
    """True iff the work tree has real (non-cosmetic) uncommitted changes.

    Untracked / added / deleted / renamed paths always count. Modified paths
    are filtered through ``_has_substantive_diff`` so CRLF-only / trailing-space
    churn does not register as dirty. A modified path that git C-quotes (special
    or non-ASCII filename) is treated as dirty conservatively — we can't pass the
    escaped form back to ``git diff`` reliably, so we don't risk a false negative.
    """
    cp = _git(root, "status", "--porcelain")
    if cp.returncode != 0:
        return False
    for ln in cp.stdout.splitlines():
        if not ln.strip():
            continue
        xy, path = ln[:2], ln[3:]
        if "->" in path:  # rename / copy — a real change
            return True
        if xy == "??":  # untracked
            return True
        if any(c in xy for c in ("A", "D", "R", "C")):
            return True
        if "M" in xy or "T" in xy:
            if path.startswith('"'):
                # C-quoted (special/non-ASCII) path: can't safely diff it, so
                # treat the modification as real rather than miss it.
                return True
            if _has_substantive_diff(root, path):
                return True
    return False


# ---------------------------------------------------------------------------
# git/disk drift checks ([G]/[D])
# ---------------------------------------------------------------------------


def _check_commit_absent_from_git(
    root: Path, st: control.ProjectState
) -> list[DriftFinding]:
    """[G] A step records a ``commit`` that doesn't exist in this repo.

    The recorded hash isn't a real commit object (rebased away, fabricated, or a
    typo). Not deterministically reconcilable — the true commit is unknown, so a
    human must decide. Surfaced, never auto-applied.
    """
    out: list[DriftFinding] = []
    for s in st.steps:
        commit = s.get("commit")
        if not commit or not _HEX_RE.match(str(commit)):
            continue
        if commit_exists(root, str(commit)):
            continue
        out.append(
            DriftFinding(
                signal=SIG_COMMIT_ABSENT_FROM_GIT,
                message=(
                    f"step {s.get('phase')}.{s.get('step')} records commit "
                    f"{commit!r}, which is not in git history; the recorded hash "
                    "is stale or wrong"
                ),
                reconcilable=False,
                phase=s.get("phase"),
                step=s.get("step"),
            )
        )
    return out


def _check_commit_without_step(
    root: Path, st: control.ProjectState
) -> list[DriftFinding]:
    """[G/S] A commit exists for a ``pending`` step (the canonical toolkit-5.3).

    The work committed but ``i2c state complete`` never landed (e.g. the codex
    loop died before bookkeeping, or the state CLI hit a PATH bug). The exact
    reconcile: mark the step complete with the discovered commit.
    """
    out: list[DriftFinding] = []
    for s in st.steps:
        if s.get("status") != "pending":
            continue
        phase, step = s.get("phase"), s.get("step")
        if phase is None or step is None:
            continue
        commit = find_commit_for_step(root, int(phase), int(step))
        if commit is None:
            continue
        short = commit[:7]
        out.append(
            DriftFinding(
                signal=SIG_COMMIT_WITHOUT_STEP,
                message=(
                    f"commit {short} matches step {phase}.{step} "
                    f"(subject '{phase}.{step}:') but the step is still 'pending'; "
                    "the work landed but completion was never recorded"
                ),
                reconcilable=True,
                proposal=ReconcileAction(
                    op="complete",
                    file="steps.json",
                    payload={"phase": int(phase), "step": int(step), "commit": short},
                    description=(
                        f"mark step {phase}.{step} complete with commit {short}"
                    ),
                ),
                phase=phase,
                step=step,
            )
        )
    return out


def _check_dirty_tree(root: Path, st: control.ProjectState) -> list[DriftFinding]:
    """[D] The work tree is dirty while a step is marked complete.

    Bookkeeping says work is done, but uncommitted changes remain — the worker
    may have left a real fix uncommitted, or temporary instrumentation behind.
    Distinguishing the two is a judgment call (diplomat #4), so recovery
    surfaces it and never auto-commits.
    """
    if not any(s.get("status") == "complete" for s in st.steps):
        return []
    if not working_tree_dirty(root):
        return []
    return [
        DriftFinding(
            signal=SIG_STEP_COMPLETE_DIRTY_TREE,
            message=(
                "the working tree has uncommitted (non-cosmetic) changes while "
                "completed steps exist; a human must decide whether this is "
                "unrecorded work or leftover instrumentation before reconciling"
            ),
            reconcilable=False,
        )
    ]


_GIT_CHECKS = (
    _check_commit_absent_from_git,
    _check_commit_without_step,
    _check_dirty_tree,
)


def audit_git(root: Path, st: control.ProjectState) -> list[DriftFinding]:
    """Run every git/disk drift check ([G]/[D]). No-ops off a git repo.

    Requires the project root (to shell git) plus the validated ``ProjectState``.
    Returns ``[]`` when ``root`` is not a git work tree or git is off PATH —
    the pure-``.state`` audit still stands on its own.
    """
    if not is_git_repo(root):
        return []
    out: list[DriftFinding] = []
    for check in _GIT_CHECKS:
        out.extend(check(root, st))
    return out


# ---------------------------------------------------------------------------
# Combined audit
# ---------------------------------------------------------------------------


def audit(root: Path, st: control.ProjectState | None = None) -> list[DriftFinding]:
    """Full drift audit: pure-``.state`` ([S]) + git/disk ([G]/[D]) signals.

    Loads + validates ``.state/`` when ``st`` is not supplied (raises
    ``control.ControlError`` on schema-invalid state). This is the single entry
    point ``diagnose`` runs as its deterministic prefilter.
    """
    if st is None:
        st = control.load_state(root)
    return audit_state(st) + audit_git(root, st)

"""i2c.control — the stable, in-process command API (DESIGN_packaging_v1.md §7).

A thin, dependency-light surface over the deterministic layers that returns
**dataclasses, not strings**, and raises **typed exceptions, not** ``sys.exit``.
It is the shared contract every driver calls: chat surfaces (codexbot), the
Phase-2 ``i2c`` console command, and orchestrators (Human/Policy/Agent). Today
the only structured way to read an i2c project is to shell out to the CLI tools
and parse their prose output — exactly the prose-vs-structure fragility i2c was
built to eliminate. This module fixes that once, so formatting lives in the
surface and never in the core.

Design: **reuse**, don't duplicate. Reads/validation go through
``validate.validate_state_file`` / ``validate.validate_devlog_jsonl`` (which
raise ``ValueError`` — wrapped here into ``ControlError``); dispatch goes
through ``state_machine.decide``; the one write op (``clear_boundary``) reuses
``state.atomic_write_json`` + schema validation. We deliberately do **not**
reuse the assembler's ``_load_*`` helpers (they call ``sys.exit``, wrong for a
library) nor its prose section-builders (worker-facing, golden-tested). Control
computes its own structured views from the same ``.state/``.

This module covers the read surface + the ``clear_boundary`` boundary command.
``run_iteration`` is re-exported (not reimplemented). ``escalation`` / ``logs`` /
``logs_transcript`` (FU-34) project the ``audit_escalation`` signal (from
``.state/``) and the runner's ``logs/loop/`` iteration index + transcripts.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from i2c import run_iteration as _runner
from i2c import state as _state
from i2c import state_machine as _sm
from i2c import validate as v


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ControlError(Exception):
    """Base error for the control surface. Callers catch this instead of
    relying on ``SystemExit`` or parsing stderr."""


class NotFoundError(ControlError):
    """A required project / state file could not be found."""


class InvalidStateError(ControlError):
    """An operation's precondition on ``.state/`` was not met (e.g.,
    ``clear_boundary`` called when ``project.state != 'audit_boundary'``)."""


# ---------------------------------------------------------------------------
# Structured returns
# ---------------------------------------------------------------------------


@dataclass
class StepView:
    phase: int
    step: int
    title: str
    status: str
    commit: str | None = None


@dataclass
class DecisionView:
    id: str
    title: str
    status: str
    phase: int | None = None
    priority: str | None = None
    decision: str | None = None
    rationale: str | None = None


@dataclass
class FollowupView:
    id: str
    title: str
    kind: str
    status: str
    priority: str | None = None
    context: str | None = None
    trigger: str | None = None
    resolution: str | None = None
    refs: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    opened: str | None = None
    closed: str | None = None


@dataclass
class DevlogView:
    phase: int
    step: int | None
    action: str
    outcome: str
    summary: str
    commit: str | None = None
    timestamp: str | None = None


@dataclass
class Dispatch:
    action: str
    next_state: str


@dataclass
class StatusReport:
    phase: int
    state: str
    module: str | None
    regime: str | None
    dependencies: list[str]
    budget: dict[str, int] | None
    steps: list[StepView]
    gotchas: list[str]
    open_decisions: list[DecisionView]
    recent_activity: list[DevlogView]


@dataclass
class PhaseSummary:
    phase: int
    module: str | None
    regime: str | None
    title: str | None
    status: str | None
    steps: list[StepView]
    decisions: list[DecisionView]
    devlog: list[DevlogView]
    open_items: list[DecisionView]


@dataclass
class BoundaryResult:
    outcome: str  # "advanced" | "terminated"
    phase: int
    state: str


@dataclass
class EscalationView:
    phase: int
    is_escalated: bool  # project.state == "audit_escalation"
    entry: DevlogView | None  # the triggering escalate/blocked devlog entry
    surrounding: list[DevlogView]  # up to 3 preceding in-phase entries, for context
    open_decisions: list[DecisionView]  # phase-tagged, status == open


@dataclass
class DriftView:
    """One drift-audit finding, flattened for a surface (recovery.DriftFinding
    projected to prose-free fields). ``proposal`` is the human-readable
    description of the reconcile action (``None`` when not reconcilable)."""

    signal: str
    message: str
    reconcilable: bool
    proposal: str | None = None
    phase: int | None = None
    step: int | None = None


@dataclass
class Diagnosis:
    """The deterministic-first failure diagnosis for one target iteration (§A).

    ``diagnose`` runs the drift audit **first** (cheap, deterministic) and
    classifies the failure: ``workflow-drift`` when the audit found drift (the
    one class recovery owns), ``unknown`` when the target iteration failed but
    the audit explains nothing (hand to the human / the LLM ``diagnose`` worker
    to bucket as code/spec/env), or ``none`` when there's no drift and no failed
    iteration. ``reconcilable`` is True when at least one finding carries a
    deterministic reconcile proposal. Read-only — this view mutates nothing."""

    target: int | None  # iteration number diagnosed (None when no loop log exists)
    classification: str  # "workflow-drift" | "unknown" | "none"
    reconcilable: bool
    phase: int
    state: str
    exit_code: int | None  # the target iteration's runner exit code
    reason: str | None  # the target iteration's summary-line reason
    malformed_signal: bool  # target failed on a missing/malformed exit signal (#1 trigger)
    findings: list[DriftView]
    escalation: DevlogView | None  # triggering escalate/blocked devlog entry, if any


@dataclass
class ReconcileItem:
    """One reconcilable drift finding and whether its fix was written."""

    signal: str
    message: str
    proposal: str  # the ReconcileAction.description
    applied: bool


@dataclass
class ReconcileReport:
    """The result of ``reconcile`` (§C). ``applied`` is True only when the
    operator passed the human gate (``apply=True``) and mutations were written
    via ``state.py``. ``items`` are the reconcilable findings (proposed or
    applied); ``skipped`` are judgment-class findings, surfaced never applied."""

    applied: bool
    items: list[ReconcileItem]
    skipped: list[DriftView]


@dataclass
class IterationLog:
    iter: int
    backend: str
    action: str
    exit_code: int
    reason: str
    timestamp: str
    tokens: dict[str, int] | None = None  # {input, output, cached} or None (FU-33)
    transcript: str | None = None  # logs/loop/iteration_NNN.txt, on demand


@dataclass
class ProjectBrief:
    """One project's line in a portfolio view (§7.6) — enough to answer
    'which project needs me?' without opening each one."""

    root: str
    name: str
    phase: int
    state: str
    module: str | None
    next_action: str
    is_escalated: bool
    escalation_reason: str | None
    open_decisions: int
    error: str | None = None  # set instead of the rest when the project failed to load


@dataclass
class PortfolioReport:
    root: str
    projects: list[ProjectBrief]


# ---------------------------------------------------------------------------
# Project root discovery (raises, unlike the assembler's sys.exit version)
# ---------------------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` (default CWD) for ``.state/project.json``.

    Mirrors ``assemble_context.find_project_root`` but raises
    ``NotFoundError`` instead of calling ``error_exit``/``sys.exit`` — a
    library must never terminate its host process. Uses ``.absolute()`` (not
    ``.resolve()``) for the same Windows mapped-drive reason documented there.
    """
    cwd = (start or Path.cwd()).absolute()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".state" / "project.json").is_file():
            return candidate
    raise NotFoundError(
        f"No .state/project.json found in {cwd} or any parent directory."
    )


def _find_followups_root(start: Path | None = None) -> Path:
    """Walk up for a ``.state/`` holding ``followups.json`` **or** ``project.json``.

    The refine backlog is independent of the phase lifecycle (D-refine-3): a repo
    can carry ``followups.json`` without being a full i2c project, and a project
    can be valid before its backlog file exists. So accept either marker.
    """
    cwd = (start or Path.cwd()).absolute()
    for candidate in [cwd, *cwd.parents]:
        state = candidate / ".state"
        if (state / "followups.json").is_file() or (state / "project.json").is_file():
            return candidate
    raise NotFoundError(
        f"No .state/followups.json or project.json found in {cwd} or any parent."
    )


# ---------------------------------------------------------------------------
# Bundled state read
# ---------------------------------------------------------------------------


@dataclass
class ProjectState:
    """The five validated ``.state/`` reads bundled together, so each public
    function doesn't re-read ad hoc."""

    root: Path
    project: dict[str, Any]
    phases: list[dict[str, Any]]
    steps: list[dict[str, Any]]
    decisions: list[dict[str, Any]]
    devlog: list[dict[str, Any]]

    def phase_record(self, phase: int) -> dict[str, Any] | None:
        for record in self.phases:
            if record.get("id") == phase:
                return record
        return None


def _state_path(root: Path, name: str) -> Path:
    return root / ".state" / name


def load_state(root: Path) -> ProjectState:
    """Read + validate the five ``.state/`` files. Raises ``ControlError``.

    ``project.json`` / ``phases.json`` / ``steps.json`` are required;
    ``decisions.json`` / ``devlog.jsonl`` default to empty when absent (same
    optionality the assembler applies). All underlying ``ValueError``s from
    ``validate`` (missing file, bad JSON, schema failure) are wrapped into
    ``ControlError`` so callers see one typed surface.
    """
    try:
        project = v.validate_state_file(_state_path(root, "project.json"))
        phases = v.validate_state_file(_state_path(root, "phases.json"))
        steps = v.validate_state_file(_state_path(root, "steps.json"))

        decisions_path = _state_path(root, "decisions.json")
        decisions = (
            v.validate_state_file(decisions_path)
            if decisions_path.is_file()
            else []
        )

        devlog_path = _state_path(root, "devlog.jsonl")
        devlog = (
            v.validate_devlog_jsonl(devlog_path) if devlog_path.is_file() else []
        )
    except ValueError as e:
        raise ControlError(str(e)) from e

    return ProjectState(
        root=root,
        project=project,
        phases=phases,
        steps=steps,
        decisions=decisions,
        devlog=devlog,
    )


def _read_followups(root: Path) -> list[dict[str, Any]]:
    """Read + validate ``.state/followups.json`` (Proposal A). Independent of the
    five ``load_state`` files: a repo can carry a refine backlog without being a
    full phase-driven i2c project (D-refine-3). Absent file -> ``[]``.
    """
    path = _state_path(root, "followups.json")
    if not path.is_file():
        return []
    try:
        return v.validate_state_file(path)
    except ValueError as e:
        raise ControlError(str(e)) from e


# ---------------------------------------------------------------------------
# View builders
# ---------------------------------------------------------------------------


def _step_view(record: dict[str, Any]) -> StepView:
    return StepView(
        phase=record.get("phase"),
        step=record.get("step"),
        title=record.get("title", ""),
        status=record.get("status", ""),
        commit=record.get("commit"),
    )


def _decision_view(record: dict[str, Any]) -> DecisionView:
    return DecisionView(
        id=record.get("id", ""),
        title=record.get("title", ""),
        status=record.get("status", ""),
        phase=record.get("phase"),
        priority=record.get("priority"),
        decision=record.get("decision"),
        rationale=record.get("rationale"),
    )


def _devlog_view(record: dict[str, Any]) -> DevlogView:
    return DevlogView(
        phase=record.get("phase"),
        step=record.get("step"),
        action=record.get("action", ""),
        outcome=record.get("outcome", ""),
        summary=record.get("summary", ""),
        commit=record.get("commit"),
        timestamp=record.get("timestamp"),
    )


def _followup_view(record: dict[str, Any]) -> FollowupView:
    return FollowupView(
        id=record.get("id", ""),
        title=record.get("title", ""),
        kind=record.get("kind", ""),
        status=record.get("status", ""),
        priority=record.get("priority"),
        context=record.get("context"),
        trigger=record.get("trigger"),
        resolution=record.get("resolution"),
        refs=list(record.get("refs") or []),
        files=list(record.get("files") or []),
        opened=record.get("opened"),
        closed=record.get("closed"),
    )


def _phase_steps(st: ProjectState, phase: int) -> list[StepView]:
    steps = [s for s in st.steps if s.get("phase") == phase]
    steps.sort(key=lambda s: s.get("step", 0))
    return [_step_view(s) for s in steps]


def _compute_budget(project: dict[str, Any]) -> dict[str, int] | None:
    if "steps_remaining" in project:
        return {"steps_remaining": project["steps_remaining"]}
    if project.get("budget_type") == "time" and "time_budget_seconds" in project:
        return {"time_budget_seconds": project["time_budget_seconds"]}
    return None


# ---------------------------------------------------------------------------
# Read surface
# ---------------------------------------------------------------------------


def status(root: Path | None = None) -> StatusReport:
    """Project-wide snapshot: current phase/state, current-phase steps,
    gotchas, open decisions, and the recent devlog tail (last 3)."""
    root = root or find_project_root()
    st = load_state(root)
    project = st.project
    phase = int(project.get("phase", 0))
    record = st.phase_record(phase)

    if record is None:
        module = regime = None
        dependencies: list[str] = []
    else:
        module = record.get("module")
        regime = record.get("regime")
        dependencies = list(record.get("dependencies") or [])

    open_decisions = [
        _decision_view(d) for d in st.decisions if d.get("status") == "open"
    ]
    recent = [_devlog_view(e) for e in st.devlog[-3:][::-1]]

    return StatusReport(
        phase=phase,
        state=project.get("state", ""),
        module=module,
        regime=regime,
        dependencies=dependencies,
        budget=_compute_budget(project),
        steps=_phase_steps(st, phase),
        gotchas=list(project.get("gotchas") or []),
        open_decisions=open_decisions,
        recent_activity=recent,
    )


def next_action(root: Path | None = None) -> Dispatch:
    """The state machine's dispatch decision for the current state, as a
    structured ``Dispatch`` (wraps ``state_machine.decide``)."""
    root = root or find_project_root()
    st = load_state(root)
    try:
        action, next_state = _sm.decide(st.project, st.steps)
    except ValueError as e:
        raise ControlError(str(e)) from e
    return Dispatch(action=action, next_state=next_state)


def phase_summary(root: Path | None = None, *, phase: int) -> PhaseSummary:
    """Operator's boundary view of one phase: header, steps, decisions added
    in that phase, the phase's devlog, and open items for the boundary."""
    root = root or find_project_root()
    st = load_state(root)
    record = st.phase_record(phase)

    if record is None:
        module = regime = title = status_ = None
    else:
        module = record.get("module")
        regime = record.get("regime")
        title = record.get("title")
        status_ = record.get("status")

    phase_decisions = [
        _decision_view(d) for d in st.decisions if d.get("phase") == phase
    ]
    devlog = [_devlog_view(e) for e in st.devlog if e.get("phase") == phase]
    open_items = [
        _decision_view(d)
        for d in st.decisions
        if d.get("phase") == phase and d.get("status") == "open"
    ]

    return PhaseSummary(
        phase=phase,
        module=module,
        regime=regime,
        title=title,
        status=status_,
        steps=_phase_steps(st, phase),
        decisions=phase_decisions,
        devlog=devlog,
        open_items=open_items,
    )


def decisions(
    root: Path | None = None, *, phase: int | None = None
) -> list[DecisionView]:
    """All decision records, optionally filtered to those tagged ``phase``."""
    root = root or find_project_root()
    st = load_state(root)
    records = st.decisions
    if phase is not None:
        records = [d for d in records if d.get("phase") == phase]
    return [_decision_view(d) for d in records]


def devlog(
    root: Path | None = None, *, phase: int | None = None, limit: int | None = None
) -> list[DevlogView]:
    """Devlog entries, optionally filtered to ``phase`` and/or limited to the
    last ``limit`` (newest last). Replaces the removed assembler ``--section
    devlog`` projection — same phase-filtered full history when ``phase`` is
    given."""
    root = root or find_project_root()
    st = load_state(root)
    entries = st.devlog
    if phase is not None:
        entries = [e for e in entries if e.get("phase") == phase]
    if limit is not None:
        entries = entries[-limit:]
    return [_devlog_view(e) for e in entries]


def followups(
    root: Path | None = None, *, status: str | None = None, kind: str | None = None,
    priority: str | None = None,
) -> list[FollowupView]:
    """The refine backlog (Proposal A), optionally filtered by ``status``,
    ``kind``, and/or ``priority``. Reads ``.state/followups.json`` independently
    of the phase-lifecycle state, so it works in any repo that has adopted the
    refine tier (D-refine-3). Empty list when the backlog file is absent."""
    root = root or _find_followups_root()
    records = _read_followups(root)
    if status is not None:
        records = [r for r in records if r.get("status") == status]
    if kind is not None:
        records = [r for r in records if r.get("kind") == kind]
    if priority is not None:
        records = [r for r in records if r.get("priority") == priority]
    return [_followup_view(r) for r in records]


# ---------------------------------------------------------------------------
# Boundary command (the one write op)
# ---------------------------------------------------------------------------


def clear_boundary(root: Path | None = None, *, advance: bool = True) -> BoundaryResult:
    """Clear an ``audit_boundary`` gate: advance to the next phase or terminate.

    Precondition: ``project.state == "audit_boundary"`` (else
    ``InvalidStateError`` — conservative closure per D-state-3, the close
    worker never sets ``done`` directly). ``advance=True`` writes
    ``phase=N+1, state=plan``; ``advance=False`` writes ``state=done``. The
    mutated ``project.json`` is schema-validated before the atomic write
    (reusing ``state.atomic_write_json``).
    """
    root = root or find_project_root()
    st = load_state(root)
    project = st.project

    if project.get("state") != "audit_boundary":
        raise InvalidStateError(
            "clear_boundary requires project.state == 'audit_boundary' "
            f"(currently {project.get('state')!r})"
        )

    if advance:
        new_phase = int(project.get("phase", 0)) + 1
        project["phase"] = new_phase
        project["state"] = "plan"
        outcome = "advanced"
    else:
        new_phase = int(project.get("phase", 0))
        project["state"] = "done"
        outcome = "terminated"

    schema = v.load_schema(v.SCHEMA_BY_FILENAME["project.json"])
    try:
        v.validate_json_schema(project, schema, label="project.json")
    except ValueError as e:
        raise ControlError(f"project.json would be schema-invalid: {e}") from e

    _state.atomic_write_json(_state_path(root, "project.json"), project)
    return BoundaryResult(outcome=outcome, phase=new_phase, state=project["state"])


# ---------------------------------------------------------------------------
# Portfolio: control projections mapped across many projects (§7.6)
# ---------------------------------------------------------------------------

# Directory names never worth descending into while discovering projects.
_PORTFOLIO_SKIP = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".state", "logs",
    ".venv", "venv", ".mypy_cache", ".pytest_cache", "dist", "build",
}

# Sort key: surface what needs the operator first. Lower = more urgent.
_STATE_RANK = {
    "audit_escalation": 0,
    "audit_boundary": 1,
    "plan": 2, "execute": 2, "review": 2, "close": 2,
    "done": 4,
}


def discover_projects(root: Path) -> list[Path]:
    """Every directory under ``root`` (inclusive) that holds
    ``.state/project.json``. Does not descend below a discovered project, and
    skips VCS / build / dependency noise. Sorted by path."""
    root = Path(root).absolute()
    found: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        d = stack.pop()
        if (d / ".state" / "project.json").is_file():
            found.append(d)
            continue  # a project is a leaf — don't descend into it
        try:
            stack.extend(
                c for c in d.iterdir()
                if c.is_dir()
                and c.name not in _PORTFOLIO_SKIP
                and not c.name.startswith(".")
            )
        except (PermissionError, OSError):
            continue
    found.sort()
    return found


def _project_brief(proj: Path) -> ProjectBrief:
    """Assemble one ProjectBrief over the existing control projections
    (``status`` / ``escalation`` / ``next_action``). A per-project load failure
    is captured in ``error`` so one broken project can't break the sweep."""
    try:
        s = status(proj)
        esc = escalation(proj)
        disp = next_action(proj)
    except ControlError as e:
        return ProjectBrief(
            root=str(proj), name=proj.name, phase=0, state="?", module=None,
            next_action="?", is_escalated=False, escalation_reason=None,
            open_decisions=0, error=str(e),
        )
    return ProjectBrief(
        root=str(proj),
        name=proj.name,
        phase=s.phase,
        state=s.state,
        module=s.module,
        next_action=disp.action,
        is_escalated=esc.is_escalated,
        escalation_reason=(esc.entry.summary if (esc.is_escalated and esc.entry) else None),
        open_decisions=len(s.open_decisions),
    )


def _attention_rank(b: ProjectBrief) -> tuple[int, str]:
    if b.error:
        return (-1, b.name)  # broken projects float to the very top
    return (_STATE_RANK.get(b.state, 3), b.name)


def portfolio(root: Path | None = None) -> PortfolioReport:
    """Map the control projections across every project under ``root`` (default
    CWD). Answers 'which project needs me?' — projects are ordered with
    escalations and boundaries first. Pure read; does not require ``root``
    itself to be a project."""
    root = (root or Path.cwd()).absolute()
    briefs = [_project_brief(p) for p in discover_projects(root)]
    briefs.sort(key=_attention_rank)
    return PortfolioReport(root=str(root), projects=briefs)


# ---------------------------------------------------------------------------
# Worker driver (re-export — keep the proven implementation)
# ---------------------------------------------------------------------------


run_iteration = _runner.run_iteration


# ---------------------------------------------------------------------------
# Escalation + iteration-log projections (FU-34)
# ---------------------------------------------------------------------------

_ESCALATE_OUTCOMES = ("escalate", "blocked")

# Parses the summary.log line shape written by run_iteration.write_summary_line.
_SUMMARY_RE = re.compile(
    r'^(?P<ts>\S+) \| iter=(?P<iter>\d+) \| backend=(?P<backend>\S+) \| '
    r'action=(?P<action>\S+) \| exit=(?P<exit>\d+)'
    r'(?: \| tokens_in=(?P<tin>\d+) tokens_out=(?P<tout>\d+) '
    r'tokens_cached=(?P<tcached>\d+))?'
    r' \| reason="(?P<reason>.*)"\s*$'
)


def escalation(
    root: Path | None = None, *, phase: int | None = None
) -> EscalationView:
    """The current escalation signal: whether the loop is halted at
    ``audit_escalation``, the triggering devlog entry, a little surrounding
    context, and the phase's open decisions. Pure ``.state/`` read; mirrors
    ``phase_summary``. ``phase`` defaults to the current project phase."""
    root = root or find_project_root()
    st = load_state(root)
    target = phase if phase is not None else int(st.project.get("phase", 0))
    is_escalated = st.project.get("state") == "audit_escalation"

    phase_entries = [e for e in st.devlog if e.get("phase") == target]
    escalate_idx: int | None = None
    for i, e in enumerate(phase_entries):
        if e.get("outcome") in _ESCALATE_OUTCOMES:
            escalate_idx = i  # keep the last match
    if escalate_idx is None:
        entry: DevlogView | None = None
        surrounding: list[DevlogView] = []
    else:
        entry = _devlog_view(phase_entries[escalate_idx])
        surrounding = [
            _devlog_view(e)
            for e in phase_entries[max(0, escalate_idx - 3):escalate_idx]
        ]

    open_decisions = [
        _decision_view(d)
        for d in st.decisions
        if d.get("phase") == target and d.get("status") == "open"
    ]
    return EscalationView(
        phase=target,
        is_escalated=is_escalated,
        entry=entry,
        surrounding=surrounding,
        open_decisions=open_decisions,
    )


def _log_dir(root: Path) -> Path:
    return root / _runner.LOG_DIR_NAME


def _parse_summary_line(line: str) -> IterationLog | None:
    m = _SUMMARY_RE.match(line.strip())
    if m is None:
        return None
    tokens = None
    if m.group("tin") is not None:
        tokens = {
            "input": int(m.group("tin")),
            "output": int(m.group("tout")),
            "cached": int(m.group("tcached")),
        }
    return IterationLog(
        iter=int(m.group("iter")),
        backend=m.group("backend"),
        action=m.group("action"),
        exit_code=int(m.group("exit")),
        reason=m.group("reason"),
        timestamp=m.group("ts"),
        tokens=tokens,
    )


def _read_summary(root: Path) -> list[IterationLog]:
    summary = _log_dir(root) / _runner.SUMMARY_LOG_NAME
    if not summary.is_file():
        return []
    out: list[IterationLog] = []
    for line in summary.read_text(encoding="utf-8").splitlines():
        rec = _parse_summary_line(line)
        if rec is not None:
            out.append(rec)
    return out


def logs(root: Path | None = None, *, limit: int | None = 10) -> list[IterationLog]:
    """The iteration index parsed from ``logs/loop/summary.log`` (no
    transcripts). Newest entries last (file order). ``limit`` keeps the last N
    (default 10; ``None`` for all). Empty list when no log exists yet."""
    root = root or find_project_root()
    entries = _read_summary(root)
    if limit is not None:
        entries = entries[-limit:]
    return entries


def logs_transcript(root: Path | None = None, *, iter: int) -> IterationLog:
    """One iteration's index entry with its ``transcript`` field populated from
    ``logs/loop/iteration_NNN.txt`` (``None`` if the file is absent). Raises
    ``NotFoundError`` when no summary entry exists for ``iter``."""
    root = root or find_project_root()
    for rec in _read_summary(root):
        if rec.iter == iter:
            path = _log_dir(root) / f"iteration_{iter:03d}.txt"
            rec.transcript = (
                path.read_text(encoding="utf-8") if path.is_file() else None
            )
            return rec
    raise NotFoundError(f"No iteration {iter} in {_runner.SUMMARY_LOG_NAME}")


# ---------------------------------------------------------------------------
# Recovery: deterministic-first diagnosis (archive/DESIGN_recovery_v1.md §A)
# ---------------------------------------------------------------------------


def _is_malformed_signal(log: IterationLog | None) -> bool:
    """True iff the target iteration failed because its exit signal couldn't be
    parsed/validated — the #1 real i2c reconcile trigger (the worker, esp. codex,
    finished without a parseable 2-line block, so the loop can't tell what landed)."""
    if log is None or log.exit_code == 0:
        return False
    r = (log.reason or "").lower()
    return "exit signal" in r and (
        "missing" in r or "malformed" in r or "schema validation" in r
    )


def diagnose(root: Path | None = None, *, target: int | None = None) -> Diagnosis:
    """Diagnose a failed/stuck iteration: run the drift audit first, classify.

    ``target`` selects which iteration's failure context to assemble (default:
    the latest in ``logs/loop/summary.log``; ``None`` when no log exists yet).
    Raises ``NotFoundError`` when an explicit ``target`` has no summary entry.
    Pure read: the drift audit and every projection it composes are read-only.
    """
    # Lazy import breaks the recovery <-> control import cycle (recovery imports
    # control at module load; control only needs recovery inside this function).
    from i2c import recovery as _recovery

    root = root or find_project_root()
    st = load_state(root)
    findings = _recovery.audit(root, st)

    entries = _read_summary(root)
    target_log: IterationLog | None = None
    if target is not None:
        target_log = next((e for e in entries if e.iter == target), None)
        if target_log is None:
            raise NotFoundError(
                f"No iteration {target} in {_runner.SUMMARY_LOG_NAME}"
            )
    elif entries:
        target_log = entries[-1]

    drift_views = [
        DriftView(
            signal=f.signal,
            message=f.message,
            reconcilable=f.reconcilable,
            proposal=(f.proposal.description if f.proposal else None),
            phase=f.phase,
            step=f.step,
        )
        for f in findings
    ]
    reconcilable = any(f.reconcilable for f in findings)

    if findings:
        classification = "workflow-drift"
    elif target_log is not None and target_log.exit_code != 0:
        classification = "unknown"
    else:
        classification = "none"

    esc = escalation(root)
    return Diagnosis(
        target=(target_log.iter if target_log else None),
        classification=classification,
        reconcilable=reconcilable,
        phase=int(st.project.get("phase", 0)),
        state=st.project.get("state", ""),
        exit_code=(target_log.exit_code if target_log else None),
        reason=(target_log.reason if target_log else None),
        malformed_signal=_is_malformed_signal(target_log),
        findings=drift_views,
        escalation=esc.entry,
    )


def _apply_proposal(root: Path, action: Any) -> None:
    """Apply one ``recovery.ReconcileAction`` through the sanctioned ``state.py``
    path (atomic + schema-validated). Recovery never writes ``.state/`` directly.

    Routes the proposal to ``state.cmd_set`` / ``state.cmd_complete`` /
    ``state.cmd_update_record`` with a constructed Namespace, suppressing their
    stdout so the library stays prose-free. Raises ``ControlError`` on a
    non-zero return (validation failure / no-match)."""
    file_path = str(_state_path(root, action.file))
    if action.op == "set":
        ns = argparse.Namespace(
            file=file_path,
            pairs=[f"{k}={val}" for k, val in action.payload["keys"].items()],
        )
        fn = _state.cmd_set
    elif action.op == "complete":
        ns = argparse.Namespace(
            file=file_path,
            phase=action.payload["phase"],
            step=action.payload.get("step"),
            commit=action.payload.get("commit"),
        )
        fn = _state.cmd_complete
    elif action.op == "update-record":
        ns = argparse.Namespace(
            file=file_path,
            match=action.payload["match"],
            updates=[f"{k}={val}" for k, val in action.payload["updates"].items()],
            from_file=None,
        )
        fn = _state.cmd_update_record
    else:  # pragma: no cover - defensive
        raise ControlError(f"unknown reconcile op {action.op!r}")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(ns)
    if rc != 0:
        raise ControlError(
            f"reconcile mutation failed (rc={rc}): {action.description}"
        )


def reconcile(root: Path | None = None, *, apply: bool = False) -> ReconcileReport:
    """Reconcile deterministic workflow drift (archive/DESIGN_recovery_v1.md §C).

    Runs the drift audit and partitions findings: reconcilable ones carry a
    deterministic proposal; judgment-class ones are surfaced, never applied.
    **Dry-run by default** — the operator passing ``apply=True`` is the human
    gate. Applied mutations go through ``state.py`` (atomic, schema-validated);
    reconcile never marks an unfinished (code-blocked) step complete — it only
    closes the state-vs-reality gap so the loop can re-attempt.
    """
    from i2c import recovery as _recovery

    root = root or find_project_root()
    st = load_state(root)
    findings = _recovery.audit(root, st)
    reconcilable = [f for f in findings if f.reconcilable and f.proposal is not None]
    judgment = [f for f in findings if not (f.reconcilable and f.proposal is not None)]

    items: list[ReconcileItem] = []
    for f in reconcilable:
        if apply:
            _apply_proposal(root, f.proposal)
        items.append(
            ReconcileItem(
                signal=f.signal,
                message=f.message,
                proposal=f.proposal.description,
                applied=apply,
            )
        )
    skipped = [
        DriftView(
            signal=f.signal,
            message=f.message,
            reconcilable=False,
            proposal=None,
            phase=f.phase,
            step=f.step,
        )
        for f in judgment
    ]
    return ReconcileReport(applied=apply, items=items, skipped=skipped)

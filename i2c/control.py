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

import re
from dataclasses import dataclass
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
class IterationLog:
    iter: int
    backend: str
    action: str
    exit_code: int
    reason: str
    timestamp: str
    tokens: dict[str, int] | None = None  # {input, output, cached} or None (FU-33)
    transcript: str | None = None  # logs/loop/iteration_NNN.txt, on demand


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

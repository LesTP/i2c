"""Per-action contracts: what each lifecycle action may change, and what must
hold when it claims success (DESIGN_action_contracts_v1).

One table, three consumers - the assembler renders it into the prompt, the
worker evaluates it with ``i2c check`` before exiting, and the runner enforces
it after the worker exits and before anything is committed.

Writes are default-deny per action: a changed path must match the action's
``write_scope``; a ``PROTECTED`` path is denied even inside a broad scope unless
the action ``owns`` it. ``NOISE`` (build by-products) is never committed and
never counts. Paths outside the scope that are not protected are *flagged* -
left uncommitted and reported - rather than failing the iteration.

Stdlib-only on purpose: invariants, run_iteration and assemble_context all
import this module.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

ACTIONS = ("plan", "tests", "execute", "review", "close")
ESCALATE_STATE = "audit_escalation"
BASELINE_ENV = "I2C_CHECK_BASELINE"

PROTECTED: tuple[str, ...] = (
    "tests/acceptance/**",
    "i2c.toml",
    "CLAUDE.md",
    "CODEX.md",
    "PIDEV.md",
    ".git/**",
)

NOISE: tuple[str, ...] = (
    "**/__pycache__/**",
    "**/*.pyc",
    "**/.pytest_cache/**",
    "**/node_modules/**",
    "**/.DS_Store",
    "logs/loop/**",
)


# ---------------------------------------------------------------------------
# Glob matching ("**" spans directories, "*" and "?" stay within one segment)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=256)
def _compile(pattern: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def glob_match(path: str, pattern: str) -> bool:
    return _compile(pattern).fullmatch(path.replace("\\", "/")) is not None


def _any_match(path: str, patterns: tuple[str, ...]) -> bool:
    return any(glob_match(path, p) for p in patterns)


# ---------------------------------------------------------------------------
# The contract table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionContract:
    action: str
    write_scope: tuple[str, ...]
    success_states: Mapping[str, tuple[str, ...]]  # regime -> states; "*" fallback
    requires: tuple[str, ...]
    owns: tuple[str, ...] = ()
    # CLOSE always ends at the boundary gate; an EXIT 0 that leaves
    # audit_escalation there is a contradiction (D-state-3).
    escalation_ok: bool = True

    def states_for(self, regime: str) -> tuple[str, ...]:
        return self.success_states.get(regime) or self.success_states["*"]

    def scope(self, phase: int) -> tuple[str, ...]:
        return tuple(p.replace("{phase}", str(phase)) for p in self.write_scope)

    def owned(self, phase: int) -> tuple[str, ...]:
        return tuple(p.replace("{phase}", str(phase)) for p in self.owns)


CONTRACTS: dict[str, ActionContract] = {
    "plan": ActionContract(
        action="plan",
        write_scope=(),
        success_states={"build": ("tests",), "*": ("execute",)},
        requires=("phase_record_exists", "build_phase_has_pending_steps", "devlog_appended"),
    ),
    "tests": ActionContract(
        action="tests",
        write_scope=(
            "tests/acceptance/phase_{phase}/**",
            "tests/acceptance/__init__.py",
            "tests/__init__.py",
        ),
        owns=("tests/acceptance/phase_{phase}/**", "tests/acceptance/__init__.py"),
        # "tests" = partial: out of budget mid-authoring, TESTS is redispatched.
        success_states={"*": ("execute", "tests")},
        requires=("acceptance_suite_nonempty", "devlog_appended"),
    ),
    "execute": ActionContract(
        action="execute",
        write_scope=("**",),
        success_states={"*": ("execute", "review")},
        requires=(
            "one_step_completed",
            "next_state_matches_remaining_steps",
            "devlog_appended",
        ),
    ),
    "review": ActionContract(
        action="review",
        write_scope=("**",),
        success_states={"*": ("close",)},
        requires=("devlog_appended",),
    ),
    "close": ActionContract(
        action="close",
        write_scope=("ARCHITECTURE.md", "ARCH_*.md", "PROJECT.md"),
        success_states={"*": ("audit_boundary",)},
        requires=("phase_marked_complete", "devlog_appended"),
        escalation_ok=False,
    ),
}


def get(action: str) -> ActionContract:
    try:
        return CONTRACTS[action]
    except KeyError:
        raise ValueError(
            f"Unknown action {action!r}; expected one of {ACTIONS}"
        ) from None


def allowed_end_states(action: str, regime: str) -> tuple[str, ...]:
    c = get(action)
    states = c.states_for(regime)
    return states + (ESCALATE_STATE,) if c.escalation_ok else states


# ---------------------------------------------------------------------------
# State snapshots + the runner baseline
# ---------------------------------------------------------------------------


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({})
    return out


@dataclass
class Snapshot:
    """Raw (unvalidated) view of the lifecycle files; schema validity is the
    caller's concern (invariants validates before evaluating)."""

    project: dict[str, Any] = field(default_factory=dict)
    phases: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    devlog: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, root: Path) -> "Snapshot":
        st = root / ".state"
        return cls(
            project=_read_json(st / "project.json", {}),
            phases=_read_json(st / "phases.json", []),
            steps=_read_json(st / "steps.json", []),
            devlog=_read_jsonl(st / "devlog.jsonl"),
        )

    def phase_record(self, phase: int) -> dict[str, Any] | None:
        return next((p for p in self.phases if p.get("id") == phase), None)

    def phase_steps(self, phase: int) -> list[dict[str, Any]]:
        return [s for s in self.steps if s.get("phase") == phase]

    def pending(self, phase: int) -> set[Any]:
        return {s.get("step") for s in self.phase_steps(phase) if s.get("status") == "pending"}

    def complete(self, phase: int) -> set[Any]:
        return {s.get("step") for s in self.phase_steps(phase) if s.get("status") == "complete"}


@dataclass
class Baseline:
    """What the runner captured before invoking the worker."""

    action: str
    phase: int
    pre_dirty: list[str]
    before: Snapshot

    def write(self, path: Path) -> None:
        payload = {
            "action": self.action,
            "phase": self.phase,
            "pre_dirty": sorted(self.pre_dirty),
            "before": {
                "project": self.before.project,
                "phases": self.before.phases,
                "steps": self.before.steps,
                "devlog_count": len(self.before.devlog),
            },
        }
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    @classmethod
    def read(cls, path: Path) -> "Baseline":
        d = json.loads(path.read_text(encoding="utf-8"))
        b = d["before"]
        before = Snapshot(
            project=b.get("project", {}),
            phases=b.get("phases", []),
            steps=b.get("steps", []),
            devlog=[{}] * int(b.get("devlog_count", 0)),
        )
        return cls(action=d["action"], phase=int(d["phase"]),
                   pre_dirty=list(d.get("pre_dirty", [])), before=before)


# ---------------------------------------------------------------------------
# Postconditions
# ---------------------------------------------------------------------------


@dataclass
class _Eval:
    root: Path
    action: str
    phase: int
    regime: str
    before: Snapshot | None
    after: Snapshot


@dataclass(frozen=True)
class Postcondition:
    name: str
    describe: Callable[[str, int], str]  # (action, phase) -> sentence for the prompt
    check: Callable[[_Eval], str | None]
    needs_baseline: bool = False


def _phase_record_exists(e: _Eval) -> str | None:
    if e.after.phase_record(e.phase) is None:
        return f"no phases.json record with id == {e.phase}"
    return None


def _build_phase_has_pending_steps(e: _Eval) -> str | None:
    if e.regime != "build":
        return None
    if not e.after.pending(e.phase):
        return f"no pending steps recorded in steps.json for phase {e.phase}"
    return None


def _acceptance_suite_nonempty(e: _Eval) -> str | None:
    if e.after.project.get("state") != "execute":
        return None  # partial: TESTS is redispatched to finish the suite
    suite = e.root / "tests" / "acceptance" / f"phase_{e.phase}"
    files = [p for p in suite.rglob("*") if p.is_file() and not _any_match(
        p.relative_to(e.root).as_posix(), NOISE)] if suite.is_dir() else []
    if not any(p.name != "__init__.py" for p in files):
        return f"acceptance suite tests/acceptance/phase_{e.phase}/ has no test files"
    return None


def _one_step_completed(e: _Eval) -> str | None:
    assert e.before is not None
    pending_before = e.before.pending(e.phase)
    if not pending_before:
        return None  # Refine / step-less Explore: no step to complete
    done = pending_before & e.after.complete(e.phase)
    if len(done) != 1:
        return (
            f"expected exactly one phase {e.phase} step to go from pending to "
            f"complete in this iteration (got {len(done)})"
        )
    return None


def _next_state_matches_remaining_steps(e: _Eval) -> str | None:
    if not e.after.phase_steps(e.phase):
        return None  # step-less regime: execute/review is the worker's call
    state = e.after.project.get("state")
    remaining = len(e.after.pending(e.phase))
    if remaining and state == "review":
        return f"state is 'review' but {remaining} phase {e.phase} step(s) are still pending"
    if not remaining and state == "execute":
        return f"no phase {e.phase} steps remain pending, so state must be 'review' (currently 'execute')"
    return None


def _devlog_appended(e: _Eval) -> str | None:
    assert e.before is not None
    new = e.after.devlog[len(e.before.devlog):]
    if not any(d.get("action") == e.action and d.get("phase") == e.phase for d in new):
        return f"no '{e.action}' devlog entry for phase {e.phase} was appended in this iteration"
    return None


def phase_complete_failure(phase: int, phases: list[dict[str, Any]]) -> str | None:
    record = next((p for p in phases if p.get("id") == phase), None)
    if record is None:
        return f"no phases.json record with id == {phase}"
    if record.get("status") != "complete":
        return (
            f"phases.json[id={phase}].status must be 'complete' "
            f"(currently {record.get('status')!r})"
        )
    return None


POSTCONDITIONS: dict[str, Postcondition] = {
    pc.name: pc
    for pc in (
        Postcondition(
            "phase_record_exists",
            lambda a, n: f"`phases.json` has a record for phase {n}",
            _phase_record_exists,
        ),
        Postcondition(
            "build_phase_has_pending_steps",
            lambda a, n: f"Build phases: `steps.json` lists the phase {n} steps, all `pending`",
            _build_phase_has_pending_steps,
        ),
        Postcondition(
            "acceptance_suite_nonempty",
            lambda a, n: f"`tests/acceptance/phase_{n}/` contains at least one test file "
                         "(unless you leave `state=tests` to finish it next time)",
            _acceptance_suite_nonempty,
        ),
        Postcondition(
            "one_step_completed",
            lambda a, n: "exactly one pending step is now marked `complete` "
                         "(`i2c state complete`)",
            _one_step_completed,
            needs_baseline=True,
        ),
        Postcondition(
            "next_state_matches_remaining_steps",
            lambda a, n: "`state` is `review` if no steps remain pending, otherwise `execute`",
            _next_state_matches_remaining_steps,
        ),
        Postcondition(
            "phase_marked_complete",
            lambda a, n: f"phase {n} is marked `complete` in `phases.json`",
            lambda e: phase_complete_failure(e.phase, e.after.phases),
        ),
        Postcondition(
            "devlog_appended",
            lambda a, n: f"you appended a `{a}` devlog entry for phase {n} via `i2c state append`",
            _devlog_appended,
            needs_baseline=True,
        ),
    )
}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathReport:
    allowed: tuple[str, ...] = ()
    noise: tuple[str, ...] = ()
    protected: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()


def classify_paths(action: str, phase: int, paths: list[str]) -> PathReport:
    c = get(action)
    scope, owned = c.scope(phase), c.owned(phase)
    buckets: dict[str, list[str]] = {"allowed": [], "noise": [], "protected": [], "out_of_scope": []}
    for raw in sorted(set(paths)):
        p = raw.replace("\\", "/")
        if p.startswith(".state/"):
            continue
        if _any_match(p, NOISE):
            buckets["noise"].append(p)
        elif _any_match(p, PROTECTED) and not _any_match(p, owned):
            buckets["protected"].append(p)
        elif _any_match(p, scope):
            buckets["allowed"].append(p)
        else:
            buckets["out_of_scope"].append(p)
    return PathReport(**{k: tuple(v) for k, v in buckets.items()})


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class Verdict:
    action: str
    failures: list[str] = field(default_factory=list)   # blocking
    flagged: list[str] = field(default_factory=list)    # out of scope, not committed
    excluded: list[str] = field(default_factory=list)   # never committed
    skipped: list[str] = field(default_factory=list)    # needed a runner baseline

    @property
    def ok(self) -> bool:
        return not self.failures


def regime_of(snapshot: Snapshot, phase: int) -> str:
    record = snapshot.phase_record(phase) or {}
    return str(record.get("regime") or "build")


def state_failure(action: str, project: dict[str, Any], phases: list[dict[str, Any]]) -> str | None:
    phase = int(project.get("phase", 0) or 0)
    record = next((p for p in phases if p.get("id") == phase), None) or {}
    allowed = allowed_end_states(action, str(record.get("regime") or "build"))
    state = project.get("state")
    if state in allowed:
        return None
    return (
        "project.json.state must be "
        + " or ".join(repr(s) for s in allowed)
        + f" after {action.upper()} (currently {state!r})"
    )


def evaluate(
    root: Path,
    action: str,
    *,
    phase: int | None = None,
    before: Snapshot | None = None,
    changed_paths: list[str] | None = None,
    worker_exit: int = 0,
) -> Verdict:
    """Evaluate ``action``'s contract against the current ``.state/``.

    ``before`` enables the iteration-relative postconditions (without it they
    are reported in ``skipped``). ``changed_paths`` enables the write-scope
    check. Success states and postconditions apply only to a worker that
    claimed success (``worker_exit == 0``); the write scope always applies.
    """
    c = get(action)
    after = Snapshot.load(root)
    if phase is None:
        phase = int(after.project.get("phase", 0) or 0)
    v = Verdict(action=action)
    tag = f"post-{action.upper()} invariant"

    if worker_exit == 0:
        msg = state_failure(action, {**after.project, "phase": phase}, after.phases)
        if msg:
            v.failures.append(f"{tag}: {msg}")
        if after.project.get("state") != ESCALATE_STATE:
            e = _Eval(root, action, phase, regime_of(after, phase), before, after)
            for name in c.requires:
                pc = POSTCONDITIONS[name]
                if pc.needs_baseline and before is None:
                    v.skipped.append(name)
                    continue
                msg = pc.check(e)
                if msg:
                    v.failures.append(f"{tag}: {msg}")

    if changed_paths is not None:
        rep = classify_paths(action, phase, changed_paths)
        if rep.protected:
            v.failures.append(
                f"post-{action.upper()} contract: protected path(s) changed: "
                + ", ".join(rep.protected)
            )
        v.flagged.extend(rep.out_of_scope)
        v.excluded.extend(rep.noise + rep.out_of_scope + rep.protected)
    return v


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def _code_list(items: tuple[str, ...]) -> str:
    return ", ".join(f"`{i}`" for i in items)


def render_markdown(action: str, phase: int, regime: str) -> str:
    """The ``## Action Contract`` prompt section. Static per (action, phase,
    regime) so the assembler stays pure."""
    c = get(action)
    scope, owned = c.scope(phase), c.owned(phase)
    if not scope:
        may = "nothing outside `.state/`."
    elif scope == ("**",):
        may = "any project file except the protected paths below."
    else:
        may = _code_list(scope) + " - nothing else outside `.state/`."
    never = _code_list(PROTECTED)
    if owned:
        never += " (except " + _code_list(owned) + ", which this action owns)"

    states = c.states_for(regime)
    musts = ["`project.json.state` is " + " or ".join(f"`{s}`" for s in states)]
    for name in c.requires:
        if name == "build_phase_has_pending_steps" and regime != "build":
            continue
        musts.append(POSTCONDITIONS[name].describe(action, phase))
    if action == "close":
        musts.append(f"the frozen acceptance suite for phase {phase} (if one exists) is unchanged")

    if c.escalation_ok:
        give_up = "set `state=audit_escalation` and emit `EXIT: 2`."
    else:
        give_up = "stop and emit `EXIT: 2` with the reason."
    lines = [
        "## Action Contract",
        "",
        "The runner checks this contract after you exit, before anything is "
        "committed. It is generated from the same table the check uses.",
        "",
        f"**You may change:** {may} Other files you change are left uncommitted "
        "and reported; `.state/` changes go through `i2c state`.",
        "",
        f"**Never change:** {never}. Changing one fails the iteration.",
        "",
        "**Before `EXIT: 0`, all of these must be true:**",
        "",
        *[f"- {m}" for m in musts],
        "",
        f"Run `i2c check --action {action}` before you emit the exit signal. If it "
        f"reports a failure, fix it; if you cannot, {give_up}",
    ]
    return "\n".join(lines)

"""i2c context assembler — builds the structured prompt workers receive.

Sits between the state machine (which decides what action to perform) and the
worker (which performs it). Reads `WORKER_SPEC.md`, `instructions/$ACTION.md`,
the adapter file (`CLAUDE.md` / `CODEX.md`), `.state/*.json`,
`.state/devlog.jsonl`, `PROJECT.md`, `ARCHITECTURE.md`, and
`ARCH_<module>.md`; emits a single markdown document on stdout.

The contract lives in `ARCH_assembler.md` (committed in repo). This module
implements it. Every section / flag / exit code maps to a section there.

Module organization (single file per D-impl-4):
  1. Constants + imports
  2. Error helpers (error_exit)
  3. Project root + path helpers
  4. State loaders (delegate to tools/validate.py)
  5. Markdown utilities (read, extract sections-with-markers)
  6. Evaluator registry for conditional sections
  7. Conditional stripper
  8. AssemblerContext dataclass + factory
  9. Per-section renderers (one function per canonical section)
 10. Banner assembler
 11. Action recipes + section recipes
 12. Top-level entry points (status, action, mid-step sections)
 13. CLI (argparse) + main
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import validate as v


# ---------------------------------------------------------------------------
# 1. Constants
# ---------------------------------------------------------------------------

BANNER_WIDTH = 47
BANNER_CHAR = "═"  # U+2550 box-drawings double horizontal
EMDASH = "—"  # U+2014

PLACEHOLDER_EMPTY = "<!-- empty -->"

ACTIONS = ("plan", "execute", "review", "close")
SECTIONS = ("status", "architecture", "module", "devlog", "phase-summary")
MODES = ("autonomous", "supervised")

# NEXT state computation per D-impl-3 (state-transition table used standalone).
# Maps current `state` value (i.e., the action just dispatched) to the next
# `state` the worker should set. CLOSE is the terminal action of a phase —
# `blocked: true` halts the loop until the human clears the gate.
_NEXT_BY_ACTION: dict[str, str] = {
    "plan": "execute",
    "execute": "execute",  # loop within execute; transitions to review on last step
    "review": "close",
    "close": "plan",
}


# ---------------------------------------------------------------------------
# 2. Error helpers
# ---------------------------------------------------------------------------


def error_exit(kind: str, file: str | Path, detail: str, code: int = 1) -> None:
    """Write a 3-line structured error to stderr and exit.

    Format per ARCH §11.1:
        ERROR: <kind>
        File: <path>
        Detail: <error>
    """
    sys.stderr.write(f"ERROR: {kind}\n")
    sys.stderr.write(f"File: {file}\n")
    sys.stderr.write(f"Detail: {detail}\n")
    sys.exit(code)


# ---------------------------------------------------------------------------
# 3. Project root + path helpers
# ---------------------------------------------------------------------------


def find_project_root(start: Path | None = None) -> Path:
    """Walk up from `start` (default CWD) looking for `.state/project.json`.

    The directory containing `.state/project.json` is the project root. All
    file lookups (PROJECT.md, ARCHITECTURE.md, ARCH_*.md, instructions/,
    adapter file) are relative to it.

    Uses ``.absolute()`` rather than ``.resolve()`` so the returned path
    keeps the form the caller is using. On Windows in particular,
    ``Path.cwd().resolve()`` expands a mapped network drive (e.g.
    ``P:\\shared\\foo``) to its UNC form (``\\\\host\\share\\foo``), which
    then breaks downstream ``subprocess.run(..., cwd=...)`` calls because
    CMD-based child processes (notably the claude CLI's plugin loader)
    cannot set a UNC path as their current directory. ``.absolute()``
    preserves the drive letter while still producing an absolute path
    that supports the parent walk below.
    """
    cwd = (start or Path.cwd()).absolute()
    for candidate in [cwd, *cwd.parents]:
        if (candidate / ".state" / "project.json").is_file():
            return candidate
    error_exit(
        "project root not found",
        str(cwd),
        "No .state/project.json found in CWD or any parent directory.",
    )
    raise AssertionError("unreachable")  # pragma: no cover


def state_path(root: Path, name: str) -> Path:
    return root / ".state" / name


def adapter_path(root: Path, backend: str) -> Path:
    return root / ("CLAUDE.md" if backend == "claude" else "CODEX.md")


def instruction_path(root: Path, action: str) -> Path:
    return root / "instructions" / f"{action}.md"


def arch_module_path(root: Path, module: str) -> Path:
    return root / f"ARCH_{module}.md"


# ---------------------------------------------------------------------------
# 4. State loaders
# ---------------------------------------------------------------------------


def _load_required_state(root: Path, name: str) -> Any:
    """Load a required .state/ JSON file with schema validation; exit 1 on failure."""
    path = state_path(root, name)
    if not path.is_file():
        error_exit(f"required state file missing", str(path), f"{name} not found")
    try:
        return v.validate_state_file(path)
    except ValueError as e:
        error_exit(f"state file schema-invalid", str(path), str(e))


def _load_optional_jsonl(root: Path, name: str) -> list[dict[str, Any]]:
    """Load a .jsonl file; return [] if missing, validate every line if present.

    devlog.jsonl is the only such file today. Per ARCH §11.4, validation
    failure on devlog.jsonl is treated as required (corrupt devlogs are bugs).
    """
    path = state_path(root, name)
    if not path.is_file():
        return []
    try:
        return v.validate_devlog_jsonl(path)
    except ValueError as e:
        error_exit(f"devlog schema-invalid", str(path), str(e))


def _load_optional_array(root: Path, name: str) -> list[Any]:
    """Load a .state/ array file with schema validation. Missing file → []."""
    path = state_path(root, name)
    if not path.is_file():
        return []
    try:
        return v.validate_state_file(path)
    except ValueError as e:
        error_exit(f"state file schema-invalid", str(path), str(e))


# ---------------------------------------------------------------------------
# 5. Markdown utilities
# ---------------------------------------------------------------------------


_MARKER_RE = re.compile(r"<!--\s*assembler:([A-Za-z_][A-Za-z0-9_]*)(?:=([^\s>-]+))?\s*-->")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def read_markdown(path: Path) -> str:
    """Read a markdown file as UTF-8. Exits 1 if missing."""
    if not path.is_file():
        error_exit(f"required markdown missing", str(path), f"{path.name} not found")
    return path.read_text(encoding="utf-8")


def parse_marker(line: str) -> tuple[str, str | None] | None:
    """Return (key, value) for a `<!-- assembler:KEY[=VALUE] -->` line, or None.

    The value is None for boolean-style markers.
    """
    m = _MARKER_RE.search(line)
    if not m:
        return None
    return m.group(1), m.group(2)


def heading_level(line: str) -> int | None:
    """Return the heading level (1-6) for an ATX heading line, else None."""
    m = _HEADING_RE.match(line)
    if not m:
        return None
    return len(m.group(1))


# ---------------------------------------------------------------------------
# 6. Evaluator registry
# ---------------------------------------------------------------------------


def _eval_dependencies_nonempty(ctx: "AssemblerContext", value: str | None) -> bool:
    """True iff phases.json[id == current_phase].dependencies has length > 0.

    Returns False when no record exists. Under ``--action plan`` against a
    fresh phase (no record yet) this means the dep-probe conditional
    section strips from the prompt; PLAN creates the record (step 4) and
    refines dependencies via ``update-record`` if the chosen module is
    non-leaf — a follow-up PLAN invocation then renders the probe. See
    DESIGN_state_lifecycle_v1.md §6.4.
    """
    record = ctx.current_phase_record()
    if record is None:
        return False
    deps = record.get("dependencies") or []
    return len(deps) > 0


def _eval_autonomous_only(ctx: "AssemblerContext", value: str | None) -> bool:
    """True iff mode is 'autonomous' (default)."""
    return ctx.mode == "autonomous"


def _eval_supervised_only(ctx: "AssemblerContext", value: str | None) -> bool:
    """True iff mode is 'supervised'. Reserved for future use; no current consumers."""
    return ctx.mode == "supervised"


def _eval_multi_step_only(ctx: "AssemblerContext", value: str | None) -> bool:
    """True iff the worker is invoked with a multi-step budget (STEP_BUDGET > 1).

    v1 runner always passes step_budget=1, so this evaluator strips the
    multi-step LOOP machinery (WORKER_SPEC §2 multi-step subsections) on
    every assembled prompt today. Forward-compatible with the multi-iteration
    loop landing later — once runners can pass --step-budget > 1, those
    sections automatically reappear.
    """
    return ctx.step_budget > 1


def _eval_omit_in_prompt(ctx: "AssemblerContext", value: str | None) -> bool:
    """Always False — sections marked this way are unconditionally stripped.

    Lets instruction-file authors keep operator-facing prose (Examples,
    Known tooling gaps, mode-discussion paragraphs) in `instructions/*.md`
    for direct reading, while keeping it out of the assembled worker prompt
    where the procedure itself already carries the load.
    """
    return False


# Keyed by marker name. `requires` is a composite key — the value names the
# evaluator to run (see ARCH §7.2 example `requires=dependencies_nonempty`).
EVALUATORS: dict[str, Callable[["AssemblerContext", str | None], bool]] = {
    "autonomous_only": _eval_autonomous_only,
    "supervised_only": _eval_supervised_only,
    "multi_step_only": _eval_multi_step_only,
    "omit_in_prompt": _eval_omit_in_prompt,
}

# Sub-evaluators for the `requires=` family. Adding a new condition means
# registering a new entry here.
REQUIRES_EVALUATORS: dict[str, Callable[["AssemblerContext"], bool]] = {
    "dependencies_nonempty": lambda ctx: _eval_dependencies_nonempty(ctx, None),
}


def evaluate_marker(key: str, value: str | None, ctx: "AssemblerContext") -> bool:
    """Return True if the marker's condition is satisfied (i.e., keep the section)."""
    if key == "requires":
        if value is None:
            return False
        sub = REQUIRES_EVALUATORS.get(value)
        if sub is None:
            return False
        return sub(ctx)
    evaluator = EVALUATORS.get(key)
    if evaluator is None:
        # Unknown markers default to keeping the section (conservative).
        return True
    return evaluator(ctx, value)


# ---------------------------------------------------------------------------
# 7. Conditional stripper
# ---------------------------------------------------------------------------


def strip_conditional_sections(markdown: str, ctx: "AssemblerContext") -> str:
    """Strip headings whose `<!-- assembler:KEY[=VALUE] -->` markers evaluate False.

    A marker applies to the section starting at the immediately preceding
    heading and ending at the next heading of equal or shallower level (or
    EOF). The marker comment line itself is also dropped from the output.
    """
    lines = markdown.splitlines()
    n = len(lines)

    # First pass: locate (heading_index, marker_dict) for every heading. A
    # marker is recognized only if it appears on a line within the heading's
    # body before any other heading.
    # Find sections to drop by index ranges.
    drop_ranges: list[tuple[int, int]] = []  # half-open [start, end)
    drop_marker_lines: set[int] = set()

    i = 0
    while i < n:
        level = heading_level(lines[i])
        if level is None:
            i += 1
            continue
        # Find next heading of <= level (or EOF).
        end = n
        for j in range(i + 1, n):
            lvl_j = heading_level(lines[j])
            if lvl_j is not None and lvl_j <= level:
                end = j
                break
        # Look for a marker between i+1 and the first non-blank, non-marker
        # content line (or end). Per ARCH §7.1 the marker is "on its own
        # line" after the heading — typically the next non-blank line.
        marker: tuple[str, str | None] | None = None
        marker_line_idx: int | None = None
        for k in range(i + 1, end):
            stripped = lines[k].strip()
            if not stripped:
                continue
            m = parse_marker(stripped)
            if m is not None:
                marker = m
                marker_line_idx = k
            # Stop searching after the first content line either way.
            break
        if marker is not None:
            key, value = marker
            keep = evaluate_marker(key, value, ctx)
            if not keep:
                drop_ranges.append((i, end))
            else:
                # Keep the section but drop the marker comment line so it
                # doesn't leak into the worker's prompt.
                if marker_line_idx is not None:
                    drop_marker_lines.add(marker_line_idx)
        # Advance by 1, not to `end`, so nested headings inside a higher-level
        # heading's section also get their markers evaluated.
        i += 1

    if not drop_ranges and not drop_marker_lines:
        return markdown

    drop_set: set[int] = set(drop_marker_lines)
    for start, end in drop_ranges:
        for idx in range(start, end):
            drop_set.add(idx)
    kept = [ln for idx, ln in enumerate(lines) if idx not in drop_set]
    # Preserve trailing newline behavior: if original ended with \n, keep it.
    trailing_nl = markdown.endswith("\n")
    out = "\n".join(kept)
    if trailing_nl and not out.endswith("\n"):
        out += "\n"
    return out


# ---------------------------------------------------------------------------
# 8. AssemblerContext
# ---------------------------------------------------------------------------


@dataclass
class AssemblerContext:
    """Cross-cutting context threaded through every renderer."""

    project_root: Path
    backend: str  # "claude" | "codex"
    mode: str  # "autonomous" | "supervised"
    action: str | None  # None for --section invocations
    section: str | None  # None for --action invocations
    phase: int | None  # required for --action and --section devlog
    module: str | None  # required for --section module
    step_budget: int = 1  # runner-supplied; v1 always 1. Drives multi_step_only stripping.
    # State (lazy-populated; populated by build_context).
    project: dict[str, Any] = field(default_factory=dict)
    phases: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    decisions: list[dict[str, Any]] = field(default_factory=list)
    devlog: list[dict[str, Any]] = field(default_factory=list)

    def current_phase_id(self) -> int:
        """Phase id used by renderers. For --action use, prefer the CLI phase;
        for --section status, fall back to project.json.phase."""
        if self.phase is not None:
            return self.phase
        return int(self.project.get("phase", 0))

    def current_phase_record(self) -> dict[str, Any] | None:
        pid = self.current_phase_id()
        for record in self.phases:
            if record.get("id") == pid:
                return record
        return None


def build_context(args: argparse.Namespace) -> AssemblerContext:
    """Load state files and build the AssemblerContext for the given CLI args."""
    root = find_project_root()
    ctx = AssemblerContext(
        project_root=root,
        backend=args.backend,
        mode=getattr(args, "mode", "autonomous") or "autonomous",
        action=getattr(args, "action", None),
        section=getattr(args, "section", None),
        phase=getattr(args, "phase", None),
        module=getattr(args, "module", None),
        step_budget=getattr(args, "step_budget", 1) or 1,
    )
    ctx.project = _load_required_state(root, "project.json")
    ctx.phases = _load_required_state(root, "phases.json")
    ctx.steps = _load_required_state(root, "steps.json")
    ctx.decisions = _load_optional_array(root, "decisions.json")
    ctx.devlog = _load_optional_jsonl(root, "devlog.jsonl")
    return ctx


# ---------------------------------------------------------------------------
# 9. Section renderers (status snapshot)
#
# Per ARCH §8 the `--section status` output is a fast-to-read snapshot,
# distinct from the full assembled prompt: no Worker Contract banner, no
# instructions, no module contract — just orientation.
# ---------------------------------------------------------------------------


def _fmt_dependencies(deps: list[str] | None) -> str:
    if not deps:
        return "(none — leaf module)"
    return ", ".join(deps)


def render_status_project(ctx: AssemblerContext) -> str:
    """## Project Status — phase, state, budget, module, dependencies."""
    p = ctx.project
    phase_id = p.get("phase", 0)
    record = ctx.current_phase_record()
    if record is None:
        # Status snapshot is tolerant — if no record matches (e.g., phase 0),
        # render what we can and skip module/dependencies.
        title = "(no phases.json record)"
        regime = "—"
        module = "—"
        deps = "—"
    else:
        title = record.get("title", "—")
        regime = record.get("regime", "—").title()
        module = record.get("module", "—")
        deps = _fmt_dependencies(record.get("dependencies"))

    lines = ["## Project Status", ""]
    lines.append(f"**Phase:** {phase_id} ({module}) {EMDASH} {title} ({regime})")
    lines.append(f"**State:** {p.get('state', '—')}")
    if "steps_remaining" in p:
        lines.append(f"**Budget:** steps_remaining={p['steps_remaining']}")
    elif p.get("budget_type") == "time" and "time_budget_seconds" in p:
        lines.append(f"**Budget:** time_budget_seconds={p['time_budget_seconds']}")
    if module != "—":
        lines.append(f"**Module:** {module}")
        lines.append(f"**Dependencies:** {deps}")
    return "\n".join(lines)


def render_current_phase_steps_table(ctx: AssemblerContext) -> str:
    """## Current Phase Steps — markdown table filtered to current phase."""
    pid = ctx.current_phase_id()
    steps = [s for s in ctx.steps if s.get("phase") == pid]
    steps.sort(key=lambda s: s.get("step", 0))
    lines = ["## Current Phase Steps", ""]
    if not steps:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    lines.append("| Step | Title | Status | Commit |")
    lines.append("|------|-------|--------|--------|")
    for s in steps:
        step_id = f"{pid}.{s.get('step', '?')}"
        title = s.get("title", "")
        status = s.get("status", "")
        commit = s.get("commit", "—") or "—"
        lines.append(f"| {step_id} | {title} | {status} | {commit} |")
    return "\n".join(lines)


def render_gotchas(ctx: AssemblerContext) -> str:
    """## Gotchas — bullets from project.json.gotchas."""
    gotchas = ctx.project.get("gotchas") or []
    lines = ["## Gotchas", ""]
    if not gotchas:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for g in gotchas:
        lines.append(f"- {g}")
    return "\n".join(lines)


def _fmt_devlog_bullet(entry: dict[str, Any]) -> str:
    """Format one devlog entry: `phase.step action → outcome (commit) — summary`.

    `step` may be null for phase-level entries; render `phase` only in that
    case. Commit is omitted if absent.
    """
    phase = entry.get("phase", "?")
    step = entry.get("step")
    if step is None:
        action_id = f"{phase}"
    else:
        action_id = f"{phase}.{step}"
    action = entry.get("action", "")
    outcome = entry.get("outcome", "")
    commit = entry.get("commit")
    summary = entry.get("summary", "")
    head = f"{action_id} {action} \u2192 {outcome}"
    if commit:
        head += f" ({commit})"
    return f"- {head} {EMDASH} {summary}"


def render_recent_activity(ctx: AssemblerContext, n: int = 3) -> str:
    """## Recent Activity (last N devlog entries) — project-wide tail."""
    lines = [f"## Recent Activity (last {n} devlog entries)", ""]
    entries = ctx.devlog[-n:][::-1] if ctx.devlog else []
    if not entries:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for e in entries:
        lines.append(_fmt_devlog_bullet(e))
    return "\n".join(lines)


def render_open_decisions(ctx: AssemblerContext) -> str:
    """## Open Decisions — bullets, filtered to status=='open'."""
    open_d = [d for d in ctx.decisions if d.get("status") == "open"]
    lines = ["## Open Decisions", ""]
    if not open_d:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for d in open_d:
        did = d.get("id", "?")
        priority = d.get("priority", "—")
        title = d.get("title", "")
        decision = d.get("decision", "")
        lines.append(f"- {did} [{priority} \u00b7 open] {title} {EMDASH} {decision}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 10. Section renderers — full-prompt sections (per ARCH §4 + §6)
#
# Renderers below take an AssemblerContext and return a string (with its
# `## Heading`) or an empty string if the section is omitted entirely.
# Banner-grouping is handled by the action recipes (§11), not the renderers.
# ---------------------------------------------------------------------------


def _extract_after_first_h2(markdown: str) -> str:
    """Skip H1 title + intro prose; return markdown from the first H2 onwards.

    Used for WORKER_SPEC.md and instructions/$ACTION.md inclusion. Removes
    the top-level title + frontmatter that would clash with the surrounding
    banner structure.
    """
    lines = markdown.splitlines()
    trailing_nl = markdown.endswith("\n")
    for i, ln in enumerate(lines):
        if heading_level(ln) == 2:
            tail = "\n".join(lines[i:])
            if trailing_nl and not tail.endswith("\n"):
                tail += "\n"
            return tail
    return ""


def _extract_section_by_heading(markdown: str, heading_text: str) -> str | None:
    """Return the markdown for one H2/H3 section identified by exact heading text.

    Used for adapter sub-section extraction (Tool Rules, Available Modules).
    Match is case-sensitive on the heading's text after `## `. Returns the
    section *body* including the heading line, ending at the next heading of
    equal-or-shallower level (or EOF). Returns None if no match.
    """
    lines = markdown.splitlines()
    n = len(lines)
    start_idx: int | None = None
    start_level: int | None = None
    for i, ln in enumerate(lines):
        lvl = heading_level(ln)
        if lvl is None:
            continue
        m = _HEADING_RE.match(ln)
        if m and m.group(2).strip() == heading_text:
            start_idx = i
            start_level = lvl
            break
    if start_idx is None or start_level is None:
        return None
    end = n
    for j in range(start_idx + 1, n):
        lvl_j = heading_level(lines[j])
        if lvl_j is not None and lvl_j <= start_level:
            end = j
            break
    return "\n".join(lines[start_idx:end]).rstrip() + "\n"


def _is_placeholder_only(body: str) -> bool:
    """True if a section body contains only HTML comments / blank lines / the heading.

    Used by the Available Modules fallback (§4.3): if the adapter's
    Available Modules section is only the `<!-- List tracks ... -->`
    placeholder, fall back to ARCHITECTURE.md. Handles both single-line
    and multi-line HTML comments.
    """
    in_comment = False
    for ln in body.splitlines():
        stripped = ln.strip()
        if not stripped:
            continue
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("#"):
            continue
        if stripped.startswith("<!--"):
            # Opening comment; check whether it closes on the same line.
            if "-->" not in stripped[4:]:
                in_comment = True
            continue
        return False
    return True


# --- Worker Contract banner content ----------------------------------------


def render_worker_spec(ctx: AssemblerContext) -> str:
    """Worker Contract banner body: WORKER_SPEC.md from first H2, conditional-stripped.

    Wraps Identity / Main Loop / Escalation Conditions / Output Contract /
    Autonomous Behavioral Rules / Prohibitions per ARCH §4. Mode framing
    (autonomous_only) is applied by strip_conditional_sections.
    """
    text = read_markdown(ctx.project_root / "WORKER_SPEC.md")
    stripped = strip_conditional_sections(text, ctx)
    body = _extract_after_first_h2(stripped)
    return body.rstrip("\n")


# --- Action Context banner content -----------------------------------------


_OUTPUT_CONTRACT_REMINDER = """\
**End your response with EXACTLY these two lines. No prose after.**

```
EXIT: 0 | 2
REASON: <one-line summary>
```

The runner parses these via line-anchored regex. Omitting them causes the
iteration to be reported as `exit=2 "signal missing or malformed"` even if
your work landed correctly in `.state/` and the commit. See your adapter's
`## Output Contract` section for full semantics.
"""


def render_action_heading(ctx: AssemblerContext) -> str:
    """## Action: $TYPE — or ## Active Action: $TYPE under --mode supervised (§9.2)."""
    if ctx.action is None:
        return ""
    label = "Active Action" if ctx.mode == "supervised" else "Action"
    return f"## {label}: {ctx.action.upper()}"


def compute_next_state(ctx: AssemblerContext) -> str:
    """Default NEXT computation per D-impl-3 (Phase 3 may override via --next).

    The current state-transition table mirrors what state_machine.py
    eventually emit. EXECUTE → execute (loop) by default; the worker
    transitions to review on the last step via state.py.
    """
    return _NEXT_BY_ACTION.get(ctx.action or "", "plan")


def render_next_state(ctx: AssemblerContext) -> str:
    """## Next State: $STATE — stripped under --mode supervised (§9.2)."""
    if ctx.action is None or ctx.mode == "supervised":
        return ""
    return f"## Next State: {compute_next_state(ctx)}"


def render_phase_heading(ctx: AssemblerContext) -> str:
    """## Phase: N — Title (Regime) — title and regime sourced from phases.json.

    Tolerance: when ``--action plan`` AND no phases.json record exists for
    the current phase, render a stub heading instead of error_exit. PLAN's
    procedure (``instructions/plan.md`` step 4) creates the record. Any
    other action hitting a missing record is a real misdispatch and still
    fails fast. See DESIGN_state_lifecycle_v1.md §6.4.
    """
    record = ctx.current_phase_record()
    if record is None:
        if ctx.action == "plan":
            return (
                f"## Phase: {ctx.current_phase_id()} {EMDASH} "
                "(record to be created by PLAN)"
            )
        # Required-input failure per §4.2: phases.json must list current phase.
        error_exit(
            "phases.json missing current-phase record",
            str(state_path(ctx.project_root, "phases.json")),
            f"No record with id == {ctx.current_phase_id()}",
        )
    title = record.get("title", "")
    regime = record.get("regime", "").title()
    return f"## Phase: {ctx.current_phase_id()} {EMDASH} {title} ({regime})"


def render_step_heading(ctx: AssemblerContext) -> str:
    """## Step: N — Title — EXECUTE only, lowest-numbered pending step in phase.

    Returns "" if no pending step exists (state machine should not have
    dispatched EXECUTE in that case — per ARCH §4.1).
    """
    if ctx.action != "execute":
        return ""
    pid = ctx.current_phase_id()
    pending = [s for s in ctx.steps if s.get("phase") == pid and s.get("status") == "pending"]
    if not pending:
        return ""
    pending.sort(key=lambda s: s.get("step", 0))
    step = pending[0]
    return f"## Step: {pid}.{step.get('step', '?')} {EMDASH} {step.get('title', '')}"


def render_instructions(ctx: AssemblerContext) -> str:
    """## Instructions — heading + body of instructions/$ACTION.md (conditional-stripped)."""
    if ctx.action is None:
        return ""
    path = instruction_path(ctx.project_root, ctx.action)
    text = read_markdown(path)
    stripped = strip_conditional_sections(text, ctx)
    body = _extract_after_first_h2(stripped)
    return f"## Instructions\n\n{body.rstrip()}"


# --- Project Context: Module Contract --------------------------------------


def render_module_contract(ctx: AssemblerContext) -> str:
    """## Module Contract: NAME — full ARCH_<module>.md (verbatim).

    Per ARCH §11.1: required if phases.json[current].module is set;
    omitted entirely if not.
    """
    record = ctx.current_phase_record()
    if record is None:
        return ""
    module = record.get("module")
    if not module:
        return ""
    path = arch_module_path(ctx.project_root, module)
    if not path.is_file():
        error_exit(
            "module contract missing",
            str(path),
            f"ARCH_{module}.md not found for current phase's module",
        )
    text = path.read_text(encoding="utf-8")
    body = _extract_after_first_h2(text) or text
    return f"## Module Contract: {module}\n\n{body.rstrip()}"


# --- Project Context: state-derived sections -------------------------------


def render_project_state(ctx: AssemblerContext) -> str:
    """## Project State — pretty-printed JSON in a fenced ```json``` block (§12)."""
    body = json.dumps(ctx.project, indent=2, ensure_ascii=False)
    return f"## Project State\n\n```json\n{body}\n```"


def render_current_phase(ctx: AssemblerContext) -> str:
    """## Current Phase — the phases.json record for the current phase, as a table.

    Tolerance: when ``--action plan`` AND no record exists, render a
    placeholder noting that PLAN will create the record. Other actions
    hitting a missing record render the standard empty marker (which is
    typically unreachable in practice — render_phase_heading fails first).
    """
    record = ctx.current_phase_record()
    lines = ["## Current Phase", ""]
    if record is None:
        if ctx.action == "plan":
            lines.append(
                "<!-- no phases.json record yet for phase "
                f"{ctx.current_phase_id()}; PLAN will create it "
                "(see instructions/plan.md step 4) -->"
            )
        else:
            lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    # Column order from schema declaration: id, module, title, regime,
    # dependencies, status.
    lines.append("| id | module | title | regime | dependencies | status |")
    lines.append("|----|--------|-------|--------|--------------|--------|")
    deps = record.get("dependencies") or []
    deps_str = ", ".join(deps) if deps else "(none)"
    lines.append(
        f"| {record.get('id', '?')} | {record.get('module', '—')} | "
        f"{record.get('title', '')} | {record.get('regime', '')} | "
        f"{deps_str} | {record.get('status', '')} |"
    )
    return "\n".join(lines)


def render_phases(ctx: AssemblerContext) -> str:
    """## Phases — one-line per phase summary (PLAN only; ARCH §4.1).

    Format: `id, module, regime, status` per ARCH §4.1.
    """
    lines = ["## Phases", ""]
    if not ctx.phases:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for p in ctx.phases:
        lines.append(
            f"- {p.get('id', '?')}, {p.get('module', '—')}, "
            f"{p.get('regime', '')}, {p.get('status', '')}"
        )
    return "\n".join(lines)


def render_phase_devlog(ctx: AssemblerContext) -> str:
    """## Phase Devlog — devlog.jsonl filtered to current phase, full history."""
    pid = ctx.current_phase_id()
    entries = [e for e in ctx.devlog if e.get("phase") == pid]
    lines = ["## Phase Devlog", ""]
    if not entries:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for e in entries:
        lines.append(_fmt_devlog_bullet(e))
    return "\n".join(lines)


def render_prior_phase_summary(ctx: AssemblerContext) -> str:
    """## Prior Phase Summary — devlog for (current phase − 1), last 3 entries.

    Omitted entirely when current phase is 1 (no prior phase). Per ARCH §11.2.
    """
    pid = ctx.current_phase_id()
    if pid <= 1:
        return ""
    prior = pid - 1
    entries = [e for e in ctx.devlog if e.get("phase") == prior]
    lines = ["## Prior Phase Summary", ""]
    if not entries:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for e in entries[-3:]:
        lines.append(_fmt_devlog_bullet(e))
    return "\n".join(lines)


def render_decisions(ctx: AssemblerContext) -> str:
    """## Decisions — all decision records, table form.

    Column order from schema declaration: id, title, status, priority,
    decision, rationale, revisit_if, timestamp.
    """
    lines = ["## Decisions", ""]
    if not ctx.decisions:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    lines.append("| id | title | status | priority | decision |")
    lines.append("|----|-------|--------|----------|----------|")
    for d in ctx.decisions:
        # Inline pipes in `decision` text would break the table; replace.
        decision = (d.get("decision", "") or "").replace("|", "\\|")
        lines.append(
            f"| {d.get('id', '?')} | {d.get('title', '')} | {d.get('status', '')} | "
            f"{d.get('priority', '—')} | {decision} |"
        )
    return "\n".join(lines)


# --- Project Context: project narrative ------------------------------------


def render_project_scope(ctx: AssemblerContext) -> str:
    """## Project Scope — PROJECT.md verbatim. Missing → placeholder (§11.2)."""
    path = ctx.project_root / "PROJECT.md"
    if not path.is_file():
        return "## Project Scope\n\n<!-- not present: PROJECT.md not found -->"
    text = path.read_text(encoding="utf-8")
    body = _extract_after_first_h2(text) or text
    return f"## Project Scope\n\n{body.rstrip()}"


def render_architecture(ctx: AssemblerContext) -> str:
    """## Architecture — ARCHITECTURE.md verbatim. Missing → placeholder (§11.2)."""
    path = ctx.project_root / "ARCHITECTURE.md"
    if not path.is_file():
        return "## Architecture\n\n<!-- not present: ARCHITECTURE.md not found -->"
    text = path.read_text(encoding="utf-8")
    body = _extract_after_first_h2(text) or text
    return f"## Architecture\n\n{body.rstrip()}"


# --- Tool Rules + Available Modules (adapter-driven) -----------------------


def _adapter_text(ctx: AssemblerContext) -> str:
    """Read the adapter file (CLAUDE.md or CODEX.md). Required (§11.1)."""
    return read_markdown(adapter_path(ctx.project_root, ctx.backend))


def render_tool_rules(ctx: AssemblerContext) -> str:
    """Tool Rules section from the adapter file (§4.1, §6).

    Adapter heading is "Claude-Specific Tool Rules" or "Codex-Specific Tool Rules".
    """
    text = _adapter_text(ctx)
    heading = "Claude-Specific Tool Rules" if ctx.backend == "claude" else "Codex-Specific Tool Rules"
    body = _extract_section_by_heading(text, heading)
    if body is None:
        return f"## {heading}\n\n{PLACEHOLDER_EMPTY}"
    return body.rstrip()


def render_available_modules(ctx: AssemblerContext) -> str:
    """## Available Modules — adapter section, falling back to ARCHITECTURE.md (§4.3)."""
    adapter = _adapter_text(ctx)
    section = _extract_section_by_heading(adapter, "Available Modules")
    if section is not None and not _is_placeholder_only(section):
        return section.rstrip()
    # Fallback: scan ARCHITECTURE.md for an Implementation Sequence heading.
    arch_path = ctx.project_root / "ARCHITECTURE.md"
    if arch_path.is_file():
        arch_text = arch_path.read_text(encoding="utf-8")
        impl = _extract_section_by_heading(arch_text, "Implementation Sequence")
        if impl is not None and not _is_placeholder_only(impl):
            # Use canonical heading; surface the impl body underneath.
            body_lines = impl.splitlines()
            # Drop the source heading line (we add our own canonical one).
            if body_lines and heading_level(body_lines[0]) is not None:
                body_lines = body_lines[1:]
            body = "\n".join(body_lines).strip("\n")
            return f"## Available Modules\n\n{body}"
    return f"## Available Modules\n\n{PLACEHOLDER_EMPTY}"


# ---------------------------------------------------------------------------
# 11. Banner assembler + action recipes
# ---------------------------------------------------------------------------


def assemble_banner(title: str) -> str:
    """Box-drawing banner per ARCH §6.

    Format: 47 box-drawing characters (U+2550) per band line, the title
    on its own line between them, blank line after the closing band.
    """
    line = BANNER_CHAR * BANNER_WIDTH
    return f"{line}\n{title}\n{line}"


def render_recent_activity_5(ctx: AssemblerContext) -> str:
    """EXECUTE-mode Recent Activity uses last 5 entries (§4.1)."""
    return render_recent_activity(ctx, n=5)


# Per ARCH §5 (Assembly Matrix) — Project Context section ordering by action.
_PROJECT_CONTEXT_BY_ACTION: dict[str, list[Callable[[AssemblerContext], str]]] = {
    "plan": [
        render_module_contract,
        render_project_state,
        render_gotchas,
        render_current_phase,
        render_phases,
        render_prior_phase_summary,
        render_project_scope,
        render_architecture,
        render_decisions,
    ],
    "execute": [
        render_module_contract,
        render_project_state,
        render_gotchas,
        render_current_phase,
        render_current_phase_steps_table,
        render_recent_activity_5,
        # Decisions intentionally omitted from EXECUTE (Phase 3.A.2): project-
        # wide decision history is reference, not per-step load-bearing.
        # Worker can pull it mid-step via `--section` if a step genuinely
        # needs it. PLAN / REVIEW / CLOSE still include it.
    ],
    "review": [
        render_module_contract,
        render_project_state,
        render_gotchas,
        render_current_phase,
        render_current_phase_steps_table,
        render_phase_devlog,
        render_architecture,
        render_decisions,
    ],
    "close": [
        render_module_contract,
        render_project_state,
        render_gotchas,
        render_current_phase,
        render_current_phase_steps_table,
        render_phase_devlog,
        render_decisions,
    ],
}


# Actions where Available Modules adds value (i.e., Architecture isn't already
# in the prompt). For PLAN and REVIEW the Component Map inside Architecture
# covers the same ground; rendering both is pure duplication. EXECUTE and
# CLOSE don't get Architecture, so Available Modules earns its place there.
_AVAILABLE_MODULES_ACTIONS = ("execute", "close")


def _stable_prefix_parts(ctx: AssemblerContext) -> list[str]:
    """Region 1 (WORKER CONTRACT) + Region 2 (TOOL RULES) parts.

    The cache-stable prefix: byte-identical across consecutive
    same-phase, same-action invocations, so the runner can route it
    through Claude Code's system prompt for prompt-cache reuse (FU-35).
    """
    parts: list[str] = []

    # Region 1: Worker Contract
    parts.append(assemble_banner("WORKER CONTRACT"))
    worker = render_worker_spec(ctx).rstrip()
    if worker:
        parts.append(worker)

    # Region 2: Tool Rules (early so the worker knows the environment before
    # reading the procedure that names its tools).
    parts.append(assemble_banner("TOOL RULES"))
    tool_renderers: list[Callable[[AssemblerContext], str]] = [render_tool_rules]
    if ctx.action in _AVAILABLE_MODULES_ACTIONS:
        tool_renderers.append(render_available_modules)
    for renderer in tool_renderers:
        chunk = renderer(ctx)
        if chunk:
            parts.append(chunk.rstrip())

    return parts


def _volatile_body_parts(ctx: AssemblerContext) -> list[str]:
    """Region 3 (PROJECT CONTEXT) + Region 4 (ACTION CONTEXT) + Region 5
    (Output Contract reminder) parts.

    Everything that changes per phase / step / iteration. Kept out of the
    cache-stable prefix so the prefix can be reused across iterations.
    """
    parts: list[str] = []

    # Region 3: Project Context (per-action ordering from §5)
    parts.append(assemble_banner("PROJECT CONTEXT"))
    for renderer in _PROJECT_CONTEXT_BY_ACTION[ctx.action]:
        chunk = renderer(ctx)
        if chunk:
            parts.append(chunk.rstrip())

    # Region 4: Action Context (last — the model's most recent context is
    # exactly what it's about to do).
    parts.append(assemble_banner("ACTION CONTEXT"))
    for renderer in (
        render_action_heading,
        render_next_state,
        render_phase_heading,
        render_step_heading,
        render_instructions,
    ):
        chunk = renderer(ctx)
        if chunk:
            parts.append(chunk.rstrip())

    # Region 5: Output Contract reminder (absolute tail — recency anchor so
    # workers whose model defaults conversational, like newer codex, are
    # less likely to skip the 2-line exit signal). Autonomous-only,
    # matching the existing autonomous_only convention for Output Contract
    # in WORKER_SPEC — supervised reviewers read the worker output directly
    # and don't need a parseable signal block.
    if ctx.mode == "autonomous":
        parts.append(assemble_banner("OUTPUT CONTRACT — REMINDER"))
        parts.append(_OUTPUT_CONTRACT_REMINDER.rstrip())

    return parts


def _join_parts(parts: list[str]) -> str:
    """Join banner-delimited parts with the canonical separator + trailing newline."""
    body = "\n\n".join(parts)
    if not body.endswith("\n"):
        body += "\n"
    return body


def build_stable_prefix(ctx: AssemblerContext) -> str:
    """Cache-stable prefix (regions 1–2) — the `--emit system` projection."""
    if ctx.action is None:
        raise ValueError("build_stable_prefix requires an action")
    return _join_parts(_stable_prefix_parts(ctx))


def build_volatile_body(ctx: AssemblerContext) -> str:
    """Per-iteration body (regions 3–5) — the `--emit user` projection."""
    if ctx.action is None:
        raise ValueError("build_volatile_body requires an action")
    return _join_parts(_volatile_body_parts(ctx))


def build_full_prompt(ctx: AssemblerContext) -> str:
    """Assemble the full prompt per ARCH §6 ordering and §5 inclusion matrix.

    Renderers may return empty strings for omitted sections (e.g., Next State
    under supervised mode, Module Contract when no module field). Those drop
    out of the banner-joined output cleanly.

    Region order (Phase 3.A.1): WORKER CONTRACT → TOOL RULES → PROJECT
    CONTEXT → ACTION CONTEXT. Identity framing first, environment rules
    early, reference material in the middle, action procedure last so
    model recency works in our favor.

    Output is byte-identical to ``build_stable_prefix(ctx).rstrip() +
    "\\n\\n" + build_volatile_body(ctx)`` — the `--emit system` /
    `--emit user` projections (FU-35) are exactly this prompt's two halves.
    """
    if ctx.action is None:
        raise ValueError("build_full_prompt requires an action")
    return _join_parts(_stable_prefix_parts(ctx) + _volatile_body_parts(ctx))


# ---------------------------------------------------------------------------
# 12. Top-level entry points
# ---------------------------------------------------------------------------


def build_section_status(ctx: AssemblerContext) -> str:
    """`--section status` per ARCH §8.

    Returns the assembled markdown for the status snapshot, with a trailing
    newline per output invariants (§12).
    """
    parts = [
        render_status_project(ctx),
        render_current_phase_steps_table(ctx),
        render_gotchas(ctx),
        render_recent_activity(ctx, n=3),
        render_open_decisions(ctx),
    ]
    body = "\n\n".join(parts)
    if not body.endswith("\n"):
        body += "\n"
    return body


def build_section_architecture(ctx: AssemblerContext) -> str:
    """`--section architecture` — verbatim ARCHITECTURE.md (per ARCH §10).

    Missing file degrades to a placeholder per §11.2 (architecture is optional).
    """
    path = ctx.project_root / "ARCHITECTURE.md"
    if not path.is_file():
        return "<!-- not present: ARCHITECTURE.md not found -->\n"
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    return text


def build_section_module(ctx: AssemblerContext) -> str:
    """`--section module --module NAME` — verbatim ARCH_<module>.md.

    Missing file exits 1 (per §11.1) — the caller asked for this specific
    module's contract, so absence is a required-input failure.
    """
    if not ctx.module:
        error_exit(
            "missing --module argument",
            "<cli>",
            "--module is required with --section module",
            code=2,
        )
    path = arch_module_path(ctx.project_root, ctx.module)
    if not path.is_file():
        error_exit(
            "module contract missing",
            str(path),
            f"ARCH_{ctx.module}.md not found",
        )
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    return text


def build_section_devlog(ctx: AssemblerContext) -> str:
    """`--section devlog --phase N` — bulleted summary filtered to phase N."""
    pid = ctx.current_phase_id()
    entries = [e for e in ctx.devlog if e.get("phase") == pid]
    lines = [f"## Phase {pid} Devlog", ""]
    if not entries:
        lines.append(PLACEHOLDER_EMPTY)
    else:
        for e in entries:
            lines.append(_fmt_devlog_bullet(e))
    body = "\n".join(lines)
    if not body.endswith("\n"):
        body += "\n"
    return body


# ---------------------------------------------------------------------------
# 12b. --section phase-summary
#
# Operator's view at phase boundary (audit_boundary): "what happened in
# phase N". Renders header + steps + decisions-added-this-phase + phase
# devlog + open items. Distinct from --section status (which is
# project-wide, current-state-focused) and from --section devlog
# (which is just the devlog tail). See ARCH_assembler.md §8b.
# ---------------------------------------------------------------------------


def render_phase_summary_header(ctx: AssemblerContext, phase_id: int) -> str:
    """## Phase N Summary — module: title (Regime, Status) + header table."""
    record = None
    for p in ctx.phases:
        if p.get("id") == phase_id:
            record = p
            break
    if record is None:
        return (
            f"## Phase {phase_id} Summary {EMDASH} (no phases.json record)\n\n"
            f"{PLACEHOLDER_EMPTY}"
        )
    module = record.get("module", "—")
    title = record.get("title", "")
    regime = (record.get("regime", "") or "").title()
    status = record.get("status", "")
    deps = _fmt_dependencies(record.get("dependencies"))

    # Compute devlog span: first → last timestamp for entries tagged to this phase.
    phase_entries = [e for e in ctx.devlog if e.get("phase") == phase_id]
    timestamps = [e.get("timestamp") for e in phase_entries if e.get("timestamp")]
    if timestamps:
        timestamps.sort()
        spans = f"{timestamps[0]} {EMDASH} {timestamps[-1]} ({len(phase_entries)} devlog entries)"
    else:
        spans = f"(no devlog entries)"

    head = f"## Phase {phase_id} Summary {EMDASH} {module}: {title} ({regime}, {status})"
    lines = [
        head,
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Module | {module} |",
        f"| Regime | {record.get('regime', '')} |",
        f"| Dependencies | {deps} |",
        f"| Status | {status} |",
        f"| Spans | {spans} |",
    ]
    return "\n".join(lines)


def render_phase_summary_steps(ctx: AssemblerContext, phase_id: int) -> str:
    """## Steps — table filtered to this phase, in step-number order."""
    steps = [s for s in ctx.steps if s.get("phase") == phase_id]
    steps.sort(key=lambda s: s.get("step", 0))
    lines = ["## Steps", ""]
    if not steps:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    lines.append("| Step | Title | Status | Commit |")
    lines.append("|------|-------|--------|--------|")
    for s in steps:
        step_id = f"{phase_id}.{s.get('step', '?')}"
        title = s.get("title", "")
        status = s.get("status", "")
        commit = s.get("commit", "—") or "—"
        lines.append(f"| {step_id} | {title} | {status} | {commit} |")
    return "\n".join(lines)


def render_phase_summary_decisions(ctx: AssemblerContext, phase_id: int) -> str:
    """## Decisions Added in This Phase — filtered by decision.phase == N.

    Δ1 (optional `phase` field on decisions.schema.json) is the clean
    filter. Decisions authored before Δ1 lack the field and will not
    appear; a footer note explains how to back-fill.
    """
    phase_decisions = [d for d in ctx.decisions if d.get("phase") == phase_id]
    untagged = [d for d in ctx.decisions if "phase" not in d]
    lines = ["## Decisions Added in This Phase", ""]
    if not phase_decisions:
        lines.append(PLACEHOLDER_EMPTY)
    else:
        for d in phase_decisions:
            did = d.get("id", "?")
            status = d.get("status", "")
            priority = d.get("priority", "—")
            title = d.get("title", "")
            decision = d.get("decision", "")
            lines.append(f"- **{did}** [{status} · {priority}] {title}")
            lines.append(f"  Decision: {decision}")
            rationale = d.get("rationale")
            if rationale:
                lines.append(f"  Rationale: {rationale}")
            revisit = d.get("revisit_if")
            if revisit:
                lines.append(f"  Revisit if: {revisit}")
    if untagged:
        lines.append("")
        lines.append(
            f"<!-- {len(untagged)} decision(s) in decisions.json lack the optional `phase` field "
            f"and are excluded from this view. Back-fill via "
            f"`state.py update-record decisions.json --match id=D-N phase={phase_id}` if any belong here. -->"
        )
    return "\n".join(lines)


def render_phase_summary_devlog(ctx: AssemblerContext, phase_id: int) -> str:
    """## Phase Devlog — full history for this phase, bulleted."""
    entries = [e for e in ctx.devlog if e.get("phase") == phase_id]
    lines = ["## Phase Devlog", ""]
    if not entries:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for e in entries:
        lines.append(_fmt_devlog_bullet(e))
    return "\n".join(lines)


def render_phase_summary_open_items(ctx: AssemblerContext, phase_id: int) -> str:
    """## Open Items for Boundary Decision — phase-tagged open decisions.

    Conservative scope: only lists decisions where `phase == N` AND
    `status == open`. Other heuristics (text-matching `Deferred:` in
    devlog summaries, scanning `contracts` fields) are deliberately
    omitted in v1 — too brittle. Operator who needs broader scope can
    grep .state/ directly.
    """
    open_phase = [
        d for d in ctx.decisions
        if d.get("phase") == phase_id and d.get("status") == "open"
    ]
    lines = ["## Open Items for Boundary Decision", ""]
    if not open_phase:
        lines.append(PLACEHOLDER_EMPTY)
        return "\n".join(lines)
    for d in open_phase:
        did = d.get("id", "?")
        priority = d.get("priority", "—")
        title = d.get("title", "")
        decision = d.get("decision", "")
        lines.append(f"- {did} [{priority} · open] {title} {EMDASH} {decision}")
    return "\n".join(lines)


def build_section_phase_summary(ctx: AssemblerContext) -> str:
    """`--section phase-summary --phase N` per ARCH_assembler.md §8b.

    Operator's audit_boundary view: what happened in phase N. Composes
    header, steps, decisions-added-this-phase, phase devlog, and
    open items. Trailing newline per output invariants (§12).
    """
    phase_id = ctx.current_phase_id()
    parts = [
        render_phase_summary_header(ctx, phase_id),
        render_phase_summary_steps(ctx, phase_id),
        render_phase_summary_decisions(ctx, phase_id),
        render_phase_summary_devlog(ctx, phase_id),
        render_phase_summary_open_items(ctx, phase_id),
    ]
    body = "\n\n".join(parts)
    if not body.endswith("\n"):
        body += "\n"
    return body


# ---------------------------------------------------------------------------
# 13. CLI + main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="assemble_context.py",
        description="Assemble worker prompts and section snapshots for i2c.",
    )
    # --action and --section are mutually exclusive (§3.1).
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--action", choices=ACTIONS,
        help="Build a full assembled prompt for the named action.",
    )
    group.add_argument(
        "--section", choices=SECTIONS,
        help="Build a single section snapshot.",
    )
    parser.add_argument(
        "--phase", type=int,
        help="Phase number (positive int). Required with --action and "
             "--section devlog.",
    )
    parser.add_argument(
        "--mode", choices=MODES, default=None,
        help="Mode framing for --action. Default: autonomous. Not valid with "
             "--section.",
    )
    parser.add_argument(
        "--module",
        help="Module name. Required with --section module.",
    )
    parser.add_argument(
        "--backend", choices=("claude", "codex"), default="claude",
        help="Which adapter to read for Tool Rules. Default: claude.",
    )
    parser.add_argument(
        "--step-budget",
        type=int,
        default=1,
        help=(
            "Step budget the runner gave the worker. Default 1 (single-step). "
            "When > 1, multi-step-only WORKER_SPEC subsections are kept; when "
            "== 1, they're stripped. Forward-compat with the multi-iteration "
            "loop landing later."
        ),
    )
    parser.add_argument(
        "--emit",
        choices=("full", "system", "user"),
        default="full",
        help=(
            "Which part of the assembled prompt to emit (--action only). "
            "'full' (default) is the whole prompt; 'system' is the "
            "cache-stable prefix (WORKER CONTRACT + TOOL RULES) for routing "
            "through Claude Code's system prompt; 'user' is the per-iteration "
            "body (PROJECT CONTEXT + ACTION CONTEXT + Output Contract). "
            "full == system.rstrip() + '\\n\\n' + user (FU-35)."
        ),
    )
    return parser


def _validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Cross-flag validation that argparse can't express directly. Exits 2 on failure."""
    if getattr(args, "step_budget", 1) < 1:
        parser.error("--step-budget must be a positive integer")
    if args.action is not None:
        if args.phase is None:
            parser.error("--phase is required with --action")
        if args.phase < 1:
            parser.error("--phase must be a positive integer")
        # --mode is fine here (default autonomous).
    else:  # --section path
        if args.mode is not None:
            parser.error("--mode is only valid with --action")
        if getattr(args, "emit", "full") != "full":
            parser.error("--emit is only valid with --action")
        # --phase is consumed only by devlog and phase-summary. The other
        # sections (status, architecture, module) always report on
        # project.json.phase or ignore phase entirely (ARCH §8); accepting
        # --phase there silently misleads the caller (FU-17).
        if args.section not in ("devlog", "phase-summary") and args.phase is not None:
            parser.error(f"--phase is not valid with --section {args.section}")
        if args.section == "devlog":
            if args.phase is None:
                parser.error("--phase is required with --section devlog")
            if args.phase < 1:
                parser.error("--phase must be a positive integer")
        if args.section == "phase-summary":
            if args.phase is None:
                parser.error("--phase is required with --section phase-summary")
            if args.phase < 1:
                parser.error("--phase must be a positive integer")
        if args.section == "module":
            if not args.module:
                parser.error("--module is required with --section module")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(args, parser)

    # Output invariant (§12): UTF-8 on stdout. The default console encoding
    # on Windows is cp1252, which can't represent box-drawing characters or
    # arrows — reconfigure so the assembled prompt round-trips bytes
    # identically regardless of platform.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (ValueError, AttributeError):  # pragma: no cover
            pass

    ctx = build_context(args)

    if args.section == "status":
        sys.stdout.write(build_section_status(ctx))
        return 0

    if args.section == "architecture":
        sys.stdout.write(build_section_architecture(ctx))
        return 0

    if args.section == "module":
        sys.stdout.write(build_section_module(ctx))
        return 0

    if args.section == "devlog":
        sys.stdout.write(build_section_devlog(ctx))
        return 0

    if args.section == "phase-summary":
        sys.stdout.write(build_section_phase_summary(ctx))
        return 0

    if args.action is not None:
        if args.emit == "system":
            sys.stdout.write(build_stable_prefix(ctx))
        elif args.emit == "user":
            sys.stdout.write(build_volatile_body(ctx))
        else:
            sys.stdout.write(build_full_prompt(ctx))
        return 0

    parser.error("unrecognized invocation")  # pragma: no cover
    return 2  # pragma: no cover


if __name__ == "__main__":
    raise SystemExit(main())

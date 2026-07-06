"""i2c single-iteration runner — drive one cold-start worker invocation.

Wires the state machine → assembler → claude → exit-signal parser →
post-action invariants into a single command. v1 is one action per
invocation (no multi-iteration loop, no Codex backend); both are
deferred to Phase 3.B once the first autonomous run produces real data.

Pipeline (per the plan):

1. Walk up from CWD to find the project root.
2. Run ``tools/state_machine.py``; parse ``ACTION:`` / ``NEXT:``.
3. ``ACTION == EXIT`` → write a summary line and exit 0 (nothing to do).
4. Validate ``--backend`` is one of {claude, codex}.
5. Run ``tools/assemble_context.py --action $ACTION --phase $PHASE
   --mode autonomous --backend $BACKEND``; capture the prompt.
6. Write the assembled prompt(s) to ``logs/loop/``: the stdin body to
   ``iteration_NNN_prompt.md``, and for claude the cache-stable system
   prefix to ``iteration_NNN_system.md`` (FU-35).
7. Run the backend with the body on stdin; capture output to
   ``logs/loop/iteration_NNN.txt``. For claude the cache-stable prefix is
   passed via ``--append-system-prompt-file`` so Claude Code prompt-caches
   it; codex sends one combined prompt (server-side prefix cache).
8. Parse the 2-line exit signal from the captured output; validate
   against ``schemas/exit_signal.schema.json``. Malformed signal →
   treated as ``exit_code: 2`` (halt-and-surface). A backend rate-limit /
   HTTP error (e.g. claude's 429 usage cap) is detected structurally and
   surfaced as ``exit_code: 3`` (retryable backend-unavailable) — distinct
   from a worker/code error so operators/scripts can branch on it.
9. If ``ACTION == CLOSE``, run ``check_post_action(root, "close")``;
   failure → halt-and-surface (exit 2).
10. Write a summary line to ``logs/loop/summary.log`` and exit with the
    worker's exit code.

Iteration number is derived from ``summary.log``: highest existing iter
+ 1, or 1 if absent. This keeps log filenames stable across runs.
"""

from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# Sibling package modules.
from i2c import assemble_context as ac
from i2c import config as cfg
from i2c import invariants
from i2c import state
from i2c import telemetry as tel
from i2c import validate as v


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------


DEFAULT_MODEL = "sonnet"
DEFAULT_MAX_BUDGET_USD = 5.00
LOG_DIR_NAME = "logs/loop"
SUMMARY_LOG_NAME = "summary.log"

# Regexes for the 2-line exit signal. Tolerant to surrounding whitespace
# so the parser succeeds when claude pads with trailing blank lines.
RE_EXIT = re.compile(r"^EXIT:\s*([02])\s*$", re.MULTILINE)
RE_REASON = re.compile(r"^REASON:\s*(.+?)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class RunnerError(Exception):
    """Halt-and-surface error; the runner exits 2 with this message on stderr."""


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------


def run_state_machine(project_root: Path) -> tuple[str, str]:
    """Invoke ``python -m i2c.state_machine`` and return ``(ACTION, NEXT)``.

    Raises ``RunnerError`` if the script exits non-zero or its stdout
    doesn't include both expected lines.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "i2c.state_machine"],
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RunnerError(
            f"state_machine.py failed (rc={proc.returncode}):\n{proc.stderr}"
        )
    action = next_state = None
    for line in proc.stdout.splitlines():
        if line.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
        elif line.startswith("NEXT:"):
            next_state = line.split(":", 1)[1].strip()
    if action is None or next_state is None:
        raise RunnerError(
            f"state_machine.py output missing ACTION/NEXT:\n{proc.stdout}"
        )
    return action, next_state


def current_phase(project_root: Path) -> int:
    """Read project.json and return the current phase integer."""
    project = v.validate_state_file(project_root / ".state" / "project.json")
    return int(project.get("phase", 0))


def assemble_prompt(
    project_root: Path,
    action: str,
    phase: int,
    *,
    backend: str,
    emit: str = "full",
    target: int | None = None,
) -> str:
    """Invoke ``python -m i2c.assemble_context`` and return the prompt text.

    ``backend`` selects which adapter's Tool Rules the assembler embeds
    (``CLAUDE.md`` vs ``CODEX.md``) — it must match the backend the prompt
    is dispatched to, or the worker reads the wrong tool guidance.

    ``emit`` selects which part of the prompt to build (FU-35):
    ``"full"`` (default) is the whole prompt; ``"system"`` is the
    cache-stable prefix (WORKER CONTRACT + TOOL RULES) routed through
    Claude Code's system prompt; ``"user"`` is the per-iteration body.

    ``target`` is forwarded as ``--target`` for recovery actions
    (diagnose/reconcile), selecting which iteration's failure context the
    assembler renders. Ignored by the normal lifecycle actions.
    """
    argv = [
        sys.executable, "-m", "i2c.assemble_context",
        "--action", action.lower(),
        "--phase", str(phase),
        "--mode", "autonomous",
        "--backend", backend,
        "--emit", emit,
    ]
    if target is not None:
        argv += ["--target", str(target)]
    proc = subprocess.run(
        argv,
        cwd=str(project_root),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise RunnerError(
            f"assemble_context.py failed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return proc.stdout


def next_iteration_number(log_dir: Path) -> int:
    """Next iteration number from the highest iter= in summary.log (+1)."""
    summary = log_dir / SUMMARY_LOG_NAME
    if not summary.is_file():
        return 1
    highest = 0
    pattern = re.compile(r"iter=(\d+)")
    for line in summary.read_text(encoding="utf-8").splitlines():
        m = pattern.search(line)
        if m:
            highest = max(highest, int(m.group(1)))
    return highest + 1


def parse_exit_signal(output: str) -> dict[str, Any] | None:
    """Extract the 2-line exit signal from worker output.

    Returns a dict with ``exit_code`` and ``reason``. Returns ``None`` when
    the EXIT line is missing (caller treats as exit_code 2). The structured
    state in ``.state/project.json`` is the canonical source for everything
    else — action_type, action_id, step counts are recoverable from there
    or from what the runner dispatched.
    """
    m_exit = RE_EXIT.search(output)
    if not m_exit:
        return None
    signal: dict[str, Any] = {"exit_code": int(m_exit.group(1))}
    m_reason = RE_REASON.search(output)
    if m_reason:
        signal["reason"] = m_reason.group(1)
    return signal


def validate_exit_signal(signal: dict[str, Any]) -> list[str]:
    """Validate the parsed signal against exit_signal.schema.json.

    Returns a list of error messages (empty = valid).
    """
    schema = v.load_schema(v.EXIT_SIGNAL_SCHEMA)
    try:
        v.validate_json_schema(signal, schema, label="exit signal")
    except ValueError as e:
        return [str(e)]
    return []


# ---------------------------------------------------------------------------
# Claude subprocess invocation (kept seam-able for tests)
# ---------------------------------------------------------------------------


def invoke_claude(
    prompt: str,
    *,
    cwd: Path,
    model: str,
    max_budget_usd: float,
    system_prompt_file: Path | None = None,
) -> tuple[int, str]:
    """Run ``claude -p`` with the prompt on stdin; return (rc, stdout).

    Uses ``--output-format json`` so the runner can extract usage telemetry
    (input/output/cache token counts) alongside the result text. The JSON
    response shape is::

        {"type": "result", "result": "...prose...",
         "usage": {"input_tokens": N, "output_tokens": M,
                   "cache_read_input_tokens": K,
                   "cache_creation_input_tokens": J}, ...}

    ``parse_claude_output`` extracts the ``result`` (the prose that carries
    the 2-line exit signal) and the usage block. If claude ever emits
    something that isn't valid JSON, the parser falls back to treating the
    raw stdout as plain text — the loop keeps working, just without
    per-iter token telemetry.

    Prompt caching (FU-35): when ``system_prompt_file`` is given, the
    cache-stable prefix (WORKER CONTRACT + TOOL RULES) is appended to
    Claude Code's default system prompt via ``--append-system-prompt-file``,
    which Claude Code automatically prompt-caches.
    ``--exclude-dynamic-system-prompt-sections`` strips Claude's own
    dynamic system content (timestamps, etc.) that would otherwise bust
    cache reuse across iterations. The volatile body stays on stdin. The
    cache hit shows up as ``cache_read_input_tokens`` on iter 2+ of a phase
    within the cache TTL.

    stderr is merged into stdout because some shells split surprisingly
    and the only signal the runner needs is the final agent message.
    """
    cmd = [
        "claude",
        "-p",
        "--dangerously-skip-permissions",
        "--output-format", "json",
        "--model", model,
        "--max-budget-usd", f"{max_budget_usd:.2f}",
    ]
    if system_prompt_file is not None:
        cmd += [
            "--append-system-prompt-file", str(system_prompt_file),
            "--exclude-dynamic-system-prompt-sections",
        ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    combined = proc.stdout + (proc.stderr if proc.stderr else "")
    return proc.returncode, combined


def invoke_codex(
    prompt: str,
    *,
    cwd: Path,
) -> tuple[int, str, str]:
    """Run ``codex exec -`` with the prompt on stdin; return
    (rc, jsonl_raw, agent_text).

    The prompt is piped via stdin (codex supports ``-`` as the prompt
    arg). This avoids ARG_MAX risks and shell-quoting hazards with the
    ~40-65 KB assembled prompts the i2c assembler emits.

    Codex emits JSONL events to stdout. The 2-line exit signal lives in
    the LAST event whose shape is::

        {"type": "item.completed", "item": {"type": "agent_message", "text": "..."}}

    The runner saves the full JSONL stream for human / parser inspection
    and routes the extracted agent_message text through the standard
    ``parse_exit_signal`` flow.

    Unlike claude, codex has no ``--model`` or ``--max-budget-usd`` CLI
    flags in this version; model selection and cost caps come from the
    codex CLI's own config.
    """
    cmd = [
        "codex", "exec", "-",
        "--dangerously-bypass-approvals-and-sandbox",
        "--json",
    ]
    proc = subprocess.run(
        cmd,
        input=prompt,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    jsonl_raw = proc.stdout
    last_agent_text = ""
    for line in jsonl_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "item.completed":
            item = obj.get("item", {})
            if item.get("type") == "agent_message":
                # Keep overwriting; the LAST agent_message is the one
                # that carries the 2-line exit signal.
                last_agent_text = item.get("text", "") or last_agent_text
    # Fallback: if no agent_message was emitted (worker died early or
    # auth failed before any output), surface combined stdout+stderr so
    # the exit-signal parser at least sees what was captured.
    if not last_agent_text:
        last_agent_text = jsonl_raw + (proc.stderr or "")
    return proc.returncode, jsonl_raw, last_agent_text


# ---------------------------------------------------------------------------
# Usage / token extraction (FU-33)
#
# Normalized shape across providers: {"input": int, "output": int, "cached": int}
#   input  = gross input tokens (everything the API processed, incl. cache)
#   output = output tokens
#   cached = cache-read subset of input (discounted portion)
#
# Both claude and codex report usage but with different field shapes:
#   claude:  input_tokens is FRESH only; cache_read and cache_creation are
#            separate fields. Gross = input + cache_read + cache_creation.
#   codex:   input_tokens is already GROSS (includes cache); cached_input_tokens
#            is the subset.
# ---------------------------------------------------------------------------


def parse_claude_output(raw: str) -> tuple[str, dict | None]:
    """Extract (result_text, usage_dict) from claude --output-format json output.

    If ``raw`` isn't valid JSON or doesn't have the expected fields, returns
    ``(raw, None)`` — fallback for plain-text mode or malformed output. The
    exit-signal parser still runs on the returned text either way.

    usage_dict shape (when present): {"input": gross, "output": M, "cached": K}.
    """
    try:
        obj = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return raw, None
    if not isinstance(obj, dict):
        return raw, None
    result = obj.get("result")
    if not isinstance(result, str):
        return raw, None
    usage = obj.get("usage")
    if not isinstance(usage, dict):
        return result, None
    fresh_in = int(usage.get("input_tokens", 0) or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    out = int(usage.get("output_tokens", 0) or 0)
    return result, {
        "input": fresh_in + cache_creation + cache_read,
        "output": out,
        "cached": cache_read,
    }


def detect_rate_limit(
    backend: str, captured: str, jsonl_raw: str | None = None
) -> str | None:
    """Return a human reason if the *backend itself* refused (rate-limit / HTTP
    error), else None.

    Distinct from a worker error: nothing the worker did — the provider returned
    an error envelope with no result. Drives the runner's exit-code-3 path so an
    operator can tell a retryable quota hit from a real worker/code failure.
    claude (``--output-format json``) carries this structurally as
    ``is_error: true`` + ``api_error_status`` (429 = usage/rate limit). codex
    detection is a follow-up (no confirmed sample yet).
    """
    if backend == "claude":
        return _claude_backend_error(captured)
    return None  # codex: best-effort TODO — needs a real 429 sample to match on.


def _claude_backend_error(raw: str) -> str | None:
    """Detect a claude backend infra error from the JSON result envelope.

    HTTP 429 = usage/rate limit; any other ``api_error_status`` = backend error.
    Returns None for a normal (worker) result or non-JSON output.
    """
    try:
        obj = json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or not obj.get("is_error"):
        return None
    status = obj.get("api_error_status")
    msg = str(obj.get("result") or "").strip() or "backend error"
    if status == 429:
        return f"backend rate-limited (HTTP 429): {msg}"
    if status is not None:
        return f"backend error (HTTP {status}): {msg}"
    return None


def parse_codex_usage(jsonl_raw: str) -> dict | None:
    """Sum usage across all turn.completed events in a codex JSONL stream.

    Returns ``None`` if no turn.completed events were found. Codex emits one
    turn.completed per turn; in single-step mode that's one per iteration,
    but the helper sums defensively in case the runner ever does multi-turn.

    usage_dict shape: {"input": gross, "output": M, "cached": K}.
    """
    total_in = total_out = total_cached = 0
    found = False
    for line in jsonl_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "turn.completed":
            continue
        usage = obj.get("usage")
        if not isinstance(usage, dict):
            continue
        total_in += int(usage.get("input_tokens", 0) or 0)
        total_out += int(usage.get("output_tokens", 0) or 0)
        total_cached += int(usage.get("cached_input_tokens", 0) or 0)
        found = True
    if not found:
        return None
    return {"input": total_in, "output": total_out, "cached": total_cached}


def format_tokens_segment(usage: dict | None) -> str:
    """Render a usage dict as a summary.log segment.

    Returns the empty string when ``usage`` is None so the line shape
    stays backward-compatible (no token fields appear).
    """
    if not usage:
        return ""
    return (
        f" | tokens_in={int(usage.get('input', 0))} "
        f"tokens_out={int(usage.get('output', 0))} "
        f"tokens_cached={int(usage.get('cached', 0))}"
    )


# ---------------------------------------------------------------------------
# Summary log
# ---------------------------------------------------------------------------


def write_summary_line(
    log_dir: Path,
    *,
    iteration: int,
    backend: str,
    action: str,
    exit_code: int,
    reason: str,
    tokens: dict | None = None,
) -> str:
    """Append (and return) a one-line summary entry.

    When ``tokens`` is provided (FU-33), inserts ``| tokens_in=N tokens_out=M
    tokens_cached=K`` between the exit code and the reason. When ``tokens``
    is None, the line shape is unchanged from pre-FU-33 callers.
    """
    ts = datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec="seconds")
    safe_reason = reason.replace("\n", " ").strip() or "(no reason given)"
    tokens_segment = format_tokens_segment(tokens)
    line = (
        f"{ts} | iter={iteration} | backend={backend} | "
        f"action={action} | exit={exit_code}"
        f"{tokens_segment}"
        f" | reason=\"{safe_reason}\""
    )
    summary = log_dir / SUMMARY_LOG_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(summary, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return line


def _run_project_tests(root: Path, cmd: str) -> bool | None:
    """Run the opt-in tests-oracle command in ``root`` (telemetry only).

    Returns True/False on pass/fail (exit 0 = pass), or None if the command
    couldn't be run at all. Best-effort: never raises, never affects the
    iteration's control flow. Runs *after* the worker's wall-clock is captured
    so the oracle's runtime isn't attributed to the worker.
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            shell=True,
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception:  # noqa: BLE001 - oracle must never break a run
        return None
    return proc.returncode == 0


def commit_state(root: Path, *, phase: int) -> tuple[bool, str]:
    """Deterministically commit .state/ (incl. telemetry.jsonl) after a CLOSE.

    Runner-owned, NOT the agentic worker: only a post-worker committer can
    capture the full close tail — the close devlog entry, the audit_boundary
    write, and the runner-authored telemetry row all land *after* the worker's
    own close commit, so the worker can never commit a complete .state/.
    Scoped to .state/ so worker code/doc commits and operator working-tree
    changes are untouched. Best-effort: returns (committed, note); never raises.
    """
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain", "--", ".state"],
            cwd=str(root), capture_output=True, text=True,
        )
        if st.returncode != 0:
            return False, "not a git repo / git error"
        if not st.stdout.strip():
            return False, "nothing to commit"
        msg = f"{phase}: close - persist .state/ + telemetry"
        cm = subprocess.run(
            ["git", "commit", "-m", msg, "--", ".state"],
            cwd=str(root), capture_output=True, text=True,
        )
        if cm.returncode != 0:
            detail = (cm.stderr or cm.stdout).strip().replace("\n", " ")[:200]
            return False, f"commit failed: {detail}"
        return True, msg
    except Exception as e:  # noqa: BLE001 - state commit must never break a run
        return False, f"error: {e}"


def dirty_tracked_outside_state(root: Path) -> list[str]:
    """Tracked files left uncommitted outside .state/ (best-effort).

    A boundary-cleanliness signal: at close, code/doc changes are the worker's
    to commit; anything tracked still dirty here (e.g. an un-committed
    ARCHITECTURE.md doc-update) would otherwise be silently orphaned. Untracked
    files (operator WIP) are ignored.
    """
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=str(root), capture_output=True, text=True,
        )
        if st.returncode != 0:
            return []
        paths: list[str] = []
        for line in st.stdout.splitlines():
            path = line[3:].strip() if len(line) > 3 else ""
            if " -> " in path:  # rename: keep the destination
                path = path.split(" -> ", 1)[1]
            if path and not path.startswith(".state"):
                paths.append(path)
        return paths
    except Exception:  # noqa: BLE001
        return []


def _worker_dirty_paths(root: Path) -> set[str]:
    """Non-``.state`` paths that are dirty or untracked (best-effort).

    Snapshotted before and after the worker so the runner commits only what the
    worker *newly* changed (after - before), fencing off pre-existing operator
    working-tree changes (e.g. doc WIP). Never raises.
    """
    try:
        st = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(root), capture_output=True, text=True,
        )
        if st.returncode != 0:
            return set()
        out: set[str] = set()
        for line in st.stdout.splitlines():
            path = line[3:].strip() if len(line) > 3 else ""
            if " -> " in path:  # rename: keep the destination
                path = path.split(" -> ", 1)[1]
            if path and not path.startswith(".state"):
                out.add(path)
        return out
    except Exception:  # noqa: BLE001
        return set()


def _last_devlog(root: Path) -> dict[str, Any] | None:
    """The last ``.state/devlog.jsonl`` entry (best-effort), or None."""
    try:
        text = (root / ".state" / "devlog.jsonl").read_text(encoding="utf-8")
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except Exception:  # noqa: BLE001
        return None


def commit_execute(
    root: Path, *, phase: int, step: Any, summary: str, pre_dirty: set[str],
) -> tuple[bool, str | None, str]:
    """Deterministically commit the worker's code after an EXECUTE step (FU-40) --
    the runner-owned counterpart to ``commit_state``.

    Commits only paths the worker newly dirtied (current non-``.state`` dirty set
    minus ``pre_dirty``), so operator WIP is never swept. Message is
    ``<phase>.<step>: <summary>`` (``<phase>: <summary>`` for step-less Refine),
    matching recovery's step/commit convention. Returns ``(committed, short_hash,
    note)``. Best-effort; never raises.
    """
    try:
        changed = sorted(_worker_dirty_paths(root) - pre_dirty)
        if not changed:
            return False, None, "no worker code changes to commit"
        label = f"{phase}.{step}" if step is not None else f"{phase}"
        msg = f"{label}: {summary}".strip()[:200]
        add = subprocess.run(
            ["git", "add", "--", *changed],
            cwd=str(root), capture_output=True, text=True,
        )
        if add.returncode != 0:
            return False, None, "git add failed"
        cm = subprocess.run(
            ["git", "commit", "-m", msg, "--", *changed],
            cwd=str(root), capture_output=True, text=True,
        )
        if cm.returncode != 0:
            detail = (cm.stderr or cm.stdout).strip().replace("\n", " ")[:200]
            return False, None, f"commit failed: {detail}"
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(root), capture_output=True, text=True,
        )
        short = rev.stdout.strip() if rev.returncode == 0 else None
        return True, short, msg
    except Exception as e:  # noqa: BLE001 - a commit failure must never break a run
        return False, None, f"error: {e}"


def _backfill_step_commit(root: Path, phase: int, step: int, commit: str) -> None:
    """Record the runner's commit hash on the (already-complete) step so
    recovery's step/commit check holds (the worker no longer supplies it)."""
    import contextlib
    import io

    ns = argparse.Namespace(
        file=str(root / ".state" / "steps.json"),
        phase=phase, step=step, commit=commit,
    )
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            state.cmd_complete(ns)
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"NOTE: step commit back-fill skipped ({e}).\n")


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


def run_iteration(
    *,
    backend: str | None = None,
    backend_map: dict[str, str] | None = None,
    default_backend: str = "claude",
    model: str,
    max_budget_usd: float,
    action_override: str | None = None,
    target: int | None = None,
    claude_invoker=invoke_claude,
    codex_invoker=invoke_codex,
    state_committer=commit_state,
    execute_committer=commit_execute,
) -> int:
    """Execute one iteration end-to-end and return the runner exit code.

    Backend selection (resolved once the action is known): an explicit
    ``backend`` override (e.g. CLI ``--backend``) wins; otherwise the
    per-action ``backend_map`` (from ``[run.backends]``) is consulted by the
    dispatched action, falling back to ``default_backend`` (``[run].backend``).

    Out-of-band recovery dispatch (archive/DESIGN_recovery_v1.md §D): when
    ``action_override`` is set (``diagnose`` / ``reconcile``), the state machine
    is bypassed entirely — the named action is dispatched directly against
    ``target`` (the iteration whose failure context the assembler renders).
    Normal runs (``action_override is None``) are unchanged: the state machine
    decides the action.

    ``claude_invoker`` and ``codex_invoker`` are seams for tests; defaults
    delegate to the real subprocess wrappers. Each invoker's signature
    must match its real-implementation counterpart.
    """
    root = ac.find_project_root()
    log_dir = root / LOG_DIR_NAME

    # 1. Decide the action: out-of-band override (recovery) or state machine.
    if action_override is not None:
        action = action_override.upper()
        next_state = ""  # recovery actions don't drive linear progression
    else:
        try:
            action, next_state = run_state_machine(root)
        except RunnerError as e:
            sys.stderr.write(f"ERROR: {e}\n")
            return 2

    # 1b. Resolve the backend for this action. Explicit override wins; else the
    #     per-action map keyed by the dispatched action; else the default.
    #     (EXIT is not a real action — it resolves to the default and is only
    #     used as the summary label.)
    backend = backend or (backend_map or {}).get(action.lower(), default_backend)
    if backend not in ("claude", "codex"):
        sys.stderr.write(f"ERROR: unknown backend {backend!r}\n")
        return 2

    # 2. ACTION: EXIT short-circuit (state-machine path only).
    if action == "EXIT":
        iteration = next_iteration_number(log_dir)
        line = write_summary_line(
            log_dir,
            iteration=iteration,
            backend=backend,
            action="EXIT",
            exit_code=0,
            reason=f"state_machine dispatched EXIT (next={next_state})",
        )
        sys.stdout.write(line + "\n")
        return 0

    # 3. Assemble prompt(s). Claude routes the cache-stable prefix
    #    (WORKER CONTRACT + TOOL RULES) through its system prompt for
    #    prompt-cache reuse (FU-35); the volatile body goes on stdin. Codex
    #    has no system-prompt flag, so it sends one combined prompt and
    #    relies on OpenAI's automatic server-side prefix caching.
    phase = current_phase(root)
    iteration = next_iteration_number(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = log_dir / f"iteration_{iteration:03d}_prompt.md"
    system_path = log_dir / f"iteration_{iteration:03d}_system.md"  # claude only
    output_path = log_dir / f"iteration_{iteration:03d}.txt"
    jsonl_path = log_dir / f"iteration_{iteration:03d}.jsonl"  # codex only
    try:
        if backend == "claude":
            system_prompt = assemble_prompt(
                root, action, phase, backend=backend, emit="system", target=target)
            stdin_prompt = assemble_prompt(
                root, action, phase, backend=backend, emit="user", target=target)
        else:  # codex
            stdin_prompt = assemble_prompt(
                root, action, phase, backend=backend, emit="full", target=target)
    except RunnerError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    # 5. Write iteration logs (system file for claude; the stdin prompt for both).
    prompt_path.write_text(stdin_prompt, encoding="utf-8")
    if backend == "claude":
        system_path.write_text(system_prompt, encoding="utf-8")

    # Telemetry pre-invoke snapshot (best-effort; see i2c/telemetry.py). The
    # prompt hash covers the full prompt actually sent to the backend.
    prompt_for_hash = (
        system_prompt + "\n" + stdin_prompt if backend == "claude" else stdin_prompt
    )
    start_commit = tel.head_commit(root)
    prev_devlog = tel.count_devlog_lines(root)
    pre_dirty = _worker_dirty_paths(root)  # FU-40: fence the worker commit off operator WIP
    wall_start = time.monotonic()

    # 6. Invoke the chosen backend.
    try:
        if backend == "claude":
            worker_rc, captured = claude_invoker(
                stdin_prompt,
                cwd=root,
                model=model,
                max_budget_usd=max_budget_usd,
                system_prompt_file=system_path,
            )
        else:  # codex
            worker_rc, jsonl_raw, captured = codex_invoker(
                stdin_prompt,
                cwd=root,
            )
            jsonl_path.write_text(jsonl_raw, encoding="utf-8")
    except FileNotFoundError as e:
        cli_name = e.filename or backend
        sys.stderr.write(
            f"ERROR: {backend} CLI not found on PATH "
            f"(`{cli_name}` could not be invoked).\n"
        )
        return 2
    output_path.write_text(captured, encoding="utf-8")

    # Telemetry post-invoke snapshot (best-effort).
    wall_clock_s = time.monotonic() - wall_start
    end_commit = tel.head_commit(root)
    drift_flag: bool | None = None

    # 6b. Extract per-iter usage telemetry (FU-33). Both backends emit
    # token counts in their JSON output; usage stays None for plain-text
    # claude (fallback) or empty codex streams.
    if backend == "claude":
        signal_text, usage = parse_claude_output(captured)
    else:
        signal_text = captured  # codex agent_message text
        usage = parse_codex_usage(jsonl_raw)

    # 7. Parse + validate the exit signal.
    signal = parse_exit_signal(signal_text)
    if signal is None:
        worker_exit = 2
        reason = (
            "exit signal missing or malformed (2-line block not found in "
            "worker output)"
        )
    else:
        errs = validate_exit_signal(signal)
        if errs:
            worker_exit = 2
            reason = f"exit signal failed schema validation: {'; '.join(errs)}"
        else:
            worker_exit = int(signal["exit_code"])
            reason = signal.get("reason", "(worker emitted no reason)")

    # Telemetry emit (best-effort, never fatal). The runner authors the
    # execution-envelope sidecar; the worker still owns devlog.jsonl. codex's
    # model is config-driven and not known to the runner (left null in v1).
    model_used = model if backend == "claude" else None

    # Telemetry config: pricing table (bundled + [telemetry.pricing] overrides)
    # for cost/tier, and the opt-in tests oracle. All best-effort; a malformed
    # i2c.toml degrades telemetry rather than breaking the run.
    try:
        tele_cfg = cfg.load_telemetry_config(root)
    except cfg.ConfigError:
        tele_cfg = cfg.TelemetryConfig()
    try:
        pricing = tel.load_pricing(overrides=tele_cfg.pricing)
    except Exception:  # noqa: BLE001 - pricing is best-effort
        pricing = None
    tests_cmd = tele_cfg.test_cmd
    tests_pass = _run_project_tests(root, tests_cmd) if tests_cmd else None

    def _emit_telemetry(final_exit: int) -> None:
        try:
            tel.record_iteration(
                root,
                iteration=iteration,
                phase=phase,
                action=action.lower(),
                backend=backend,
                model=model_used,
                usage=usage,
                exit_code=final_exit,
                wall_clock_s=wall_clock_s,
                start_commit=start_commit,
                end_commit=end_commit,
                prompt_text=prompt_for_hash,
                prev_devlog_count=prev_devlog,
                drift_flag=drift_flag,
                pricing=pricing,
                tests_pass=tests_pass,
                tests_cmd=tests_cmd,
            )
        except Exception as e:  # noqa: BLE001 - telemetry must never break a run
            sys.stderr.write(f"NOTE: telemetry skipped ({e}).\n")

    # 7b. Backend rate-limit / infra-error short-circuit (exit 3). When the
    #     backend refused (claude JSON is_error + api_error_status, e.g. the
    #     HTTP 429 usage cap) there is no worker result — surface it distinctly
    #     as exit 3 ("backend unavailable, retry later") instead of the generic
    #     exit=2 malformed-signal path, so an operator/script can tell a quota
    #     hit from a real worker/code error. Nothing landed → skip invariants +
    #     commits.
    rl_reason = detect_rate_limit(
        backend, captured, jsonl_raw if backend == "codex" else None)
    if rl_reason is not None:
        line = write_summary_line(
            log_dir, iteration=iteration, backend=backend, action=action,
            exit_code=3, reason=rl_reason, tokens=usage)
        sys.stdout.write(line + "\n")
        sys.stderr.write(f"ERROR: {rl_reason}\n")
        _emit_telemetry(3)
        return 3

    # 8. CLOSE invariants.
    if action == "CLOSE":
        failures = invariants.check_post_action(root, "close")
        if failures:
            # Halt-and-surface: log a summary line that flags invariant
            # failure, then return 2 regardless of the worker's claimed
            # exit code. This is the FU-22 mitigation in action.
            invariant_reason = (
                "post-CLOSE invariants failed: " + " | ".join(failures)
            )
            line = write_summary_line(
                log_dir,
                iteration=iteration,
                backend=backend,
                action=action,
                exit_code=2,
                reason=invariant_reason,
                tokens=usage,
            )
            sys.stdout.write(line + "\n")
            sys.stderr.write(f"ERROR: {invariant_reason}\n")
            _emit_telemetry(2)
            return 2

    # 8b. Drift advisory (detect-and-surface; archive/DESIGN_recovery_v1.md §C). After a
    #     lifecycle action, run the cheap pure-.state drift audit alongside the
    #     CLOSE invariants and surface any *reconcilable* drift so the operator
    #     can `i2c diagnose` / `i2c reconcile`. Non-fatal: this never changes the
    #     exit code (human-gated reconcile is the remedy, not an auto-halt).
    #     Recovery actions are exempt (their whole job is to inspect/fix drift).
    if action_override is None:
        from i2c import control as _control
        from i2c import recovery as _recovery
        try:
            drift = _recovery.audit_state(_control.load_state(root))
        except _control.ControlError:
            drift = []
        reconcilable = [f for f in drift if f.reconcilable]
        drift_flag = bool(reconcilable)
        if reconcilable:
            sys.stderr.write(
                f"NOTE: workflow drift detected after {action} "
                f"({', '.join(f.signal for f in reconcilable)}); "
                "run `i2c diagnose` then `i2c reconcile`.\n"
            )

    # 8c. Runner-owned EXECUTE code commit (FU-40 Inc 2). The worker edits files
    #     + writes .state via `i2c state`; the deterministic runner commits the
    #     code it changed (current non-.state dirty set minus the pre-invoke
    #     snapshot, so operator WIP is fenced off) as "<phase>.<step>: <summary>",
    #     re-capturing end_commit so telemetry sees it, then back-fills the hash
    #     into steps.json (recovery's step/commit link). Best-effort; never fatal.
    if action == "EXECUTE" and worker_exit == 0:
        entry = _last_devlog(root)
        if entry is not None:
            e_step = entry.get("step")
            e_phase = int(entry.get("phase", phase))
            committed, chash, note = execute_committer(
                root, phase=e_phase, step=e_step,
                summary=str(entry.get("summary", "")), pre_dirty=pre_dirty,
            )
            if committed:
                sys.stdout.write(f"committed EXECUTE code: {note}\n")
                end_commit = tel.head_commit(root)
                if e_step is not None and chash:
                    _backfill_step_commit(root, e_phase, int(e_step), chash)
            else:
                sys.stdout.write(f"NOTE: no EXECUTE code commit ({note}).\n")

    # 8c-tests. Runner-owned TESTS commit (D-tests-4 / FU-40 spec correction).
    #     TESTS authors the phase-level acceptance suite under
    #     tests/acceptance/phase_<N>/ but does not run git; the deterministic
    #     runner commits those files as "<phase>.tests: <summary>" (a distinct
    #     prefix so the benchmark / future integrity check can find the TESTS
    #     commit), fenced off operator WIP like EXECUTE/REVIEW. Without this the
    #     suite would be left uncommitted and then fenced out of EXECUTE's commit
    #     (it's in EXECUTE's pre_dirty snapshot) — the suite would never land in
    #     its own commit, breaking the oracle. No step-hash back-fill (TESTS is
    #     not a steps.json step). Best-effort; never fatal.
    if action == "TESTS" and worker_exit == 0:
        entry = _last_devlog(root)
        if entry is not None:
            t_phase = int(entry.get("phase", phase))
            committed, _chash, note = execute_committer(
                root, phase=t_phase, step="tests",
                summary=str(entry.get("summary", "")), pre_dirty=pre_dirty,
            )
            if committed:
                sys.stdout.write(f"committed TESTS suite: {note}\n")
                end_commit = tel.head_commit(root)
            else:
                sys.stdout.write(f"NOTE: no TESTS commit ({note}).\n")

    # 8d. Runner-owned REVIEW fix-up commit (FU-40 Inc 3). REVIEW applies Must/
    #     Should fixes to code; the deterministic runner commits them phase-level
    #     (step=None) as "<phase>: <review summary>", mirroring the EXECUTE path
    #     with the same operator-WIP fence. Best-effort; never fatal.
    if action == "REVIEW" and worker_exit == 0:
        entry = _last_devlog(root)
        if entry is not None:
            r_phase = int(entry.get("phase", phase))
            committed, _chash, note = execute_committer(
                root, phase=r_phase, step=None,
                summary=str(entry.get("summary", "")), pre_dirty=pre_dirty,
            )
            if committed:
                sys.stdout.write(f"committed REVIEW fix-ups: {note}\n")
                end_commit = tel.head_commit(root)
            else:
                sys.stdout.write(f"NOTE: no REVIEW commit ({note}).\n")

    # 8e. Runner-owned CLOSE docs commit (FU-40 Inc 3). CLOSE edits project docs
    #     (ARCHITECTURE.md / ARCH_*.md / PROJECT.md); the runner commits those
    #     worker-authored edits phase-level as "<phase>: <close summary>" here,
    #     then commits the .state/ + telemetry tail separately after telemetry
    #     (§9b) — keeping worker docs and the runner-authored state tail as two
    #     commits. Best-effort; never fatal.
    if action == "CLOSE" and worker_exit == 0:
        entry = _last_devlog(root)
        if entry is not None:
            c_phase = int(entry.get("phase", phase))
            committed, _chash, note = execute_committer(
                root, phase=c_phase, step=None,
                summary=str(entry.get("summary", "")), pre_dirty=pre_dirty,
            )
            if committed:
                sys.stdout.write(f"committed CLOSE docs: {note}\n")
                end_commit = tel.head_commit(root)
            else:
                sys.stdout.write(f"NOTE: no CLOSE docs commit ({note}).\n")

    # 9. Normal summary + return worker's exit code.
    line = write_summary_line(
        log_dir,
        iteration=iteration,
        backend=backend,
        action=action,
        exit_code=worker_exit,
        reason=reason,
        tokens=usage,
    )
    sys.stdout.write(line + "\n")
    _emit_telemetry(worker_exit)

    # 9b. Runner-owned state commit after a successful CLOSE. The worker cannot
    #     capture the full close tail (close devlog + audit_boundary + the
    #     runner-authored telemetry row all land after its own commit), so the
    #     deterministic runner commits .state/ here — closing the
    #     uncommitted-at-audit_boundary gap. Scoped to .state/; never fatal.
    if action == "CLOSE" and worker_exit == 0:
        committed, note = state_committer(root, phase=phase)
        if committed:
            sys.stdout.write(f"committed .state/ after close: {note}\n")
        else:
            sys.stderr.write(f"NOTE: .state/ not committed after close ({note}).\n")
        dangling = dirty_tracked_outside_state(root)
        if dangling:
            sys.stderr.write(
                "NOTE: tracked changes outside .state/ left uncommitted at close ("
                + ", ".join(dangling[:10])
                + "); code/docs are the worker's to commit.\n"
            )
    return worker_exit


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_iteration.py",
        description="Drive one cold-start worker invocation for i2c.",
    )
    parser.add_argument(
        "--backend",
        choices=("claude", "codex"),
        default="claude",
        help="Which backend to invoke. Supports 'claude' (uses --model / "
             "--max-budget-usd) and 'codex' (config-driven; CLI flags ignored).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model passed to claude -p. Default: {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--max-budget-usd",
        type=float,
        default=DEFAULT_MAX_BUDGET_USD,
        help=f"Cost cap for claude -p. Default: {DEFAULT_MAX_BUDGET_USD:.2f}.",
    )
    parser.add_argument(
        "--action",
        choices=("diagnose", "reconcile"),
        default=None,
        help="Out-of-band recovery action to dispatch against --target, bypassing "
             "the state machine. Omit for a normal state-machine-driven iteration.",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=None,
        help="Target iteration for the recovery --action (default: latest).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", newline="\n")
        except (ValueError, AttributeError):  # pragma: no cover
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_iteration(
        backend=args.backend,
        model=args.model,
        max_budget_usd=args.max_budget_usd,
        action_override=args.action,
        target=args.target,
    )


if __name__ == "__main__":
    raise SystemExit(main())

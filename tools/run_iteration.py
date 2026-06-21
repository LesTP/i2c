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
   treated as ``exit_code: 2`` (halt-and-surface).
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
from pathlib import Path
from typing import Any

# Sibling tools (same dir).
import assemble_context as ac
import invariants
import validate as v


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
    """Invoke ``tools/state_machine.py`` and return ``(ACTION, NEXT)``.

    Raises ``RunnerError`` if the script exits non-zero or its stdout
    doesn't include both expected lines.
    """
    script = Path(__file__).resolve().parent / "state_machine.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
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
    project_root: Path, action: str, phase: int, *, backend: str, emit: str = "full"
) -> str:
    """Invoke ``tools/assemble_context.py`` and return the prompt text.

    ``backend`` selects which adapter's Tool Rules the assembler embeds
    (``CLAUDE.md`` vs ``CODEX.md``) — it must match the backend the prompt
    is dispatched to, or the worker reads the wrong tool guidance.

    ``emit`` selects which part of the prompt to build (FU-35):
    ``"full"`` (default) is the whole prompt; ``"system"`` is the
    cache-stable prefix (WORKER CONTRACT + TOOL RULES) routed through
    Claude Code's system prompt; ``"user"`` is the per-iteration body.
    """
    script = Path(__file__).resolve().parent / "assemble_context.py"
    proc = subprocess.run(
        [
            sys.executable, str(script),
            "--action", action.lower(),
            "--phase", str(phase),
            "--mode", "autonomous",
            "--backend", backend,
            # v1 runner is always single-step. When the multi-iteration loop
            # ships, this becomes a runner parameter; for now it's a constant
            # so the multi_step_only marker mechanism keeps stripping the
            # WORKER_SPEC multi-step subsections.
            "--step-budget", "1",
            "--emit", emit,
        ],
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


# ---------------------------------------------------------------------------
# Top-level runner
# ---------------------------------------------------------------------------


def run_iteration(
    *,
    backend: str,
    model: str,
    max_budget_usd: float,
    claude_invoker=invoke_claude,
    codex_invoker=invoke_codex,
) -> int:
    """Execute one iteration end-to-end and return the runner exit code.

    ``claude_invoker`` and ``codex_invoker`` are seams for tests; defaults
    delegate to the real subprocess wrappers. Each invoker's signature
    must match its real-implementation counterpart.
    """
    root = ac.find_project_root()
    log_dir = root / LOG_DIR_NAME

    # 1. State machine dispatch.
    try:
        action, next_state = run_state_machine(root)
    except RunnerError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    # 2. ACTION: EXIT short-circuit.
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

    # 3. Backend validation.
    if backend not in ("claude", "codex"):
        sys.stderr.write(f"ERROR: unknown backend {backend!r}\n")
        return 2

    # 4. Assemble prompt(s). Claude routes the cache-stable prefix
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
                root, action, phase, backend=backend, emit="system")
            stdin_prompt = assemble_prompt(
                root, action, phase, backend=backend, emit="user")
        else:  # codex
            stdin_prompt = assemble_prompt(
                root, action, phase, backend=backend, emit="full")
    except RunnerError as e:
        sys.stderr.write(f"ERROR: {e}\n")
        return 2

    # 5. Write iteration logs (system file for claude; the stdin prompt for both).
    prompt_path.write_text(stdin_prompt, encoding="utf-8")
    if backend == "claude":
        system_path.write_text(system_prompt, encoding="utf-8")

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
            return 2

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
    )


if __name__ == "__main__":
    raise SystemExit(main())

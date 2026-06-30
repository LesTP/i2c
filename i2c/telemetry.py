"""i2c telemetry sidecar — ``.state/telemetry.jsonl``.

Runner-authored *execution envelope* for each autonomous worker invocation:
model, token usage, timing, git deltas, prompt hash, phase metadata, and the
denormalized worker outcome. One row per invocation.

Two hard rules (see DESIGN_telemetry_v1.md):

* **Observational, never control state.** The state machine, invariants,
  recovery/drift audit, and migration must never read this file. Nothing here
  feeds a dispatch decision.
* **Best-effort, never fatal.** Every derived field degrades to ``None`` when it
  can't be computed (no git repo, no usage block, unparseable phase). The
  collectors below swallow their own IO errors; the caller additionally wraps
  :func:`record_iteration` so a telemetry failure can never change an
  iteration's control flow or exit code.

Cost/tier, the tests oracle, ``tool_calls`` and ``review_findings`` are left
``None`` in v1 (the schema marks them nullable); they're populated by later
increments without a schema change.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from i2c import state
from i2c import validate as v

TELEMETRY_FILENAME = "telemetry.jsonl"
SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Small derivation helpers (each best-effort; returns None on any failure)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.datetime.now(tz=datetime.timezone.utc).isoformat(timespec="seconds")


def _git(args: list[str], cwd: Path) -> str | None:
    """Run ``git <args>`` in ``cwd``; return stripped stdout or None on failure.

    Returns None when git isn't installed, ``cwd`` isn't a repo, or the command
    exits non-zero — so callers can treat "no git" the same as "unknown".
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def head_commit(root: Path) -> str | None:
    """Full 40-char HEAD sha, or None outside a git repo."""
    out = _git(["rev-parse", "HEAD"], root)
    if out and all(c in "0123456789abcdefABCDEF" for c in out):
        return out
    return None


def diff_numstat(
    root: Path, start: str, end: str
) -> tuple[int | None, int | None, int | None]:
    """``git diff --numstat start..end`` → (files_touched, loc_added, loc_removed).

    Returns ``(None, None, None)`` if git can't produce the diff. Binary files
    (``-`` in the added/removed columns) count toward ``files_touched`` but
    contribute 0 to the line totals.
    """
    out = _git(["diff", "--numstat", f"{start}..{end}"], root)
    if out is None:
        return (None, None, None)
    files = added = removed = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        files += 1
        a, r = parts[0], parts[1]
        if a.isdigit():
            added += int(a)
        if r.isdigit():
            removed += int(r)
    return (files, added, removed)


def prompt_hash(text: str) -> str:
    """``sha256:<hex>`` fingerprint of the assembled prompt (for replay keys)."""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def phase_meta(root: Path, phase: int) -> tuple[str | None, bool | None]:
    """(regime, leaf) for ``phase`` from phases.json; (None, None) if unknown.

    ``leaf`` is True when the phase record's ``dependencies`` is empty/absent.
    """
    try:
        data = json.loads((root / ".state" / "phases.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (None, None)
    if not isinstance(data, list):
        return (None, None)
    for rec in data:
        if isinstance(rec, dict) and rec.get("id") == phase:
            regime = rec.get("regime")
            leaf = not rec.get("dependencies")
            return (regime if isinstance(regime, str) else None, leaf)
    return (None, None)


def _devlog_lines(root: Path) -> list[str]:
    try:
        text = (root / ".state" / "devlog.jsonl").read_text(encoding="utf-8")
    except OSError:
        return []
    return [ln for ln in text.splitlines() if ln.strip()]


def count_devlog_lines(root: Path) -> int:
    """Non-empty devlog line count — snapshot before invoking the worker."""
    return len(_devlog_lines(root))


def devlog_tail_since(
    root: Path, prev_count: int, action: str
) -> tuple[int | None, str | None]:
    """Denormalize (step, outcome) from the devlog line(s) the worker just wrote.

    Only lines appended after ``prev_count`` are considered (so pre-existing
    history is never misattributed). Returns the last new line whose ``action``
    matches; ``(None, None)`` when the worker logged nothing (e.g. a fake worker
    in tests, or an early escalation).
    """
    new = _devlog_lines(root)[prev_count:]
    for line in reversed(new):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("action") == action:
            step = rec.get("step")
            outcome = rec.get("outcome")
            return (
                step if isinstance(step, int) else None,
                outcome if isinstance(outcome, str) else None,
            )
    return (None, None)


# ---------------------------------------------------------------------------
# Row construction (pure) + write
# ---------------------------------------------------------------------------


def build_row(
    *,
    iteration: int,
    phase: int,
    action: str,
    backend: str,
    timestamp: str,
    mode: str = "autonomous",
    model: str | None = None,
    tier: str | None = None,
    step: int | None = None,
    outcome: str | None = None,
    exit_code: int | None = None,
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    tokens_cached: int | None = None,
    cost_usd: float | None = None,
    cost_source: str | None = None,
    wall_clock_s: float | None = None,
    tool_calls: int | None = None,
    start_commit: str | None = None,
    end_commit: str | None = None,
    prompt_hash: str | None = None,
    files_touched: int | None = None,
    loc_added: int | None = None,
    loc_removed: int | None = None,
    regime: str | None = None,
    leaf: bool | None = None,
    tests_pass: bool | None = None,
    tests_cmd: str | None = None,
    drift_flag: bool | None = None,
    review_findings: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Assemble a telemetry row. All keys are present (explicit nulls) so the
    JSONL stays columnar-friendly for analysis. Caller validates on write."""
    return {
        "schema_version": SCHEMA_VERSION,
        "iteration": iteration,
        "phase": phase,
        "step": step,
        "action": action,
        "outcome": outcome,
        "exit_code": exit_code,
        "mode": mode,
        "backend": backend,
        "model": model,
        "tier": tier,
        "timestamp": timestamp,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_cached": tokens_cached,
        "cost_usd": cost_usd,
        "cost_source": cost_source,
        "wall_clock_s": wall_clock_s,
        "tool_calls": tool_calls,
        "start_commit": start_commit,
        "end_commit": end_commit,
        "prompt_hash": prompt_hash,
        "files_touched": files_touched,
        "loc_added": loc_added,
        "loc_removed": loc_removed,
        "regime": regime,
        "leaf": leaf,
        "tests_pass": tests_pass,
        "tests_cmd": tests_cmd,
        "drift_flag": drift_flag,
        "review_findings": review_findings,
    }


def record_iteration(
    root: Path,
    *,
    iteration: int,
    phase: int,
    action: str,
    backend: str,
    model: str | None,
    usage: dict[str, Any] | None,
    exit_code: int | None,
    wall_clock_s: float | None,
    start_commit: str | None,
    end_commit: str | None,
    prompt_text: str | None,
    prev_devlog_count: int,
    drift_flag: bool | None,
    mode: str = "autonomous",
) -> dict[str, Any]:
    """Derive the remaining fields, build the row, validate, and append it.

    Returns the written row. Raises ValueError only if the assembled row fails
    schema validation (nothing is written in that case) — the runner wraps this
    call so such a failure degrades to a skipped row, never a failed iteration.
    """
    regime, leaf = phase_meta(root, phase)

    files_touched = loc_added = loc_removed = None
    if start_commit and end_commit and start_commit != end_commit:
        files_touched, loc_added, loc_removed = diff_numstat(root, start_commit, end_commit)

    step, outcome = devlog_tail_since(root, prev_devlog_count, action)

    tokens_in = tokens_out = tokens_cached = None
    if usage:
        tokens_in = int(usage.get("input", 0) or 0)
        tokens_out = int(usage.get("output", 0) or 0)
        tokens_cached = int(usage.get("cached", 0) or 0)

    row = build_row(
        iteration=iteration,
        phase=phase,
        action=action,
        backend=backend,
        timestamp=_utcnow(),
        mode=mode,
        model=model,
        step=step,
        outcome=outcome,
        exit_code=exit_code,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=tokens_cached,
        wall_clock_s=round(wall_clock_s, 3) if wall_clock_s is not None else None,
        start_commit=start_commit,
        end_commit=end_commit,
        prompt_hash=prompt_hash(prompt_text) if prompt_text else None,
        files_touched=files_touched,
        loc_added=loc_added,
        loc_removed=loc_removed,
        regime=regime,
        leaf=leaf,
        drift_flag=drift_flag,
    )

    path = Path(root) / ".state" / TELEMETRY_FILENAME
    state.append_validated_jsonl(path, row, schema_name=v.TELEMETRY_ENTRY_SCHEMA)
    return row

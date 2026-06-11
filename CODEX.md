# Codex Worker Adapter — [Project Name]

> This file is a **template**. Copy it into a new i2c project and fill in the
> placeholders. The framework keeps a canonical copy at `p:\shared\i2c\CODEX.md`
> so future updates can be diffed in.
>
> **Contract:** Backend-specific mechanics for Codex workers. The universal
> loop contract (identity, main loop, escalation, output contract,
> prohibitions) lives in `WORKER_SPEC.md` and arrives in your prompt
> pre-assembled — you do not read it. Action procedures live in
> `instructions/$ACTION.md` and also arrive pre-assembled. This adapter
> covers what is **Codex-specific** plus what is **project-specific**.

## Framework
<!-- Name the governance framework. For new i2c projects this is just "i2c"; for
projects that wrap i2c with additional rules, name the wrapper. -->

## Available Modules
<!-- List tracks and modules. This gives the worker high-level orientation
without loading PROJECT.md or ARCHITECTURE.md (both of which the assembler
includes when an action needs them — PLAN, REVIEW). Example:

**Track A — Core Logic:**
- `event_store`: append-only durable storage with cursor reader
- `orchestrator`: pipeline + event loop + slash command routing

**Track B — Platform Integration (after Track A):**
- `formatting`: Telegram MarkdownV2 escaping and message splitting
- `transport`: pluggable platform I/O (Telegram, CLI, future Discord)
-->

## Project-Specific Notes
<!-- Anything a cold-start worker needs to know about this project that isn't
captured by PROJECT.md, ARCHITECTURE.md, or the per-module ARCH files. Keep
short and prescriptive. Examples:

- **Language:** Python 3.12+
- **Test framework:** stdlib `unittest`, discoverable from `tests/`
- **State writes:** all `.state/` mutations go through `python3 tools/state.py`
  (see WORKER_SPEC §6 Prohibitions). Schemas live in `schemas/`.
- **External dependencies:** `toolkit/` (sibling project) provides
  `llm_client`, `telegram_client`, `cost_accountant`. No direct provider SDK
  imports.
- **Deployment target:** Raspberry Pi inside an Incus container.
- **Storage:** SQLite for runtime persistence; JSON / JSONL for `.state/`.
-->

## Codex-Specific Tool Rules

- **No `@`-reference loading.** Read files explicitly using shell commands.
  When prose contains `@FILENAME` markers, treat them as file paths to read
  with `cat` or `sed -n`.
- **Minimize tool calls.** Every tool call re-processes the full context.
  Combine multiple file reads, greps, and short commands into single shell
  invocations.
  - Bad: `cat A.py` then `cat B.py` (two tool calls).
  - Good: `cat A.py && echo '---' && cat B.py` (one tool call).
  - Bad: `grep foo A` then `grep foo B`.
  - Good: `grep -n foo A B`.
- **Search-tool fallback.** This loop environment may not have `rg`
  installed. Before using `rg`, check availability with `command -v rg`. If
  it is absent, fall back to portable equivalents: `find` for file
  discovery, `grep -RIn` for text search, `sed -n` for bounded file reads.
  Do not repeatedly retry `rg` after it has failed in the same iteration.
- **Fresh reads before edits.** Before editing any source or test file,
  re-read it immediately — not at the start of the iteration. Governance
  arrived fresh in your prompt; this rule applies to source files only.
- **Non-interactive shell only.** The loop has no stdin. Commands that
  open editors (`vim`, `nano`, `git commit` without `-m`,
  `git rebase -i`), prompt for input (`read`, `sudo` without `-n`,
  `ssh` without `-o BatchMode=yes`), or pipe through pagers (`less`,
  `more`, `git log` without `--no-pager`) will hang. To stage part of
  a file, split into discrete edits or use `git restore` to revert
  unwanted parts before `git add`. `git add -p` is interactive-only.
- **State writes go through `state.py`.** Never use `sed`, `echo >`, or
  direct file edits on `.state/` files. The CLI guarantees atomic,
  schema-validated writes.
- **Use `state.py --from-file` for multi-line or `$`-laden payloads.**
  Write the JSON to a temp file and pass `--from-file <path>`; bypasses
  shell quoting entirely. Inline-quoting works for short one-line JSON
  without `$` or newlines.

<!-- Add project-specific tool rules below. -->

## Turn Health Check (Codex-specific safety)

This is a **safety circuit breaker**, separate from the step budget. The
runner provides `ITERATION_JSONL` in the prompt's environment when
applicable. After each completed action, check the turn count:

```bash
grep -c '"item.completed"' "$ITERATION_JSONL"
```

If `total_turns > steps_completed * 50`, emit the exit signal with `EXIT 2`
and reason `"turn health check exceeded"`. Do **not** continue.

Calibration notes (apply judgment, not just the formula):

- The 50-turns-per-step ceiling is calibrated for single-repo work where
  the worker mostly reads, edits, and tests within one project directory.
- **Cross-repo work** (e.g., a step that edits both this project and
  `toolkit/`) legitimately needs more tool calls — discovering the
  editable install path, reading files in two repos, committing to two
  repos. If you trip the ceiling during a clearly-cross-repo step, log
  the exit and note the cause in the devlog `summary` so the orchestrator
  can decide whether to relax the threshold for future cross-repo phases.
- The check is a circuit breaker, not the budgeting mechanism. The step
  budget (`steps_remaining` in `project.json`) is what counts work; this
  is just the safety net against runaway tool churn.

## Runner Info

**Runner:** `run-iteration.sh` — invokes `codex exec` per iteration with the
assembled prompt on stdin, logs to `logs/loop/`.

The runner ships an iteration-specific JSONL log path in the prompt when
relevant; that path is the input to the turn-health check above.

## Output Contract

End every invocation with exactly these five lines — no additional text after:

```
EXIT: 0 | 1 | 2
REASON: <one-line summary>
ACTION_TYPE: PLAN | EXECUTE | REVIEW | CLOSE
ACTION_ID: <phase.step>
STEPS_COMPLETED: <number of actions performed in this invocation>
```

| Code | Meaning |
|------|---------|
| 0 | Normal completion — runner reads `.state/project.json` to decide next dispatch |
| 1 | Halt state on entry (`audit_boundary`/`audit_escalation`/`done`) — nothing to do |
| 2 | Error — judgment-based escalation or health check tripped |

The runner's parser uses line-anchored regexes on each field. The block can be plain or inside a fenced code block; both work. **Do not omit it** — prose-only output causes the runner to report `exit=2 "signal missing or malformed"` even when the work landed correctly in `.state/` and the commit.

## Mode

Mode (autonomous vs. supervised) is set by the runner via the assembler's
`--mode` flag. The assembled prompt's framing reflects the active mode:

- **Autonomous** (default): apply fixes, commit, transition state, emit the
  exit signal without waiting for input.
- **Supervised** (`--mode supervised`): the assembled instructions include
  pause-for-approval framing; surface proposed changes before committing.

You do not choose the mode. If the framing in your prompt is ambiguous,
default to autonomous behavior and note the ambiguity in the devlog
`summary`.

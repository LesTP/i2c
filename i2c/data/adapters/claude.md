# Claude Worker Adapter — [Project Name]

> This file is a **template**. Copy it into a new i2c project and fill in the
> placeholders. The framework keeps a canonical copy at `p:\shared\i2c\CLAUDE.md`
> so future updates can be diffed in.
>
> **Contract:** Backend-specific mechanics for Claude workers. The universal
> loop contract (identity, main loop, escalation, output contract,
> prohibitions) lives in `WORKER_SPEC.md` and arrives in your prompt
> pre-assembled — you do not read it. Action procedures live in
> `instructions/$ACTION.md` and also arrive pre-assembled. This adapter
> covers what is **Claude-specific** plus what is **project-specific**.

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
- **State writes:** all `.state/` mutations go through `i2c state`
  (see WORKER_SPEC §6 Prohibitions). Schemas live in `schemas/`.
- **External dependencies:** `toolkit/` (sibling project) provides
  `llm_client`, `telegram_client`, `cost_accountant`. No direct provider SDK
  imports.
- **Deployment target:** Raspberry Pi inside an Incus container.
- **Storage:** SQLite for runtime persistence; JSON / JSONL for `.state/`.
-->

## Claude-Specific Tool Rules

- **Edit tool requires fresh reads.** Before editing any source or test file,
  read it immediately before the edit — not at the start of the iteration.
  Governance state arrived fresh in your prompt; this rule applies to source
  files only.
- **No subagent spawning for routine work.** Do NOT spawn `Agent(Explore)`
  subagents for simple file discovery — use `bash find` or `bash ls`
  instead. Subagents are appropriate for genuinely open-ended research.
- **Non-interactive shell only.** The loop has no stdin. Commands that
  open editors (`vim`, `nano`, `git commit` without `-m`,
  `git rebase -i`), prompt for input (`read`, `sudo` without `-n`,
  `ssh` without `-o BatchMode=yes`), or pipe through pagers (`less`,
  `more`, `git log` without `--no-pager`) will hang. To stage part of
  a file, split into discrete edits or use `git restore` to revert
  unwanted parts before `git add`. `git add -p` is interactive-only.
- **State writes go through `i2c state`.** Never use `sed`, `echo >`, or
  direct file edits on `.state/` files. The CLI guarantees atomic,
  schema-validated writes.
- **Use `i2c state --from-file` for multi-line or `$`-laden payloads.**
  Write the JSON to a temp file and pass `--from-file <path>`; bypasses
  shell quoting entirely. Inline-quoting works for short one-line JSON
  without `$` or newlines.

<!-- Add project-specific tool rules below. Examples:
- Use `bash grep` instead of the Grep tool if built-in tools have path issues.
- Use `bash find` instead of the Glob tool if paths contain special characters.
-->

## Runner Info

**Runner:** `run-iteration.sh` — invokes `claude -p` per iteration with the
assembled prompt on stdin, logs to `logs/loop/`.

**Slash commands** (supervised mode, interactive use): live in
`.claude/commands/` per i2c convention. Each is a thin wrapper that shells
out to `i2c assemble …` or `i2c state …`.
You do not need to read these in autonomous mode — the same procedures are
already in your assembled prompt via `instructions/*.md`.

## Output Contract

End every invocation with exactly these two lines — no additional text after:

```
EXIT: 0 | 2
REASON: <one-line summary>
```

| Code | Meaning |
|------|---------|
| 0 | Normal completion — runner reads `.state/project.json` to decide next dispatch |
| 2 | Error — judgment-based escalation or health check tripped |

The runner's parser uses line-anchored regexes. The block can be plain or inside a fenced code block; both work. **Do not omit it** — prose-only output causes the runner to report `exit=2 "signal missing or malformed"` even when the work landed correctly in `.state/` and the commit. Everything else the runner needs (action, phase, step, status) it reads from `.state/project.json` and from what it dispatched.

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

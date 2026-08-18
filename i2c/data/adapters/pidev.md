# pi.dev Worker Adapter — [Project Name]

> This file is a **template**. Copy it into a new i2c project and fill in the
> placeholders. The framework keeps a canonical copy at `p:\shared\i2c\PIDEV.md`
> so future updates can be diffed in.
>
> **Contract:** Backend-specific mechanics for pi.dev workers. The universal
> loop contract (identity, main loop, escalation, output contract,
> prohibitions) lives in `WORKER_SPEC.md` and arrives in your prompt
> pre-assembled — you do not read it. Action procedures live in
> `instructions/$ACTION.md` and also arrive pre-assembled. This adapter
> covers what is **pi.dev-specific** plus what is **project-specific**.
>
> pi.dev (the `pi` CLI) is a multi-provider agentic coding CLI. i2c invokes it
> as `pi -p … --provider openrouter --model <id>` (DESIGN_backend_v1 §3.8/§3.9),
> so the worker model is whatever OpenRouter id the runner selected.

## Framework
<!-- Name the governance framework. For new i2c projects this is just "i2c"; for
projects that wrap i2c with additional rules, name the wrapper. -->

## Available Modules
<!-- List tracks and modules. This gives the worker high-level orientation
without loading PROJECT.md or ARCHITECTURE.md (both of which the assembler
includes when an action needs them — PLAN, REVIEW).

Pattern A (per-module ARCH files): list the modules, one per ARCH_<module>.md.
Pattern B (single-document, project.json.pattern="B"): there are no per-module
files — list the ARCHITECTURE.md layers/components, or leave this minimal since
scope comes from ARCHITECTURE.md's Implementation Sequence. Example:

**Track A — Core Logic:**
- `event_store`: append-only durable storage with cursor reader
- `orchestrator`: pipeline + event loop + slash command routing
-->

## Project-Specific Notes
<!-- Anything a cold-start worker needs to know about this project that isn't
captured by PROJECT.md, ARCHITECTURE.md, or the per-module ARCH files. Keep
short and prescriptive. Examples:

- **Language:** Python 3.12+
- **Test framework:** stdlib `unittest`, discoverable from `tests/`
- **State writes:** all `.state/` mutations go through `i2c state`
  (see WORKER_SPEC §6 Prohibitions).
- **Deployment target:** Raspberry Pi inside an Incus container.
-->

## Pidev-Specific Tool Rules

- **You have `read`, `bash`, `edit`, `write` tools.** Use `edit`/`write` to
  change source and test files; use `bash` to run tests and to write `.state/`
  via `i2c state`. `grep`/`find`/`ls` are available for search.
- **No `@`-reference loading of governance.** All governance already arrived in
  your prompt. When prose contains `@FILENAME` markers, treat them as file paths
  to read with the `read` tool (or `cat`), not as auto-includes.
- **State writes go through `i2c state`.** Never use `sed`, `echo >`, `edit`, or
  `write` on `.state/` files directly. The CLI guarantees atomic,
  schema-validated writes. Use `i2c state --from-file <path>` for multi-line or
  `$`-laden payloads to bypass shell quoting.
- **Fresh reads before edits.** Before editing any source or test file, re-read
  it immediately with the `read` tool. Governance arrived fresh in your prompt;
  this rule applies to source files only.
- **Non-interactive shell only.** The loop has no stdin. Commands that open
  editors, prompt for input (`read`, `sudo` without `-n`), or page (`less`,
  `git log` without `--no-pager`) will hang. You never commit — the runner does
  — so any git you run is read-only; always pass `--no-pager`.
- **Minimize tool calls.** Combine related reads/greps into single `bash`
  invocations where practical (each call re-processes context).
- **`i2c` is on your PATH.** Call it as a bare command (`i2c state …`).

<!-- Add project-specific tool rules below. -->

## Output Contract

End every invocation with exactly these two lines — no additional text after:

```
EXIT: <0 or 2>
REASON: <one-line summary>
```

**Pick exactly ONE exit code — write `EXIT: 0` or `EXIT: 2`, never the literal
`0 | 2`.** Emit `0` for normal completion (the runner then reads
`.state/project.json` to decide the next dispatch) and `2` for an
error / judgment-based escalation.

| Code | Meaning |
|------|---------|
| 0 | Normal completion |
| 2 | Error — judgment-based escalation |

The runner's parser uses line-anchored regexes; the block can be plain or inside
a fenced code block. **Do not omit it** — prose-only output causes the runner to
report `exit=2 "signal missing or malformed"` even when the work landed correctly
in `.state/`. Everything else the runner needs (action, phase, step, status) it
reads from `.state/project.json` and from what it dispatched.

## Mode

Mode (autonomous vs. supervised) is set by the runner via the assembler's
`--mode` flag; the assembled prompt's framing reflects it. You do not choose the
mode. If the framing is ambiguous, default to autonomous behavior and note the
ambiguity in the devlog `summary`.

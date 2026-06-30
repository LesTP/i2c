# i2c — Idea to Code

A governance framework for AI-driven software development. It keeps project
state in structured, schema-validated files on disk and feeds AI worker
agents fully pre-assembled prompts — so humans and agents share one source of
truth, and the loop that drives them is deterministic and auditable.

The core idea: **don't ask the model to parse and maintain prose state.**
Project state lives in `.state/*.json`, validated against JSON Schema and
written only through a small CLI. Worker agents (Claude, Codex, …) receive all
the governance content they need pre-assembled in their prompt — they never
read or write governance files directly. This removes the impedance mismatch
between deterministic control flow and prose-formatted state that tends to
cause subtle, recurring bugs in agent frameworks.

> Licensed under the [MIT License](LICENSE).

---

## At a glance

```
Phase lifecycle:   plan → execute (×N) → review → close → [human gate]
State of truth:    .state/project.json + phases.json + steps.json + devlog.jsonl + decisions.json
Worker prompts:    pre-assembled (the worker reads no governance files)
Backends:          Claude or Codex (per-backend adapter; the loop contract is universal)
Modes:             autonomous (loop runner) or supervised (human + assistant)
```

---

## Status

i2c is functionally complete for **supervised use** and **single-iteration
autonomous runs**. It has driven a real multi-phase software project
end-to-end — fourteen phases, autonomously, across both the Claude and Codex
backends.

It ships as an **installable package**: `pip install` puts the `i2c` console
command on your `PATH` and bundles the framework assets (JSON Schemas,
`WORKER_SPEC.md`, `instructions/`, adapters, templates) as package data, so a
consuming project carries only its own `.state/` and docs — no copied framework
code. `i2c init` scaffolds a new project, and `i2c migrate` performs versioned
in-place `.state/` upgrades (see
[Versioning & migration](#versioning--migration)).

On top of the state model sits a **single structured command layer**
(`i2c.control`) with an operator CLI — `i2c status` / `portfolio` /
`phase-summary` / `decisions` / `devlog` / `escalation` / `logs` / `diagnose`
(all with a `--json` mode) plus the `clear-boundary` and `reconcile` actions —
and an optional **Telegram surface** (see [Chat surface](#chat-surface-telegram)).
Both are thin, deterministic adapters over the same command API.

Active development: pluggable backends beyond Claude and Codex (e.g. Gemini /
OpenRouter), a Discord surface and an optional conversational agent layer, and a
multi-iteration loop.

---

## Requirements

- **Python 3.10+** with [`jsonschema`](https://pypi.org/project/jsonschema/)
  (the only runtime dependency). Tests use the standard-library `unittest` —
  no extra test dependencies.
- **For autonomous runs:** a backend CLI on your `PATH` — the `claude` CLI
  (Claude Code) and/or the `codex` CLI — with the relevant provider API
  credentials configured in your environment.
- **For supervised runs:** any capable coding assistant that can run the
  `i2c` commands; no backend CLI required.
- **The `i2c` command:** the worker/operator surface is the `i2c` console
  command, installed from this package. **Recommended: `pipx install i2c`** —
  pipx puts the `i2c` entry point on your `PATH` (run `pipx ensurepath` once)
  and installs into an isolated environment, which also sidesteps the
  externally-managed-environment restriction below. Alternatives: a virtualenv
  (`python -m venv .venv && .venv/bin/pip install i2c`) or, for framework
  development, an editable install (`pip install -e .`).
  - **`i2c` must be on `PATH`.** Worker procedures and adapters call the
    `i2c` console command directly (e.g. `i2c state …`), so the entry point
    has to be resolvable as a command — not only importable. Verify your
    install with `i2c doctor` (it checks PATH and login-shell PATH, the
    `jsonschema` dependency, packaged schemas, an `i2c.toml` parser, and a
    backend CLI), or minimally `i2c --version` (the command, not
    `python -m i2c.cli`). The `python -m i2c.cli …` form always works as an
    operator fallback, but it does **not** satisfy the worker, which invokes
    bare `i2c`.
  - **Externally-managed environments (PEP 668).** On Debian/Ubuntu,
    Homebrew Python, and most containers, a plain `pip install` fails with
    `externally-managed-environment`. Use **pipx** or a **venv** (above). Only
    on a disposable/container environment is `pip install --break-system-packages`
    an acceptable shortcut.
  - **Autonomous / containerized runs need `i2c` on the *system* PATH.**
    Install i2c in the environment where the worker executes (e.g. the
    container), which may differ from your laptop — and put it on the
    **login-shell** PATH, not just your interactive one. This matters because
    backends differ in how they run the worker's shell commands: the **codex**
    backend runs every command via `bash -lc` (a login shell), which
    **rebuilds PATH from `/etc/profile` + `~/.profile` and discards the
    launching process's PATH** — so exporting `PATH` before `i2c run`, or a
    `--user` install under `~/.local/bin`, is **not** enough: codex's
    `i2c state` calls fail with `i2c: command not found` partway through a
    phase. (The **claude** backend preserved the inherited PATH in our testing
    and tolerated a `~/.local/bin` install, but a system PATH is the robust
    setup for *either* backend.) Put `i2c` on a system/login-shell PATH:
    `pipx` (`pipx ensurepath`), a system install, or symlink the launcher into
    `/usr/local/bin` (`sudo ln -s "$(command -v i2c)" /usr/local/bin/i2c`).
    Confirm on that host with `i2c doctor` — its **i2c on login-shell PATH**
    check probes a real `bash -lc` shell and fails when only `~/.local/bin`
    has it.

---

## How it works

### Architecture: state vs content

i2c splits the governance system into two layers, each with one owner:

- **Structured state** (`.state/*.json`, `.jsonl`) — phases, steps, status,
  decisions, log. Machine-readable JSON, written only through `i2c state`
  (atomic, schema-validated), read by the state machine, the assembler, and
  surfaces. This is the single source of truth.
- **Narrative content** (markdown) — `PROJECT.md`, `ARCHITECTURE.md`,
  `ARCH_*.md`, `WORKER_SPEC.md`, `instructions/*.md`, the adapter. Human-authored
  context the worker reads to perform an action.

The pipeline keeps the two cleanly separated: the **state machine** reads
structured state to pick the next ACTION; the **assembler** combines both layers
into one prompt; the **worker** receives that prompt and reads *zero* governance
files — it only touches source/test code, and writes outcomes back through
`i2c state`. JSON (not YAML or prose) is the state format so reads are
unambiguous and every write is atomic and schema-checked.

Design rationale and the full decision log: [`DECISIONS.md`](DECISIONS.md);
historical design memos in [`archive/`](archive/).

### State model

Every i2c project has a `.state/` directory holding five git-tracked files.
Diffs of these files cleanly show every state transition.

| File | Shape | What it holds |
|------|-------|---------------|
| `project.json` | JSON object | Current phase number, lifecycle state, gotchas, step or time budget |
| `phases.json` | Array of objects | One record per phase — id, module, title, regime (build/refine/explore), dependencies, status |
| `steps.json` | Array of objects | One record per step across all phases — (phase, step), title, status, commit hash |
| `devlog.jsonl` | One JSON object per line | Append-only history of every action's outcome |
| `decisions.json` | Array of objects | Decision records — id, title, status (open/closed/superseded), decision text, rationale |

Schemas ship inside the package at [`i2c/data/schemas/`](i2c/data/schemas/). All
writes go through the `i2c state` command (implemented by
[`i2c/state.py`](i2c/state.py)) for atomicity and schema validation — never
`sed`, `echo >`, or a text editor. Every write validates the resulting file
against its schema first; a validation failure leaves the file untouched.

### Lifecycle states

`project.json.state` is the single variable that drives the state machine.
There are seven values:

| State | Meaning | Next dispatch | Recovery write (when halted) |
|-------|---------|---------------|------------------------------|
| `plan` | Next action is PLAN | PLAN | — |
| `execute` | Next action is EXECUTE | EXECUTE | — |
| `review` | Next action is REVIEW | REVIEW | — |
| `close` | Next action is CLOSE | CLOSE | — |
| `audit_boundary` | Phase done; human decides next phase or terminus | EXIT | `set phase=N+1 state=plan` (advance) or `set state=done` (terminate) |
| `audit_escalation` | Worker hit an escalation; human required | EXIT | `set state=execute\|review\|...` (resume after resolving) |
| `done` | Project terminal; no further dispatch | EXIT | `set phase=N+1 state=plan` (deliberately add a phase) |

CLOSE workers always transition to `audit_boundary` (conservative closure —
the human, not the worker, decides whether the next state is `plan` or
`done`). EXECUTE and REVIEW workers transition to `audit_escalation` on
escalation per their instruction files.

### The four worker actions

Each phase moves through four actions. The state machine dispatches them; the
worker performs them. Each has a single-purpose instruction file.

| Action | Trigger | What the worker does | Instruction file |
|--------|---------|----------------------|------------------|
| `PLAN` | `state == "plan"` | Identify the next phase, choose the regime, break work into steps (Build) or set a time budget (Refine/Explore), write the phase record and dependency-probe results if non-leaf | [`instructions/plan.md`](instructions/plan.md) |
| `EXECUTE` | `state == "execute"` with pending steps | Pick the next pending step, implement and test, commit, log to devlog, transition to review when the last step is done | [`instructions/execute.md`](instructions/execute.md) |
| `REVIEW` | `state == "review"` | Read all phase code, categorize findings as Must/Should/Optional, apply Must+Should, log skipped Optionals as decisions | [`instructions/review.md`](instructions/review.md) |
| `CLOSE` | `state == "close"` | Phase-level tests, integration check if non-leaf, gotcha promotion, contract propagation, decision closure, mark phase complete, set the human gate | [`instructions/close.md`](instructions/close.md) |

`EXIT` is also emitted by the state machine (when there's nothing to do or the
budget is exhausted) — the worker doesn't perform it; it just stops.

### `i2c state` operations

```bash
# Top-level keys on a JSON-object file (project.json)
i2c state set project.json state=execute

# Advance to a new phase + transition to plan (one atomic write)
i2c state set project.json phase=12 state=plan

# Mark a step or phase complete (matched by key)
i2c state complete steps.json --phase 11 --step 3 --commit abc1234
i2c state complete phases.json --phase 11

# Append one record to an array file (steps/phases/decisions)
i2c state append-record steps.json '{"phase":11,"step":4,"title":"...","status":"pending"}'

# Update fields on one record in an array file (matched by single key=value)
i2c state update-record decisions.json --match id=D-22 status=closed decision="..."

# Append a record to devlog.jsonl (JSONL)
i2c state append devlog.jsonl '{"phase":11,"step":3,...}'

# Append a string to project.json.gotchas
i2c state append-gotcha project.json "fsync after every append"
```

For payloads containing `$` or newlines, pass `--from-file <path>` instead of
an inline string to avoid shell-quoting hazards.

### Versioning & migration

A `.state/` directory is shaped by the framework version that wrote it.
`project.json` carries an optional `schema_version` recording which framework
schema the project targets. `i2c init` stamps the current version on new
projects; an **absent** `schema_version` means a legacy, pre-versioning project
(treated as version 0).

`i2c migrate` upgrades a project's `.state/` in place to the schema the
installed `i2c` expects:

```bash
i2c migrate              # apply any needed migration, then stamp the version
i2c migrate --check      # report only: exit 1 if a migration is needed, else 0 (CI-friendly)
i2c migrate --dry-run    # show the changes that would be applied; never writes
```

Runtime tools (`assemble` / `state` / `run` / `status`) are **not** gated on the
version, so existing unversioned projects keep working; drift is surfaced only by
`i2c migrate --check`. A migration needed exits 1 under `--check`; a
`.state/` that targets a *newer* schema than the installed `i2c` exits 2 (upgrade
`i2c`). Migrations are in-place and atomic — your git history is the backup, and
`--dry-run` previews the change first. See [`CHANGELOG.md`](CHANGELOG.md) for the
per-version migration notes.

### Recovery (diagnose / reconcile)

When an iteration dies or is cut off mid-step, `.state/` can drift out of sync
with reality — a commit landed but its step stayed `pending`, all steps
completed but `project.state` never advanced, a phase was marked complete but
the close gate was never set. i2c detects and repairs this **workflow drift**
(the one failure class recovery owns; code/spec/env bugs are left to REVIEW +
human judgment).

- **Drift audit** (`i2c/recovery.py`) — a deterministic check of `.state/`
  against itself and against git/disk (commit-without-step, state-not-advanced,
  dirty tree, …), with CRLF/whitespace false-positive guards and a clean no-op
  off a git repo.
- **`i2c diagnose [--target N]`** — read-only. Runs the audit first and
  classifies the failure (`workflow-drift` / `unknown` / `none`), flagging the
  #1 trigger: a missing/malformed 2-line exit signal.
- **`i2c reconcile [--apply]`** — dry-run by default; `--apply` is the human
  gate. Applies the deterministic fixes **only through `i2c state`** (never
  writing `.state/` directly) and surfaces judgment calls untouched; it never
  marks a code-blocked step complete.
- **Out-of-band dispatch** — `i2c run --action diagnose|reconcile --target N`
  runs the action as a worker against a specific iteration, bypassing the state
  machine. The runner also prints a non-fatal drift advisory after each
  lifecycle action.

This extends i2c's detect-and-halt post-action invariants into
*detect-and-reconcile*. Full design:
[`archive/DESIGN_recovery_v1.md`](archive/DESIGN_recovery_v1.md). The deferred
`fix` code-repair agent is tracked in [`FUTURE_recovery.md`](FUTURE_recovery.md).

---

## Two execution modes

### Autonomous

The loop runner ([`i2c/run_iteration.py`](i2c/run_iteration.py)) invokes
the worker once per ACTION. Before each invocation it:

1. Calls `state_machine.py` to determine the next ACTION.
2. Calls `assemble_context.py` to build the full prompt (instructions,
   adapter, project state, project docs, devlog tail).
3. Pipes the prompt into `claude -p` or `codex exec`.
4. Parses the worker's two-line exit signal and writes `summary.log`.

The worker reads zero governance files. It performs its action, writes
outcomes via `i2c state`, emits the exit signal, and exits. The runner
re-invokes for the next action. (A multi-iteration driver — one invocation
covering several steps — is on the roadmap.)

At phase close the worker transitions to `state=audit_boundary` and the loop
halts. A human audits, then writes one of:

- `i2c state set project.json phase=N+1 state=plan` — advance to
  the next phase, or
- `i2c state set project.json state=done` — declare the project
  terminal,

and the loop resumes (or stops permanently on `done`).

### Supervised

A human works directly with an AI assistant. The same tools serve both modes —
only the caller differs.

```bash
# Cold-start orientation
i2c status

# Per-action context, framed for human approval
i2c assemble --action plan --phase N --mode supervised
```

Under `--mode supervised` the assembler strips `autonomous_only` framing
(Output Contract, Behavioral Rules, Next State) and reframes `Action: $TYPE`
as `Active Action: $TYPE` so the assistant pauses for human approval before
committing. Both modes use the same `.state/`, `i2c state`, schemas, and
instruction files.

---

## Work modes (for humans)

Each working session runs in one mode at a time. These are *workflow* modes,
distinct from the *execution* modes above.

**1. Discuss (no code changes)** — determine scope, identify the regime,
specify accordingly. Prefer the simplest solution; reuse existing code;
preserve existing architecture unless there's a clear reason to change. Ends
with a `.state/` update (a new phase plan, or a decision record).

**2. Code / Debug** — implement the plan; for debugging, propose a testable
hypothesis first, then change code. Switching between the two within a session
is expected.

**3. Review** — improve existing code rather than add features. Priority #1:
preserve functionality. Priority #2: simplify and reduce code. Confirm
alignment with the architecture.

These map informally to the worker actions (PLAN↔Discuss, EXECUTE↔Code/Debug,
REVIEW↔Review); CLOSE is mostly housekeeping with no clean human equivalent.

---

## Chat surface (Telegram)

A Telegram bot exposes the same views and actions over chat — a thin,
deterministic adapter over `i2c.control` (no LLM logic in the bot; it dispatches
the same operations the CLI does). Install the optional extra:

```bash
pip install i2c[telegram]
export I2C_TELEGRAM_TOKEN=...                 # bot token (environment only)
i2c serve telegram --root /path/to/projects
```

The bot scans `--root` for projects, so one bot serves a whole portfolio.
Commands:

- **Read (open):** `/audit [proj] [facet]` — the read hub; `facet` is
  `(none)` → summary, or `phase N` / `decisions [N]` / `devlog [N]` /
  `escalation` / `logs [N]` / `logs iter N`. Plus `/diagnose [proj] [N]`
  (recovery drift audit + classification), `/portfolio` (cross-project),
  `/setdir <proj>` (set the current project), `/commands`.
- **Admin (gated to the `admins` in the `[telegram]` table of `i2c.toml`):**
  `/run [proj] [N] [backend]` — N iterations (default 1) on a single backend;
  `/batch [proj]` — run a whole phase to a halt, choosing the backend
  per-action from `[run.backends]`; `/reconcile [proj] [apply]` — apply
  workflow-drift fixes (dry-run unless `apply`); `/endphase [proj] [last]` —
  clear the `audit_boundary` (advance, or `last` to terminate).

---

## Bootstrap a new i2c project

1. **Create the project directory** and initialize git.

2. **Install the framework** so the `i2c` command and the bundled schemas are
   available:

   ```bash
   pipx install i2c        # recommended: puts `i2c` on PATH + isolated env
   # or, for framework development from this checkout:
   pip install -e .        # inside a venv; see Requirements re: PEP 668
   ```

   This installs the `i2c` console command and the bundled assets — JSON
   Schemas, `WORKER_SPEC.md`, and `instructions/` (package data). Your project
   carries **no** framework Python or canonical markdown. Confirm your install
   with `i2c doctor` before continuing (it checks PATH, dependencies, schemas,
   and backends) — see [Requirements](#requirements) for PATH, PEP 668
   (externally-managed environments), and autonomous/containerized setup notes.

3. **Scaffold the project** with `i2c init` (run in the project root):

   ```bash
   i2c init                      # or: i2c init --name MyProject --backend both
   ```

   This seeds `.state/` (`project.json` at phase 0 / state plan, empty
   `phases`/`steps`/`decisions`, empty `devlog.jsonl`), writes `PROJECT.md` and
   `ARCHITECTURE.md` templates, scaffolds the adapter(s)
   (`CLAUDE.md` / `CODEX.md`), and adds `logs/loop/` to `.gitignore`. It refuses
   to clobber an existing project unless you pass `--force`.

4. **Fill in the scaffolded files:**
   - `PROJECT.md` — scope, constraints, success criteria
   - `ARCHITECTURE.md` — component map, implementation sequence
   - `CLAUDE.md` / `CODEX.md` — adapter Available Modules + project notes
   - `ARCH_<module>.md` for each module that needs a contract (see
     [`ref/SPEC_architecture.md`](ref/SPEC_architecture.md))

   To customize a packaged procedure, eject it into the project and edit the
   local copy (it then wins over the packaged default, §5.3):

   ```bash
   i2c eject instructions/plan.md     # or: i2c eject WORKER_SPEC.md / instructions
   ```

5. **Run the smoke test** to confirm the toolchain works in your environment:

   ```bash
   python3 examples/smoke_test.py
   ```

6. **Begin Phase 1** in supervised mode:

   ```bash
   i2c assemble --action plan --phase 1 --mode supervised
   ```

   and follow the procedure in the assembled `Instructions` section.

To run autonomously instead, drive the loop with
`i2c run --backend claude --max-budget-usd 5.00` from the project root.

### Configuration (`i2c.toml`)

`i2c init` writes a starter `i2c.toml` at the project root that records default
settings for `i2c run`, so you don't re-type flags:

```toml
[run]
backend = "claude"      # claude | codex
model = "sonnet"
max_budget_usd = 5.00
```

Precedence is **CLI flag > `i2c.toml` > built-in default** — e.g. `i2c run
--backend codex` overrides the file for one invocation. Only the `[run]` table
is read today; unknown keys are ignored. **Secrets / API keys do not belong in
`i2c.toml`** — configure those via environment variables.

> A consumer project installs the framework (`pip install`) and carries only
> its own `.state/`, project docs, `i2c.toml`, and filled-in adapters — no
> framework Python and no canonical markdown. `WORKER_SPEC.md` and
> `instructions/` ship in the package and resolve project-local-override →
> packaged default (§5.3); a project overrides either per-file when it needs to
> customize.

---

## Repository layout

```
i2c/
├── README.md                     ← this file
├── LICENSE                       ← MIT
├── CHANGELOG.md                  ← versioned release + migration notes
├── pyproject.toml                ← package metadata + `i2c` console entry point
│
├── ref/                          ← human-facing reference for ARCH-file authoring
│   ├── SPEC_architecture.md      Pattern A / Pattern B templates
│   └── GUIDE_architecture.md     process walkthrough
│
├── i2c/                          ← the installable package
│   ├── __init__.py
│   ├── cli.py                    `i2c` dispatcher (status/next-action/state/assemble/run/init/eject/migrate/…)
│   ├── scaffold.py               `i2c init` + `i2c eject`
│   ├── control.py                in-process command API (structured returns)
│   ├── state.py                  atomic, schema-validated write CLI (--from-file flag)
│   ├── validate.py               schema loader + validation helpers
│   ├── assemble_context.py       builds worker prompts and section snapshots
│   ├── state_machine.py          ACTION + NEXT computation (read-only)
│   ├── invariants.py             post-action invariant checks
│   ├── run_iteration.py          single-iteration autonomous runner
│   └── data/                     package data
│       ├── schemas/              JSON Schema for every state file
│       ├── WORKER_SPEC.md        universal worker loop contract (project-overridable)
│       ├── instructions/         per-action procedures (project-overridable)
│       ├── adapters/             claude.md / codex.md (i2c init scaffold source)
│       └── templates/            PROJECT.md / ARCHITECTURE.md / i2c.toml (i2c init scaffold source)
│
├── templates/.claude/commands/   ← slash-command wrappers for supervised mode
├── tests/                        ← unit tests (stdlib unittest)
└── examples/
    ├── initial_state/            canonical fixture: a mid-phase project
    └── smoke_test.py             end-to-end CLI walkthrough
```

A project *using* i2c keeps its own `.state/`, `PROJECT.md`, `ARCHITECTURE.md`,
`ARCH_<module>.md`, governance markdown, and filled-in adapters, and installs
the framework via `pip`; `logs/loop/` holds runner output in autonomous mode and
is gitignored.

---

## Reference docs

- [`ARCH_assembler.md`](ARCH_assembler.md) — the context assembler's contract:
  CLI surface, section catalog, assembly matrix, error policy.
- [`ref/SPEC_architecture.md`](ref/SPEC_architecture.md) +
  [`ref/GUIDE_architecture.md`](ref/GUIDE_architecture.md) — how to author the
  ARCH files that drive planning.
- [`DECISIONS.md`](DECISIONS.md) — index of every architecture decision
  (D1–D21, D-state-*, D-pkg-*, …) with status and a pointer to the
  authoritative design doc.
- [`WORKFLOW.md`](WORKFLOW.md) — actor topology and dispatch-flow diagrams.

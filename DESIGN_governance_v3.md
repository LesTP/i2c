# Design Spec — i2c: Structured State and Deterministic Dispatch

**Status:** Accepted
**Date:** 2026-05-27 (proposed) → 2026-05-28 (accepted)
**Project:** i2c (idea to code) — standalone at `p:\shared\i2c`
**Lineage:** e2e v2 (pseudocode in WORKER_SPEC) → v2.5 (state_machine.sh) → i2c (structured backend + per-action instruction files)

---

## 1. Motivation

### What v2/v2.5 solved

The v2 governance framework replaced prose-based state transitions with a
deterministic pseudocode loop. The v2.5 iteration moved that logic into
`state_machine.sh` — a bash script the worker calls before each action.
The worker no longer computes transitions or exit conditions.

### What remains unsolved

**The worker still parses markdown for state.**
The state machine script greps DEVPLAN for checkboxes, parses YAML
frontmatter with sed, and scopes checkbox counts to phase sections using
regex. This is deterministic code reading a non-deterministic format.
Ambiguity bugs (phase-scoped grep, empty value vs 0) trace directly to
this impedance mismatch.

**The same concepts appear in multiple documents.**
How to do a review is described in GOVERNANCE.md (conceptually),
WORKER_SPEC.md (when the state machine enters review), phase-review.md
(the procedure), and the adapter file (how to invoke it). Four files
touching the same thing. Changes must be propagated manually across all four.

**The worker loads process documentation it doesn't need.**
GOVERNANCE.md describes regimes, modes, contract changes, and doc formats.
The worker needs to know how to execute a specific action — not the full
process framework. Loading GOVERNANCE.md wastes context tokens on content
the worker never acts on.

**Doc updates are edits, not writes.**
The worker appends DEVLOG entries by editing markdown. It marks steps
complete by checking off checkboxes in prose. It updates frontmatter with
sed. Every edit requires a fresh read, careful string matching, and risks
clobbering concurrent changes.

---

## 2. Core Idea: Separate State from Content

Split the governance system into two concerns:

**Structured state** — phases, steps, status, transitions, metrics.
Machine-readable JSON. Written and read by scripts and workers. The state
machine operates on this data.

**Narrative content** — architecture contracts, project scope, action
instructions. Human-readable markdown. The worker reads this for context
when performing an action.

```
┌─────────────────────────────────────────────────┐
│  STRUCTURED STATE (.state/*.json, .jsonl)        │
│  phases, steps, status, blocked, gotchas         │
│  Read/written by: state_machine, assembler,      │
│  worker (via state.py), codexbot                 │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  STATE MACHINE (bash + jq)                       │
│  Reads: .state/project.json, .state/steps.json   │
│  Outputs: ACTION + NEXT                          │
│  Called by runner BEFORE worker invocation        │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  CONTEXT ASSEMBLER (assemble_context.py)         │
│  Reads: .state/, instructions/, project docs,    │
│         WORKER_SPEC.md, adapter file             │
│  Outputs: structured prompt with all governance  │
│           context the worker needs               │
│  Called by runner, output becomes worker prompt   │
└──────────────────┬──────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────┐
│  WORKER (Claude or Codex session)                │
│  Receives: full context in prompt (zero file     │
│            reads for governance content)          │
│  Reads: source files, test files (on demand)     │
│  Writes: source code, .state/ via state.py       │
└─────────────────────────────────────────────────┘
```

### What each layer owns

| Layer | Format | Who reads | Who writes |
|-------|--------|-----------|------------|
| Structured state | JSON / JSONL | State machine, assembler, codexbot | Worker (outcomes via `state.py`), codexbot (`/close`) |
| Instruction files | Markdown | Assembler (includes one per action) | Human (framework maintenance) |
| Project docs | Markdown | Assembler (includes per action type) | Human (architecture), worker (contract propagation) |
| WORKER_SPEC.md | Markdown | Assembler (always included) | Human (framework maintenance) |
| Adapter files | Markdown | Assembler (always included) | Human (project setup) |

The worker never reads governance files directly — everything arrives
pre-assembled in the prompt. The worker only reads source code and test
files that it needs to inspect or modify.

No standalone GOVERNANCE.md. Process knowledge that workers need is in
instruction files and WORKER_SPEC. Process knowledge for humans is folded
into orientation docs (README, setup guide).

---

## 3. Deterministic Dispatch and Context Assembly

> **Note (2026-06-04):** For the authoritative assembler contract — CLI surface, section catalog, assembly matrix, error policy, conditional-section mechanism — see [`ARCH_assembler.md`](ARCH_assembler.md). Where this section and that contract disagree on assembler behavior, the ARCH contract wins. This section remains useful as design rationale.

### The invocation flow

In e2e v2.5, the worker called the state machine and read its own
context files. In i2c, the runner does both before the worker starts:

```
e2e (old): runner → invoke worker → worker reads files → worker calls
           state_machine → worker reads instruction file → worker works

i2c (new): runner → state_machine.sh → assemble_context.py → invoke
           worker with full prompt → worker works → worker writes state
```

The worker wakes up with everything it needs in-context. Zero governance
file reads. Zero file-discovery turns.

### Single-step invocations (STEP_BUDGET = 1)

The common case. The runner handles everything before the worker starts:

```
1. runner calls: bash tools/state_machine.sh
   → reads .state/project.json, .state/steps.json
   → outputs: ACTION + NEXT (or EXIT)
   → if EXIT: runner stops, no worker invocation

2. runner calls: python3 tools/assemble_context.py --action $ACTION --phase $PHASE
   → reads: WORKER_SPEC, adapter, instructions/$ACTION.md, project.json,
            PROJECT.md, ARCH_module.md, steps.json, devlog.jsonl
   → outputs: structured prompt to stdout

3. runner invokes worker with assembled prompt:
   → claude -p "$PROMPT" or codex exec "$PROMPT"

4. worker does the work (reads only source/test files)

5. worker writes outcomes via: python3 tools/state.py ...

6. worker emits EXIT signal
```

### Multi-step invocations (STEP_BUDGET > 1)

For multi-step, the worker calls the state machine between steps. The
first step's context arrives pre-assembled; subsequent steps use the
assembler as a mid-step context provider:

```bash
# Mid-step context request (called by worker between steps):
python3 tools/assemble_context.py --action execute --phase 11

# Or request a single section:
python3 tools/assemble_context.py --section architecture
python3 tools/assemble_context.py --section module event_store
python3 tools/assemble_context.py --section devlog --phase 10
```

The assembler serves double duty: pre-invocation full-prompt builder AND
mid-step context provider. Same tool, two modes.

### The state machine script (bash + jq)

Reads `.state/project.json` and `.state/steps.json`. No markdown parsing.
Called by the runner before invocation, not by the worker.

```bash
# e2e v2.5 (old): grep checkboxes in markdown
unchecked=$(sed -n "/^## Phase ${phase}/,/^## /p" "$DEVPLAN" | grep -c '^- \[ \]')

# i2c: read from structured state
pending=$(jq '[.[] | select(.phase == '$phase' and .status == "pending")] | length' .state/steps.json)
```

### The context assembler (Python)

`tools/assemble_context.py` reads structured state, governance files,
and project docs, then outputs a structured prompt with clear section
delimiters. The worker receives this as its invocation prompt.

**What gets assembled per action:**

| Section | Source | PLAN | EXEC | REVIEW | CLOSE |
|---------|--------|------|------|--------|-------|
| Identity + loop contract | WORKER_SPEC §1, §3 | ✓ | ✓ | ✓ | ✓ |
| Escalation + prohibitions | WORKER_SPEC §5, §8 | ✓ | ✓ | ✓ | ✓ |
| Output contract | WORKER_SPEC §6 | ✓ | ✓ | ✓ | ✓ |
| Tool rules | Adapter file | ✓ | ✓ | ✓ | ✓ |
| Project state | project.json | ✓ | ✓ | ✓ | ✓ |
| Gotchas | project.json | ✓ | ✓ | ✓ | ✓ |
| Project scope | PROJECT.md | ✓ | | | |
| Action instructions | instructions/$ACTION.md | ✓ | ✓ | ✓ | ✓ |
| Full architecture | ARCHITECTURE.md | ✓ | | ✓ | |
| Module contract | ARCH_module.md | ✓ | ✓ | ✓ | ✓ |
| Current steps | steps.json (current phase) | | ✓ | ✓ | ✓ |
| Recent devlog | devlog.jsonl (last 5) | | ✓ | | |
| Phase devlog | devlog.jsonl (full phase) | | | ✓ | ✓ |
| Prior phase summary | devlog.jsonl (prev phase, last 3) | ✓ | | | |
| Decisions | decisions.json | | | ✓ | ✓ |
| Module list | Adapter or ARCHITECTURE.md | ✓ | ✓ | ✓ | ✓ |

**Assembled prompt structure:**

```
═══════════════════════════════════════════════
WORKER CONTRACT
═══════════════════════════════════════════════

## Identity
[from WORKER_SPEC §1]

## Main Loop
[from WORKER_SPEC §3]

## Escalation Conditions
[from WORKER_SPEC §5]

## Output Contract
[from WORKER_SPEC §6]

## Prohibitions
[from WORKER_SPEC §8]

═══════════════════════════════════════════════
ACTION CONTEXT
═══════════════════════════════════════════════

## Action: EXECUTE
## Next State: execute
## Phase: 11 — Orchestrator (Build)
## Step: 3 — Slash commands

## Instructions
[from instructions/execute.md]

═══════════════════════════════════════════════
PROJECT CONTEXT
═══════════════════════════════════════════════

## Module Contract: Orchestrator
[from ARCH_orchestrator.md]

## Current Phase Steps
[from steps.json, filtered to phase 11]

## Recent Activity
[from devlog.jsonl, last 5 entries]

## Gotchas
[from project.json gotchas array]

═══════════════════════════════════════════════
TOOL RULES
═══════════════════════════════════════════════

[from adapter file — Claude or Codex specific]
```

### The write API (Python CLI)

`tools/state.py` — thin Python CLI using only stdlib `json`. Provides
atomic writes (write to temp file, `os.replace()`), field validation,
and a clean interface for the operations the worker needs:

```bash
# Update project state
python3 tools/state.py set project.json state=review blocked=false

# Mark a step complete
python3 tools/state.py complete steps.json --phase 11 --step 3 --commit abc123

# Append a devlog entry
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":1,...}'

# Add a gotcha
python3 tools/state.py append-gotcha project.json "jq empty string vs null: use // default"
```

**Why Python for writes, bash for reads:** The state machine only reads
and does simple arithmetic (budget decrement). Bash + jq handles this
cleanly. Writes are more complex — find-and-update in arrays, atomic
file replacement, JSONL append. Python's `json` stdlib and `os.replace()`
make these operations natural and safe. The split is: bash reads, Python
writes.

### Per-action instruction files

Four core files matching the state machine's four actions. Each is
self-contained — the assembler includes one per action in the prompt.

| File | Contains |
|------|----------|
| `instructions/plan.md` | How to plan a phase. Regime identification, step breakdown format, what to write to steps.json. **Conditional section:** dependency probe for non-leaf modules. |
| `instructions/execute.md` | How to do a step. Test-before-commit, devlog entry format, how to mark step complete via state.py. |
| `instructions/review.md` | How to review. Priorities, finding categories (must/should/optional), how to apply fixes. |
| `instructions/close.md` | How to close. Phase-level tests, learning review, contract propagation, gotcha promotion. **Conditional section:** integration check for non-leaf modules. |

Conditional sub-procedures (dependency probe, integration check) are
sections within the relevant instruction file, not separate files. The
assembler reads the instruction file, evaluates conditions against
`.state/`, and includes or strips conditional sections deterministically:

```markdown
## Pre-plan: Dependency Probe (non-leaf modules only)

**When:** This module depends on other modules that are already built.
**Skip if:** This is a leaf module with no cross-module dependencies.

[procedure...]
```

This avoids separate files and dispatch logic. The assembler evaluates
conditions against `.state/` and strips sections that don't apply —
the worker receives only the instructions relevant to its situation.

These replace: WORKER_SPEC §3 action table, COMMANDS/phase-plan.md,
COMMANDS/step-done.md, COMMANDS/phase-review.md, COMMANDS/phase-complete.md,
COMMANDS/dependency-probe.md, COMMANDS/integration-check.md, and the
relevant sections of the old GOVERNANCE.md.

---

## 4. Structured State — What Lives There

### Core state (always present)

```
.state/
  project.json      — current state, phase, blocked flag, gotchas
  phases.json        — array of phase records
  steps.json         — array of step records (all phases)
  devlog.jsonl       — append-only structured log entries
  decisions.json     — decision records with status
```

All files are git-tracked. Git diff shows state transitions clearly
in JSON.

### project.json — the state machine's primary input

```json
{
  "phase": 11,
  "state": "execute",
  "blocked": false,
  "steps_remaining": 7,
  "gotchas": [
    "jq empty string vs null: use // \"default\" to avoid silent failures",
    "sed -i behaves differently on macOS (requires backup extension)"
  ]
}
```

Replaces: DEVPLAN frontmatter (`phase`, `state`, `blocked`,
`steps_remaining`) and DEVPLAN Cold Start Summary's gotchas field.

The assembler includes this in every worker prompt alongside PROJECT.md.
Between these two sources, the worker knows where the project is (state)
and what the project is (scope). No DEVPLAN needed.

`gotchas` lives here because `project.json` is included in every
assembled prompt — gotchas are immediately visible without extra assembly logic.
They're short operational strings, not prose. Written at phase close
(promoted from devlog learnings), read at cold start and during plan.

### phases.json

```json
[
  { "id": 1, "module": "event_store", "title": "Core storage", "regime": "build", "status": "complete" },
  { "id": 11, "module": "orchestrator", "title": "Pipeline + event loop", "regime": "build", "status": "in_progress" }
]
```

Replaces: DEVPLAN phase summaries.

### steps.json

```json
[
  { "phase": 11, "step": 1, "title": "Core pipeline wiring", "status": "complete", "commit": "abc1234" },
  { "phase": 11, "step": 2, "title": "Event loop", "status": "complete", "commit": "def5678" },
  { "phase": 11, "step": 3, "title": "Slash commands", "status": "pending" }
]
```

Replaces: DEVPLAN step checklists (the `- [x]` / `- [ ]` lines).
The state machine counts `status: "pending"` in the current phase.
No checkbox grep. No phase-section scoping.

### devlog.jsonl — append-only

```json
{"phase": 11, "step": 1, "action": "execute", "outcome": "complete", "contracts": [], "summary": "Wired pipeline topology with dependency injection. 42 tests pass.", "commit": "abc1234", "timestamp": "2026-05-27T04:00:00Z"}
{"phase": 11, "step": 2, "action": "execute", "outcome": "complete", "contracts": ["ARCH_orchestrator.md"], "summary": "Event loop with debounced extraction. Fixed coaching event routing.", "commit": "def5678", "timestamp": "2026-05-27T04:15:00Z"}
```

Replaces: DEVLOG.md markdown entries. Each line is a complete record.
No archival needed — filter by phase with `jq` or `grep`. A single
file for the life of the project. JSONL entries are small (~200 bytes
each); a 50-phase project is ~10KB. No compaction until real projects
show otherwise.

### decisions.json

```json
[
  { "id": "D-12", "title": "Round structure", "status": "open", "priority": "critical", "decision": "Signal-based, not time-based", "rationale": "...", "revisit_if": "Game moderator specifies time-based rounds" }
]
```

Replaces: DECISIONS.md markdown entries.

---

## 5. What Stays as Markdown

Narrative content that humans author and workers read for context:

- **PROJECT.md** — scope, constraints, success criteria
- **ARCHITECTURE.md** — component map, data flow, contracts
- **ARCH_*.md** — per-module interface specs
- **WORKER_SPEC.md** — loop contract, escalation, prohibitions (backend-agnostic)
- **instructions/*.md** — per-action worker instructions
- **Adapter files** (CLAUDE.md, CODEX.md) — tool-specific mechanics + project notes

### No persistent rendered views

There is no DEVPLAN.md or DEVLOG.md in i2c projects. The structured
state in `.state/` is the single source of truth. Human-readable views
are assembled on demand:

- **Codexbot commands** (`/status`, `/audit`, `/progress`) assemble
  formatted views from `.state/` JSON at query time.
- **Orchestrator sessions** read `.state/` directly when reporting to
  the human.
- **Optional render scripts** (`tools/render_status.sh`) can print a
  human-readable summary to stdout when needed. These are view-layer
  utilities, not persistent files.

Maintaining parallel rendered views (JSON + markdown) creates a sync
obligation with no benefit — the JSON is small, self-explanatory, and
readable by both LLM workers and humans in a text editor.

---

## 6. State Format: JSON

**Decision: JSON for all structured state.** YAML was considered and
rejected.

JSON wins because:
- `jq` is the standard CLI tool — powerful, well-documented, universal
- JSONL for append-only logs is a natural fit (one record per line)
- No indentation ambiguity — parseable without context
- Python's `json` module is stdlib, no dependencies
- Codexbot already uses JSON throughout

YAML's advantage (human editability) doesn't apply: humans rarely edit
state directly in i2c — they issue commands (`/close`, `/run`) that
modify state through scripts. JSON is editable in emergencies — unlike
a database, you can fix it in any text editor.

The Obsidian model (one file per entity with YAML frontmatter) was
considered for its visual dashboard. Rejected due to file sprawl and
YAML parsing fragility. If an Obsidian dashboard is desired later,
generate it from JSON state on demand — a render script that creates
per-entity markdown files from `phases.json` and `steps.json`.

---

## 7. Worker Spec and Adapter Architecture

### Three-file structure (assembled, not loaded by worker)

Three governance files contribute to the worker's context, each with a
distinct role. The assembler reads all three and includes them in the
prompt — the worker never reads them directly:

| File | Scope | Content |
|------|-------|--------|
| **Adapter** (CLAUDE.md / CODEX.md) | Per-project, per-backend | Tool rules, project notes, module list |
| **WORKER_SPEC.md** | Shared across projects, backend-agnostic | Loop contract, escalation conditions, output contract, prohibitions |
| **instructions/$ACTION.md** | Shared across projects, action-specific | Full procedure for one action (plan/execute/review/close) |

Plus structured state (`.state/`) and project docs (`PROJECT.md`,
`ARCH_*.md`) — also included by the assembler, not read by the worker.

### What changes from e2e

**WORKER_SPEC.md** simplifies:
- §3 Main Loop: writes `.state/` JSON via `state.py`, not `sed` on DEVPLAN
- §4 Document Discipline: rewritten for structured state (no DEVPLAN/DEVLOG/DECISIONS markdown edits)
- §§1, 5, 6, 7, 8: unchanged (identity, escalation, output contract, autonomous rules, prohibitions)

**Adapter files** slim down significantly:
- Remove: action-to-command-file mapping table (replaced by `instructions/$ACTION.md`)
- Remove: duplicated output contract (lives only in WORKER_SPEC)
- Remove: reading tiers (the assembler handles what to include per action)
- Keep: tool-specific rules (backend-specific behavioral guidance)
- Keep: project-specific notes (language, test framework, key constraints)
- Keep: module list (quick orientation)
- The adapter is read by the assembler and included in the prompt — the
  worker never reads it as a file

**GOVERNANCE.md** disappears as a standalone file. Its content splits:
- Process concepts (regimes, modes) → human orientation docs, referenced by instruction files when needed
- Step execution procedures → `instructions/execute.md`
- Review procedures → `instructions/review.md`
- Phase close procedures → `instructions/close.md`
- Doc formats → eliminated (no DEVPLAN/DEVLOG markdown to format)
- Contract change rules → `instructions/execute.md` and `instructions/close.md`

### Why keep WORKER_SPEC separate from adapters

WORKER_SPEC is ~150 lines and identical across backends. Merging it into
adapters would duplicate the loop contract, escalation conditions, and
output format across CLAUDE.md and CODEX.md — creating exactly the
multi-file sync problem that motivates this redesign. Keeping it separate
means one source of truth for the loop contract.

### Why keep WORKER_SPEC separate from instruction files

The loop contract (identity, escalation, output format, prohibitions) is
universal — it applies regardless of which action the worker performs.
Folding it into each instruction file would scatter the contract across
4 files. The worker reads WORKER_SPEC once at cold start; it doesn't
re-read it per action.

---

## 7.5. Orchestrator Contract

The orchestrator is an AI session (Claude or Codex) that dispatches
work, analyzes results, and reports to the human. It sits between the
human and the runner. It never implements code.

### Role and boundaries

**The orchestrator does:**
- Dispatch iterations: `bash run-iteration.sh -n N`
- Read project state: `.state/project.json`, `.state/steps.json`
- Read iteration results: `logs/loop/summary.log`, iteration transcripts
- Analyze patterns across iterations (log review, failure clustering)
- Report to human (Telegram, conversation, or other channel)
- Clear phase gates: `python3 tools/state.py set project.json blocked=false state=plan`
- Promote gotchas: `python3 tools/state.py append-gotcha project.json "..."`
- Orient itself: `python3 tools/assemble_context.py --section status`

**The orchestrator must NOT:**
- Read worker adapter files (CLAUDE.md, CODEX.md) — doing so causes the
  orchestrator to follow worker instructions and implement code
- Run build commands, test suites, or modify source files
- Invoke the assembler in worker mode (it uses `--section status` only)

**Fix directly vs dispatch:**
- Fix directly: config edits, file cleanup, non-code doc changes, small
  targeted fixes (1-2 functions, <5 test assertions)
- Dispatch a worker: new features, refactors touching 3+ files, handler
  signature changes, anything that warrants a DEVLOG entry

### What the orchestrator reads

| Source | When | How |
|--------|------|-----|
| `.state/project.json` | Status checks, before dispatch | `jq` or `cat` |
| `.state/steps.json` | Progress assessment | `jq` filter by phase |
| `.state/devlog.jsonl` | Post-run analysis, phase review | `jq` filter by phase/recency |
| `.state/decisions.json` | Phase boundary audit | `jq` filter open/recent |
| `logs/loop/summary.log` | After every run | `tail -N` |
| `logs/loop/iteration_NNN.txt` | When diagnosing anomalies | Full read of specific iteration |
| Assembled status | Cold start, orientation | `python3 tools/assemble_context.py --section status` |

### What the orchestrator writes

| Target | When | How |
|--------|------|-----|
| `.state/project.json` (blocked, state) | `/close` — clearing phase gate | `python3 tools/state.py set project.json blocked=false state=plan` |
| `.state/project.json` (gotchas) | After log review | `python3 tools/state.py append-gotcha project.json "..."` |
| `logs/loop/audits.log` | `/close` — recording audit | Append one line with timestamp, phase, commit |
| Telegram / conversation | After every run, at phase boundaries | Report: iterations, cost, what was built, blockers |

### Command surface

| Command | What the orchestrator does |
|---------|--------------------------|
| `run [N]` | Dispatch N iterations. Report result after each. |
| `batch N` | Run N iterations, stop on error/blocked, post-run log review, report summary. |
| `status` | Read `.state/project.json` + `steps.json`. Report phase, state, steps done/pending, blockers. |
| `close` | Verify last iteration was CLOSE. Append audit record. Clear blocked gate. Report. |
| `audit` | Read `.state/` files. Assess phase completeness: steps, decisions, contracts. |
| `review` | Read `devlog.jsonl` + iteration logs. Identify patterns, failures, learnings. Propose gotchas. |
| `decisions` | Read `decisions.json`. Summarize recent, flag open/low-confidence entries. |
| `logs [N]` | Read iteration N's transcript via digest parser. |
| `cost` | Read `summary.log` for per-iteration costs. Estimate remaining budget. |
| `progress` | Read `phases.json` + `steps.json`. Visual progress: phases complete/total, steps done/pending. |
| `timeline` | Read `devlog.jsonl` timestamps. Phase durations, step averages, projected completion. |
| `update` | Append gotchas and learnings from recent log review. |

### Cold-start recovery

If the orchestrator loses context (compaction, crash, new session):

1. Read the orchestrator contract file (its own adapter)
2. Determine the active project (ask human if unknown)
3. Run `python3 tools/assemble_context.py --section status` for orientation
4. Read `tail -5 logs/loop/summary.log` for recent activity
5. Do NOT read worker adapter files

### Post-run protocol

**After every batch:**
1. Read `summary.log` tail for the batch
2. Run `tools/digest_logs.py` on iteration transcripts
3. Full transcript only if digest shows anomalies
4. Extract learnings → promote to gotchas if pattern repeats 2+ iterations
5. Report to human: iterations, cost, what was built, blockers

**At phase boundaries (blocked=true after CLOSE):**
1. Read `decisions.json` — flag open/low-confidence entries
2. Flag contract changes needing human confirmation
3. Summarize the phase: what was built, iterations, cost, issues
4. Wait for human `/close` before dispatching next phase

### Orchestrator vs codexbot

Both can dispatch work. The difference:

| | Orchestrator | Codexbot |
|---|---|---|
| Nature | AI session (LLM) | Python app (deterministic) |
| Judgment | Makes decisions about dispatch, analysis, reporting | Executes commands mechanically |
| State reads | Uses assembler, `jq`, reads transcripts | Uses `StateReader` class |
| Reporting | Flexible, context-aware summaries | Formatted templates |
| When to use | Interactive sessions, complex analysis, novel situations | Routine dispatch, scheduled batches, simple status checks |

Both use the same underlying tools (`state.py`, `run-iteration.sh`,
`.state/` files). The orchestrator contract is a template —
project-specific values (paths, project table, environment gotchas)
are filled in at project setup.

---

## 8. Deployment Strategy

### Clean break — new projects only

i2c is a new framework, not a migration of e2e. Existing e2e projects
continue using v2.5 (DEVPLAN frontmatter, DEVLOG markdown, COMMANDS/).
New projects adopt i2c from the start.

**Why not gradual migration:**
- Hybrid mode (JSON + markdown) creates dual-maintenance — exactly the
  sync problem we're solving
- Fallback detection (is this a v2 or v3 project?) adds complexity for
  a transitional benefit
- e2e v2.5 works. Deployed projects don't need to change.
- Building clean is faster than migrating and testing backward compat

**What this means for codexbot:**
- `StateReader` replaces `LogReader` — no fallback needed
- `/setdir` targets i2c projects only; e2e projects use the existing
  codexbot deployment
- Codexbot changes are deferred until the state format is finalized and
  tested on a real project

### i2c project layout

```
project/
  .state/
    project.json
    phases.json
    steps.json
    devlog.jsonl
    decisions.json
  instructions/         ← symlinked or copied from i2c framework
    plan.md
    execute.md
    review.md
    close.md
  schemas/              ← JSON Schema files for state validation
    project.schema.json
    steps.schema.json
    phases.schema.json
    devlog_entry.schema.json
    decisions.schema.json
    exit_signal.schema.json
  tools/
    state_machine.sh
    assemble_context.py
    state.py
    validate.py           ← schema validation (~40 lines, from toolkit)
    parse_jsonl.py
    digest_logs.py
  logs/loop/
    summary.log
    iteration_NNN.jsonl
    iteration_NNN.txt
  PROJECT.md
  ARCHITECTURE.md
  ARCH_*.md
  WORKER_SPEC.md        ← symlinked or copied from i2c framework
  CLAUDE.md             ← project-specific adapter
  CODEX.md              ← project-specific adapter
  run-iteration.sh
```

---

## 9. What This Eliminates

| e2e artifact | i2c replacement |
|-------------|----------------|
| DEVPLAN.md (per-project) | `.state/project.json` + `PROJECT.md` |
| DEVPLAN frontmatter (YAML in markdown) | `.state/project.json` |
| DEVPLAN step checklists (`- [x]`) | `.state/steps.json` |
| DEVPLAN phase summaries | `.state/phases.json` |
| DEVPLAN Cold Start Summary | `project.json` (gotchas) + `PROJECT.md` (scope) |
| DEVLOG.md (manual markdown entries) | `.state/devlog.jsonl` |
| DEVLOG_archive.md | Unnecessary — query by phase from JSONL |
| DECISIONS.md (manual markdown) | `.state/decisions.json` |
| GOVERNANCE.md (standalone) | Content split into instruction files + human orientation docs |
| COMMANDS/*.md (7 files) | `instructions/*.md` (4 files with conditional sections) |
| Checkbox grep with phase scoping | `jq` query on steps.json |
| sed-based frontmatter updates | `python3 tools/state.py` writes |
| Markdown DEVLOG formatting | JSONL append (one line per entry) |
| DEVLOG archival dance | Unnecessary — single JSONL file, filter by phase |
| Worker file-discovery turns (3-5 per invocation) | Deterministic context assembly — zero governance reads |
| Tiered reading logic in adapter | Assembler decides what to include per action |

---

## 10. Decisions Log

All design decisions made during review, with rationale.

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Project name: i2c** (idea to code) | Clean identity, separate from e2e lineage |
| D2 | **Clean break, new projects only** | Hybrid mode creates the dual-maintenance problem we're solving. e2e v2.5 works for existing projects. |
| D3 | **No persistent rendered views** | Maintaining parallel JSON + markdown is unnecessary sync weight. Human views are on-demand (codexbot commands, render scripts). |
| D4 | **Write API: Python CLI (`tools/state.py`)** | Atomic writes via `os.replace()`, natural JSON manipulation, stdlib only. State machine stays bash+jq for reads (simpler, no Python dependency for the hot path). |
| D5 | **Cold start: project.json + PROJECT.md** | Single source of truth for state (JSON) and scope (markdown). No DEVPLAN. |
| D6 | **Gotchas in project.json** | Co-located with state the worker reads on every invocation. Short strings, not prose. Written at close, read at cold start and plan. |
| D7 | **No DEVLOG compaction** | JSONL entries are ~200 bytes each. A 50-phase project is ~10KB. Not worth optimizing until real projects show otherwise. |
| D8 | **4 instruction files with conditional sections** | Dependency probe and integration check are sections within plan.md and close.md, not separate files. Avoids dispatch logic and extra file loads. |
| D9 | **State machine: bash + jq** | Simplest viable. The state machine logic is simple reads + a switch statement. Rewrite to Python if it gets complex — it hasn't yet. |
| D10 | **WORKER_SPEC + adapter: keep separate** | WORKER_SPEC is ~150 lines, identical across backends, stable. Merging into adapters duplicates content. Merging into instruction files scatters the loop contract. |
| D11 | **No standalone GOVERNANCE.md** | Worker-facing content moves to instruction files. Human-facing content folds into orientation docs. Eliminates a file the worker was told to load but never acted on. |
| D12 | **Standalone `p:\shared\i2c`** | Sibling to e2e, not a child. Clean separation, own git history. |
| D13 | **Codexbot changes deferred** | StateReader replaces LogReader, but implementation waits until state format is finalized and tested on a real project. |
| D14 | **Full deterministic context assembly** | Runner calls `assemble_context.py` before invocation. Worker receives all governance context in the prompt. Zero governance file reads by the worker. Eliminates 3-5 wasted turns per invocation. |
| D15 | **Structured sections in assembled prompt** | Clear delimiters (`═══`) and section headers. Model can navigate by scanning headers rather than parsing unstructured text. |
| D16 | **Assembler doubles as mid-step context provider** | Worker can call `assemble_context.py --section X` for additional governance context during multi-step invocations. Source code reads remain direct file reads. |
| D17 | **Adapter file survives but shrinks** | Contains only tool rules and project-specific notes. Included by the assembler, not read by the worker directly. Reading tiers eliminated — assembler decides what to include. |
| D18 | **State machine runs in runner, not worker** | For single-step (common case), worker receives ACTION/NEXT in prompt. For multi-step, worker calls state machine between steps. |
| D19 | **Governance content stays markdown; assembler handles conditional filtering** | WORKER_SPEC, instructions, and adapter are all included wholesale or nearly so — structuring them as JSON wraps prose in parse overhead without enabling useful queries. Conditional sections (dependency probe, integration check) are extracted by the assembler using heading patterns, evaluated against `.state/`, and included/excluded deterministically. The worker receives only the instructions that apply — no "skip if" judgment calls. Assembly recipe stays in Python code (not a data-driven config) until complexity warrants it. |
| D20 | **Supervised mode uses the same tools, different caller** | All tools (`state.py`, `assemble_context.py`, `state_machine.sh`) serve both autonomous and supervised modes. In supervised mode: human drives (no runner), assistant calls assembler on demand for orientation (`--mode supervised` strips autonomous-only sections like exit signals and budget management), state writes use the same `state.py`, and there's no exit signal or step budget. Instruction files are mode-agnostic; the assembler adds supervised-mode framing (pause for approval, show before committing). No separate supervised command files — avoids the dual-procedure sync problem that motivated killing COMMANDS/. |
| D21 | **Toolkit validation copied into i2c, not added as dependency** | i2c copies ~40 lines from toolkit's `structured_llm` module (`validate_json_schema`, `load_schema`, `parse_json_response`) into `tools/validate.py`. Only external dependency: `jsonschema`. This keeps i2c self-contained — no `pip install toolkit` required in worker environments. The full `structured_call` (LLM + validate + retry) stays in toolkit, consumed by codexbot for LLM-powered commands (`/review`, `/audit`). Schema validation is used by `state.py` (validate before writing), `assemble_context.py` (validate state at assembly time), and the runner (validate exit signals). Schemas live in `i2c/schemas/`. |

---

## Appendix B: Supervised Mode

### Overview

i2c supports two execution modes with the same toolchain:

**Autonomous:** Runner dispatches worker sessions. No human in the loop
during execution. Human intervenes at phase boundaries (`/close`).

**Supervised:** Human works directly with an AI assistant (Claude in VS
Code, Codex in terminal). The human is in the loop during execution —
reviewing changes, making decisions, steering direction.

### How supervised mode works

The human (or their assistant) calls the same tools the runner calls,
but interactively:

**Cold start / orientation:**
```bash
python3 tools/assemble_context.py --section status
```
Outputs: current phase, state, recent steps, gotchas. Replaces the
e2e `cold-start` command.

**Get instructions for current action:**
```bash
python3 tools/assemble_context.py --action execute --phase 4 --mode supervised
```
The `--mode supervised` flag strips autonomous-only sections (exit
signals, budget management, state machine calls) and adds supervised
framing: pause after major steps, show changes before committing, ask
before ambiguous decisions.

**Write state after completing work:**
```bash
python3 tools/state.py complete steps.json --phase 4 --step 2 --commit abc123
python3 tools/state.py append devlog.jsonl '{"phase":4,"step":2,...}'
```
Same `state.py` as autonomous mode. State writes don't care who calls
them.

### Mode comparison

| Aspect | Autonomous | Supervised |
|--------|-----------|------------|
| Who drives | Runner (run-iteration.sh) | Human + assistant |
| Context assembly | Runner calls assembler → prompt | Assistant calls assembler on demand |
| State machine | Runner calls before invocation | Optional — human knows what to do |
| Instructions | Assembled with autonomous framing | Assembled with `--mode supervised` framing |
| State writes | Worker calls state.py | Assistant calls state.py (same tool) |
| Exit signal | Worker emits EXIT 0/1/2 | Not applicable |
| Phase boundaries | blocked=true, human `/close` | Human decides when phase is done |
| Budget | STEP_BUDGET, steps_remaining | Not applicable |

### What replaces e2e supervised commands

| e2e command | i2c equivalent |
|------------|----------------|
| `cold-start.md` | `assemble_context.py --section status` |
| `phase-plan.md` | `assemble_context.py --action plan --mode supervised` |
| `step-done.md` | `state.py complete` + `state.py append devlog.jsonl` |
| `phase-review.md` | `assemble_context.py --action review --mode supervised` |
| `phase-complete.md` | `assemble_context.py --action close --mode supervised` |

No separate command files. The assembler + state.py replace all five.

---

## Appendix C: Codexbot Integration

### Overview

Codexbot currently reads project state from markdown files
and section parsing (LogReader). With i2c's `.state/` JSON backend,
codexbot reads structured data instead via a new `StateReader`. The
operator command surface stays the same — same slash commands, same
Telegram UX — but the implementation gets simpler, more reliable, and
enables new commands that are impossible with markdown parsing.

i2c projects use `StateReader` exclusively — there is no fallback to
`LogReader`. The existing codexbot deployment continues serving e2e
projects with `LogReader` unchanged.

### Command implementations

| Command | Current (LogReader, markdown) | i2c (StateReader, JSON) |
|---------|-------------------------------|------------------------|
| `/status` | Grep DEVPLAN frontmatter for `state:`, `phase:`. Count checkboxes with regex. | Read `project.json` + count pending in `steps.json`. |
| `/decisions` | Split DECISIONS.md by `##` headings, return last N | `jq '[-N:]'` on `decisions.json` |
| `/review` | Pattern-match DEVLOG sections for anomalies | Query `devlog.jsonl` for current phase, filter by outcome |
| `/audit` | Three file reads + section extraction from DEVPLAN, DEVLOG, DECISIONS | Read `project.json` + `steps.json` + recent `devlog.jsonl` entries |
| `/close` | Append to audits.log, sed DEVPLAN frontmatter `blocked: false` | Write `blocked=false, state=plan` to `project.json` via `state.py` |
| `/update` | Edit markdown — find gotchas section, insert text | Append to `gotchas` array in `project.json` |
| `/logs [N]` | Read iteration transcript | Unchanged — iteration logs stay in `logs/loop/` |
| `/run [N]` | Shell exec → summary.log | Unchanged — `run-iteration.sh` is backend-independent |
| `/batch N` | Shell exec + post-run protocol | Post-run digest reads `devlog.jsonl` instead of DEVLOG.md |

### New commands enabled by structured state

Trivial with JSON, impractical with markdown parsing:

| Command | What it does | Implementation |
|---------|-------------|----------------|
| `/progress` | Visual progress bar: phases complete/total, steps done/pending | `jq` on `phases.json` + `steps.json` |
| `/cost` | Spend report: total, per-phase, per-model | Read `cost_ledger.jsonl` |
| `/timeline` | Phase durations, average step time, projected completion | Timestamps from `devlog.jsonl` |
| `/contracts` | All contract changes across the project | Filter `devlog.jsonl` where `contracts` array is non-empty |
| `/diff <phase>` | All commits in a phase, combined diff | Commit hashes from `steps.json` |
| `/health` | Step success rate, failure clustering, cost trend | Aggregate `devlog.jsonl` + `cost_ledger.jsonl` |

### Dynamic output assembly

The key capability: **codexbot assembles output views at query time
instead of dumping file contents.**

```
# i2c /audit output (assembled from JSON queries):

Phase 11: Orchestrator (Build)
State: execute | Steps: 2/4 complete | Blocked: no

Recent activity (last 3 steps):
  11.1 ✓ Core pipeline wiring (abc1234)
  11.2 ✓ Event loop + routing (def5678)
  11.3 ⬜ Slash commands (pending)

Open decisions: D-12 (round structure)

Contract changes this phase:
  11.2 → ARCH_orchestrator.md (event loop API)
```

Richer, more compact, and computed from JSON queries rather than text
extraction. The formatting logic lives in codexbot's command handlers,
not in project files.

### StateReader API

```python
class StateReader:
    def __init__(self, project_dir: Path):
        self.state_dir = project_dir / ".state"

    def project_state(self) -> dict:
        return json.loads((self.state_dir / "project.json").read_text())

    def phases(self, status: str | None = None) -> list[dict]:
        phases = json.loads((self.state_dir / "phases.json").read_text())
        if status:
            return [p for p in phases if p["status"] == status]
        return phases

    def steps(self, phase: int | None = None, status: str | None = None) -> list[dict]:
        steps = json.loads((self.state_dir / "steps.json").read_text())
        if phase is not None:
            steps = [s for s in steps if s["phase"] == phase]
        if status:
            steps = [s for s in steps if s["status"] == status]
        return steps

    def devlog(self, phase: int | None = None, limit: int = 10) -> list[dict]:
        entries = []
        with (self.state_dir / "devlog.jsonl").open() as f:
            for line in f:
                entry = json.loads(line)
                if phase is None or entry.get("phase") == phase:
                    entries.append(entry)
        return entries[-limit:]

    def decisions(self, count: int = 5, status: str | None = None) -> list[dict]:
        decisions = json.loads((self.state_dir / "decisions.json").read_text())
        if status:
            decisions = [d for d in decisions if d["status"] == status]
        return decisions[-count:]
```

Replaces `LogReader` (~297 lines of regex-based markdown parsing) with
~50 lines of JSON reads. The `ArtifactPackage` interface to the Codex
client stays — it's still a dict of named text sections. The difference
is how those sections are assembled.

### Codexbot components unchanged

- **Command Router** — `CommandSpec`, `ParsedCommand`, `CommandResult`
  don't touch file formats. New commands are registered additively.
- **Telegram Adapter** — transport layer, no file format dependency.
- **Shell Executor** — `/run` and `/batch` shell out to `run-iteration.sh`.
  The runner reads `.state/` instead of DEVPLAN frontmatter, but that's
  a runner change, not a codexbot change.
- **State Store (SQLite)** — bot operational state (sessions, threads,
  runs) is separate from project development state (`.state/`). No overlap.

### Codexbot sequencing

1. Finalize `.state/` format on a real i2c project
2. Build `StateReader` (replaces `LogReader` for i2c projects)
3. Update command handlers to use `StateReader`
4. Add new commands (`/progress`, `/cost`, `/timeline`)
5. Test on the pilot project

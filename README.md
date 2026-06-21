# i2c — Idea to Code

A governance framework for AI-driven software development. Replaces
markdown-parsed state with structured state on disk, eliminating the
impedance mismatch between deterministic code and prose-formatted state
that drove recurring bugs in the predecessor framework (e2e).

i2c projects keep state in `.state/*.json` files validated against
JSON Schema. State is read and written through a small CLI; humans and
agents share one source of truth. Worker agents (Claude, Codex) receive
all governance content pre-assembled in their prompt — they never read
governance files directly.

For the full design rationale, see
[`DESIGN_governance_v3.md`](DESIGN_governance_v3.md). For the workflow
diagrams, see [`WORKFLOW.md`](WORKFLOW.md).

---

## At a glance

```
Phase lifecycle:   plan → execute (×N) → review → close → [human gate]
State of truth:    .state/project.json + phases.json + steps.json + devlog.jsonl + decisions.json
Worker prompts:    pre-assembled (no governance reads by the worker)
Backends:          Claude or Codex (per-backend adapter; loop contract is universal)
Modes:             autonomous (loop runner) or supervised (human + assistant)
```

---

## File and directory layout

```
i2c/
├── README.md                       ← this file
├── FOLLOWUPS.md                    ← rolling backlog + cold-start summary (canonical project state)
│
├── DESIGN_governance_v3.md         ← original architectural rationale (D1–D21); state-model section superseded
├── DESIGN_state_lifecycle_v1.md    ← current state-model design (7-state enum); shipped 2026-06-08
├── ARCH_assembler.md               ← assembler contract (CLI surface, section catalog, error policy)
├── WORKFLOW.md                     ← actor topology + dispatch flow diagrams
├── FUTURE_waymark.md               ← roadmap for Waymark VS Code extension over i2c .state/
│
├── WORKER_SPEC.md                  ← universal worker loop contract (backend-agnostic)
├── CLAUDE.md                       ← Claude adapter template + tool rules
├── CODEX.md                        ← Codex adapter template + tool rules
│
├── instructions/                   ← per-action procedures (assembled into worker prompts)
│   ├── plan.md                       Identify regime, write step/phase/decision records,
│   │                                 conditional dep-probe + escalation triggers (step 2.5)
│   ├── execute.md                    Pick step, implement, test, commit, log
│   ├── review.md                     Must/Should/Optional categorization, apply fixes
│   └── close.md                      Phase-level tests, gotchas, integration check,
│                                     decision closure, set audit_boundary gate
│
├── ref/                            ← human-facing reference for collaborative authoring (not assembled)
│   ├── SPEC_architecture.md          ARCH-file templates: Pattern A + Pattern B + variants
│   └── GUIDE_architecture.md         Process walkthrough for architecture authoring sessions
│
├── schemas/                        ← JSON Schema for every state file
│   ├── project.schema.json           top-level state (phase, 7-state enum, gotchas, budget)
│   ├── phases.schema.json            array of phase records (regime, dependencies)
│   ├── steps.schema.json             array of step records (status enum, commit hash)
│   ├── devlog_entry.schema.json      per-line schema for devlog.jsonl
│   ├── decisions.schema.json         array of decision records (optional phase field)
│   └── exit_signal.schema.json       worker exit signal validation
│
├── tools/
│   ├── state.py                      atomic, schema-validated write CLI (+ --from-file flag)
│   ├── validate.py                   schema loader + validation helpers
│   ├── assemble_context.py           builds worker prompts and section snapshots
│   ├── state_machine.py              ACTION + NEXT computation (read-only)
│   ├── invariants.py                 post-action invariant checks
│   └── run_iteration.py              single-iteration autonomous runner
│
├── templates/                      ← slash-command wrappers for supervised mode
│   └── .claude/commands/             cold-start, phase-plan, phase-review, etc.
│
├── tests/                          ← unit tests (stdlib unittest, ~278 tests)
└── examples/
    ├── initial_state/                canonical fixture: a mid-phase project
    └── smoke_test.py                 end-to-end CLI walkthrough
```

Within a real project using i2c, additional files live at the project root:

```
<your project>/
├── .state/                         ← actual project state (git-tracked)
│   ├── project.json
│   ├── phases.json
│   ├── steps.json
│   ├── devlog.jsonl
│   └── decisions.json
├── PROJECT.md                      ← scope, constraints, success criteria
├── ARCHITECTURE.md                 ← component map, data flow, implementation sequence
├── ARCH_<module>.md                ← per-module interface contracts (Pattern A only;
│                                     Pattern B keeps everything in ARCHITECTURE.md)
├── CLAUDE.md / CODEX.md            ← copied from i2c, filled in
├── WORKER_SPEC.md                  ← copied or symlinked from i2c
├── instructions/                   ← copied or symlinked from i2c (worker procedures)
├── schemas/                        ← copied or symlinked from i2c
├── tools/                          ← copied or symlinked from i2c (state.py + validate.py
│                                     must be in sync; assemble_context.py canonical from i2c)
└── logs/loop/                      ← runner output (autonomous mode only; gitignored)
```

---

## State model

Every i2c project has a `.state/` directory holding five files. All are
git-tracked. Diffs of these files cleanly show state transitions.

| File | Shape | What it holds |
|------|-------|---------------|
| `project.json` | JSON object | Current phase number, lifecycle state (see below), gotchas, step or time budget |
| `phases.json` | Array of objects | One record per phase — id, module, title, regime (build/refine/explore), dependencies, status |
| `steps.json` | Array of objects | One record per step across all phases — (phase, step), title, status, commit hash |
| `devlog.jsonl` | One JSON object per line | Append-only history of every action's outcome |
| `decisions.json` | Array of objects | Decision records — id, title, status (open/closed/superseded), decision text, rationale |

Schemas are in [`schemas/`](schemas/). All writes go through
[`tools/state.py`](tools/state.py) for atomicity and validation; never
`sed`, `echo >`, or text editors.

### Lifecycle state values

`project.json.state` is the single variable that drives the state
machine. Seven values, all valid; see
[`DESIGN_state_lifecycle_v1.md`](DESIGN_state_lifecycle_v1.md) for the
full model.

| State | Meaning | Next dispatch | Recovery write (when halted) |
|-------|---------|---------------|------------------------------|
| `plan` | Next action is PLAN | PLAN | — |
| `execute` | Next action is EXECUTE | EXECUTE | — |
| `review` | Next action is REVIEW | REVIEW | — |
| `close` | Next action is CLOSE | CLOSE | — |
| `audit_boundary` | Phase done, human/wrapper decides next phase or terminus | EXIT | `set phase=N+1 state=plan` (advance) or `set state=done` (terminate) |
| `audit_escalation` | Worker hit an escalation (three strikes / contract drift / scope expansion); human required | EXIT | `set state=execute\|review\|...` (resume the running state after resolving the escalation) |
| `done` | Project terminal; no further dispatch | EXIT | `set phase=N+1 state=plan` (deliberate add-a-phase) |

CLOSE workers always transition to `audit_boundary` (conservative
closure — the human/wrapper, not the worker, decides whether the next
state is `plan` or `done`). EXECUTE and REVIEW workers transition to
`audit_escalation` on escalation per their instruction files.

### state.py operations

```bash
# Top-level keys on a JSON-object file (project.json)
python3 tools/state.py set project.json state=execute

# Advance to a new phase + transition to plan (one atomic write)
python3 tools/state.py set project.json phase=12 state=plan

# Mark a step or phase complete (matched by key)
python3 tools/state.py complete steps.json --phase 11 --step 3 --commit abc1234
python3 tools/state.py complete phases.json --phase 11

# Append one record to an array file (steps/phases/decisions)
python3 tools/state.py append-record steps.json '{"phase":11,"step":4,"title":"...","status":"pending"}'

# Update fields on one record in an array file (matched by single key=value)
python3 tools/state.py update-record decisions.json --match id=D-22 status=closed decision="..."

# Append a record to devlog.jsonl (JSONL)
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":3,...}'

# Append a string to project.json.gotchas
python3 tools/state.py append-gotcha project.json "fsync after every append"
```

Every operation validates the resulting file against its schema before
writing. Validation failure leaves the file untouched.

---

## The four worker actions

Each phase moves through four actions. The state machine dispatches them;
the worker performs them. Each has a single-purpose instruction file.

| Action | Trigger | What the worker does | Instruction file |
|--------|---------|----------------------|------------------|
| `PLAN` | `state == "plan"` | Identify the next phase, choose the regime (build/refine/explore), break work into steps (Build) or set a time budget (Refine/Explore), write the phase record and dependency-probe results if non-leaf | [`instructions/plan.md`](instructions/plan.md) |
| `EXECUTE` | `state == "execute"` and pending steps exist | Pick the next pending step, implement and test, commit, log to devlog, transition to review when last step done | [`instructions/execute.md`](instructions/execute.md) |
| `REVIEW` | `state == "review"` | Read all phase code, categorize findings as Must/Should/Optional, apply Must+Should, log skipped Optionals as decisions | [`instructions/review.md`](instructions/review.md) |
| `CLOSE` | `state == "close"` | Phase-level tests, integration check if non-leaf, gotcha promotion from devlog, contract propagation, decision closure, mark phase complete, set the human gate | [`instructions/close.md`](instructions/close.md) |

`EXIT` is also an action the state machine can emit (when there's nothing
to do or the budget is exhausted) — the worker doesn't perform it; it
just stops.

---

## Two execution modes

### Autonomous

The loop runner (`run-iteration.sh`, not yet built — Phase 3) invokes
the worker repeatedly. Before each invocation it:

1. Calls `state_machine.py` to determine the next ACTION
2. Calls `assemble_context.py` to build the full prompt (instructions,
   adapter, project state, project docs, devlog tail)
3. Pipes the prompt into `claude -p` or `codex exec`
4. Parses the worker's exit signal and writes `summary.log`

The worker reads zero governance files. It does its action, writes
outcomes via `state.py`, emits the exit signal, exits. The runner
re-invokes for the next action.

At phase close, the worker transitions to `state=audit_boundary` and the
loop halts. A human or autonomous wrapper audits, then writes one of:

- `python3 tools/state.py set project.json phase=N+1 state=plan` —
  advance to the next phase
- `python3 tools/state.py set project.json state=done` — declare the
  project terminal

and the loop resumes (or stops permanently on `done`).

### Supervised

A human works directly with an AI assistant (Claude in VS Code, Codex in
terminal). The same tools serve both modes — only the caller differs.

Supervised cold-start: `python3 tools/assemble_context.py --section status`.
Per-action context:
`python3 tools/assemble_context.py --action plan --phase N --mode supervised`.
The assembler strips `autonomous_only` framing (Output Contract,
Behavioral Rules, Next State) and reframes `Action: $TYPE` as
`Active Action: $TYPE` so the assistant pauses for human approval.

Mode is set by the runner's `--mode` flag; the assembled prompt's
framing reflects it (autonomous: act and commit; supervised: surface
proposed changes before committing). Both modes use the same `.state/`,
`state.py`, schemas, and instruction files.

---

## Work modes (for humans)

Each session a human runs operates in one mode at a time. These are
*workflow* modes, distinct from the *execution* modes above.

**1. Discuss (no code changes)**
- Determine scope, identify the regime, specify accordingly
- Prioritize simplest solutions; check if existing code can be reused
- Preserve existing architecture unless there's a clear reason to change
- If context is missing, ask before proceeding
- **Ends with** a `.state/` update — typically a new phase plan, or a
  decision record closing an open question

**2. Code / Debug**
- **Code:** implement the plan from the discuss session
- **Debug:** propose a testable hypothesis first, then make changes
- Switching between code and debug within a session is expected

**3. Review**
- Goal: improve existing code, not write new features
- Priority #1: preserve existing functionality
- Priority #2: simplify and reduce code
- Confirm architecture alignment (no drift from spec)

These map informally to the worker actions but are not the same thing.
Workers in PLAN mode are in Discuss; in EXECUTE in Code/Debug; in REVIEW
in Review. CLOSE doesn't have a clean human equivalent — it's mostly
housekeeping.

---

## Bootstrap a new i2c project

1. **Create the project directory** and initialize git.

2. **Copy (or symlink) the framework files** from `p:\shared\i2c\`:
   - `WORKER_SPEC.md`
   - `instructions/`
   - `schemas/`
   - `tools/`
   - `CLAUDE.md` and `CODEX.md` (as templates — fill in the placeholders)
   - `templates/.claude/commands/` — supervised-mode slash wrappers (see `templates/README.md`)

3. **Write the project docs**:
   - `PROJECT.md` — scope, constraints, success criteria
   - `ARCHITECTURE.md` — component map, data flow, implementation
     sequence
   - `ARCH_<module>.md` for each module that needs a contract

4. **Initialize `.state/`** by hand. Smallest viable starting state:

   ```bash
   mkdir .state
   echo '{"phase": 0, "state": "plan", "gotchas": []}' > .state/project.json
   echo '[]' > .state/phases.json
   echo '[]' > .state/steps.json
   echo '[]' > .state/decisions.json
   touch .state/devlog.jsonl
   ```

5. **Run the smoke test** against the fixture to confirm the toolchain
   works in your environment:
   ```bash
   python3 examples/smoke_test.py
   ```

6. **Begin Phase 1** by running
   `python3 tools/assemble_context.py --action plan --phase 1 --mode supervised`
   and following the procedure in the assembled `Instructions` section.

For a project that wants to run autonomously rather than supervised,
substitute step 6 with `python3 ../i2c/tools/run_iteration.py --backend
claude --max-budget-usd 5.00` (and see the Invocation guidance in
`FOLLOWUPS.md` about laptop-vs-server constraints).

---

## Build status

The framework is functionally complete for single-iteration autonomous
runs. CC (clankercourts) is the first real consumer; it has run Phases
2–14 end-to-end autonomously across both Claude and Codex backends.
Codexbot integration MVP shipped 2026-06-09 (Telegram surface for i2c
projects). Remaining work is incremental — multi-iteration loop,
FU-32 Δ5 (PLAN precondition check), and the deferred codexbot commands
(`/decisions`, `/escalation`, `/logs`, `/review`) blocked on FU-34.

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1.1 | Schemas, `state.py`, `validate.py`, tests, fixture, smoke test | ✅ |
| 1.2 | Instruction files (plan, execute, review, close), WORKER_SPEC, adapter templates | ✅ |
| 1.2.5 | `ARCH_assembler.md` contract spec | ✅ |
| 1.2.6 | `templates/.claude/commands/` slash wrappers | ✅ |
| 1.3 | `assemble_context.py` — context assembler | ✅ |
| 2 | Clankercourts pilot (Phases 1–14 shipped; Phase 1 supervised, Phases 2–14 autonomous) | ✅ |
| 3.A | `state_machine.py`, `run_iteration.py`, `invariants.py` — single-iteration autonomous loop | ✅ |
| 3.A.1 | Prompt compaction + region reorder (Worker Contract → Tool Rules → Project Context → Action Context) | ✅ |
| 3.A.2 | EXECUTE recipe trim (drop project-wide decisions table) | ✅ |
| 3.B | First real autonomous run (CC Phases 2–14 on Claude + Codex) | ✅ |
| 3.D | Codex backend (`--backend codex` in runner) | ✅ |
| State lifecycle v1 | 7-state enum replaces `(state, blocked)` overload (FU-30) | ✅ |
| FU-32 Δ1, Δ2, Δ4 + phase-summary | autonomous-PLAN readiness work | ✅ |
| FU-32 Δ4 v2 | Pattern A/B template collapse + worked examples (lyonel, noise-machine, PoP_port) | ✅ |
| Codexbot Phase 18a MVP | Telegram surface for i2c projects (`/start`, `/run`, `/close`, `/audit`) | ✅ |
| FU-33 | Per-iter token telemetry (`tokens_in/out/cached`) in `summary.log` for both backends | ✅ |
| FU-7 | Exit signal trimmed to 2-line block (`exit_code` + `reason`); schema strict | ✅ |
| 3.C | Multi-iteration loop (`--step-budget > 1`) | upcoming |
| FU-32 Δ5 | PLAN precondition check on ARCH completeness | deferred (after Pattern A/B template proves out further) |
| Codexbot 18a remainder | `/decisions`, `/escalation`, `/logs`, `/review` for i2c | deferred (blocked on FU-34) |
| 5 | (Optional) Diplomat migration from e2e | deferred |

### What works today

- All `.state/` operations through `state.py` with atomic writes, schema
  validation, and `--from-file` payload paths (FU-21 closed). Adapter
  Tool Rules in CLAUDE.md / CODEX.md recommend `--from-file` for any
  payload with `$` characters or newlines (FU-12 closed 2026-06-09).
- Schema validation on every write; `validate.py` re-validates any
  existing file. Optional `phase: integer` field on decision records
  (FU-32 Δ1) lets phase-summary filter cleanly; instruction examples
  in `plan.md` and `review.md` tell workers to include it.
- Worker prompt assembly through `assemble_context.py` — full per-action
  prompts, status snapshots (`--section status`), phase-boundary audit
  view (`--section phase-summary --phase N`), and mid-step single-section
  requests (`--section {architecture,module,devlog}`). Conditional
  sections strip deterministically per `ARCH_assembler.md` §7.
- Single-iteration autonomous loop: `python tools/run_iteration.py
  [--backend claude|codex] [--model sonnet] [--max-budget-usd 5.00]`
  dispatches one worker invocation with state-machine + invariants
  checking. Both backends proven in production on CC Phases 2–14. Per-iter
  token usage (`tokens_in / tokens_out / tokens_cached`) is appended to
  `summary.log` for both backends (FU-33).
- **Telegram control surface for i2c projects** via codexbot: `/start`,
  `/run N`, `/run N to-review`, `/batch N to-review`, `/close`, `/audit`.
  `/close` clears `audit_boundary` by advancing `phase=N+1 state=plan`
  atomically; `/audit` renders the assembler's phase-summary. Restart
  the service per `~/claude-code-workspace/projects/pirozhok/README.md`.
- ARCH-file authoring template at `ref/SPEC_architecture.md` +
  `ref/GUIDE_architecture.md` (Pattern A: per-module ARCH files;
  Pattern B: single-document architecture with optional Layer
  Contracts). Worked examples cover both patterns including lyonel,
  noise-machine, and PoP_port from the operator's prior work.
- Cross-platform (Windows / PowerShell + Python 3.12 for supervised
  use; Linux server via SSH-into-container for autonomous loops, since
  Meta-issued laptops can't host the subprocess chain — FU-28 wontfix).

### What doesn't work yet

- **No multi-iteration loop.** Runner hard-codes `--step-budget 1`;
  one worker invocation = one ACTION. Phase 3.C wraps it with a
  multi-step driver. The `multi_step_only` marker mechanism in
  WORKER_SPEC and instructions is already forward-compatible.
- **Codexbot's `/decisions`, `/escalation`, `/logs`, `/review`** don't
  branch for i2c yet (currently e2e-only). Blocked on FU-34 upstream
  (`--section escalation`, `--section iteration`).
- **No autonomous-capable PLAN-precondition enforcement.** Today the
  worker reads the ARCH template's Required sections by prose discipline.
  FU-32 Δ5 would make absence of `## Phasing in This Pilot` or
  `## Escalation Triggers` a hard halt at PLAN time. Deferred until
  more ARCH files are authored under the v2 template.

For the rolling state-of-the-project view (what's pending, what's been
shipped recently, what the next steps are), see the cold-start summary
at the top of [`FOLLOWUPS.md`](FOLLOWUPS.md).

---

## Where to look next

- **Current state and active priorities:**
  [`FOLLOWUPS.md`](FOLLOWUPS.md) — cold-start summary up top, then the
  rolling backlog (open / partially-closed / closed FU entries).
  Single canonical source for "where are we right now."
- **Architectural rationale and locked decisions:**
  [`DESIGN_governance_v3.md`](DESIGN_governance_v3.md) — original
  design doc with D1–D21; state-model section is superseded by
  `DESIGN_state_lifecycle_v1.md` (banner at top of the file notes
  this).
- **State lifecycle (current model):**
  [`DESIGN_state_lifecycle_v1.md`](DESIGN_state_lifecycle_v1.md) —
  7-state enum that replaced `(state, blocked)`. Shipped 2026-06-08.
- **Assembler contract:**
  [`ARCH_assembler.md`](ARCH_assembler.md) — authoritative CLI
  surface, section catalog (including `--section phase-summary` §8b),
  assembly matrix, error policy.
- **ARCH-file authoring template:**
  [`ref/SPEC_architecture.md`](ref/SPEC_architecture.md) +
  [`ref/GUIDE_architecture.md`](ref/GUIDE_architecture.md) — Pattern A
  (per-module ARCH files) and Pattern B (single-document
  architecture) with worked examples.
- **Visual workflow:** [`WORKFLOW.md`](WORKFLOW.md) — actor topology,
  dispatch paths, action map.
- **Waymark refit roadmap:**
  [`FUTURE_waymark.md`](FUTURE_waymark.md) — VS Code extension over
  i2c `.state/`, deferred until pulled forward.
- **Source-of-truth for replaced material:** the predecessor framework
  lives at `p:\shared\e2e\` (DEVPLAN-frontmatter, DEVLOG-markdown,
  COMMANDS/*.md). i2c is a clean break, not a migration — existing
  e2e projects continue using e2e.

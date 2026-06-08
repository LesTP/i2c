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
├── DESIGN_governance_v3.md         ← architectural rationale; the "why"
├── WORKFLOW.md                     ← actor topology + invocation flow diagrams
├── FOLLOWUPS.md                    ← rolling backlog of design notes / tooling gaps
├── FUTURE_waymark.md               ← roadmap for refitting waymark on i2c
│
├── WORKER_SPEC.md                  ← universal worker loop contract (backend-agnostic)
├── CLAUDE.md                       ← per-project adapter template — Claude tool rules
├── CODEX.md                        ← per-project adapter template — Codex tool rules
│
├── instructions/                   ← per-action procedures (assembled into worker prompts)
│   ├── plan.md                       Identify regime, write step/phase/decision records,
│   │                                 conditional dependency-probe for non-leaf modules
│   ├── execute.md                    Pick step, implement, test, commit, log
│   ├── review.md                     Must/Should/Optional categorization, apply fixes
│   └── close.md                      Phase-level tests, gotchas, conditional integration
│                                     check, decision closure, set the human gate
│
├── schemas/                        ← JSON Schema for every state file
│   ├── project.schema.json           top-level state (phase, state enum, gotchas, budget)
│   ├── phases.schema.json            array of phase records (regime, dependencies)
│   ├── steps.schema.json             array of step records (status enum, commit hash)
│   ├── devlog_entry.schema.json      per-line schema for devlog.jsonl
│   ├── decisions.schema.json         array of decision records
│   └── exit_signal.schema.json       worker exit signal validation
│
├── tools/
│   ├── state.py                      atomic, schema-validated write CLI
│   ├── validate.py                   schema loader + validation helpers
│   └── assemble_context.py           builds worker prompts and section snapshots
│
├── tests/                          ← unit tests (stdlib unittest, 78+ tests)
└── examples/
    ├── initial_state/                canonical fixture: a mid-phase project
    └── smoke_test.py                 end-to-end CLI walkthrough
```

Within a real project using i2c, additional files live at the project root:

```
<your project>/
├── .state/                         ← actual project state (gitignored? no — git-tracked)
│   ├── project.json
│   ├── phases.json
│   ├── steps.json
│   ├── devlog.jsonl
│   └── decisions.json
├── PROJECT.md                      ← scope, constraints, success criteria
├── ARCHITECTURE.md                 ← component map, data flow
├── ARCH_<module>.md                ← per-module interface contracts
├── CLAUDE.md / CODEX.md            ← copied from i2c, filled in
├── WORKER_SPEC.md                  ← copied or symlinked from i2c
├── instructions/                   ← copied or symlinked from i2c
├── schemas/                        ← copied or symlinked from i2c
├── tools/                          ← copied or symlinked from i2c
└── logs/loop/                      ← runner output (autonomous mode only)
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

1. Calls `state_machine.sh` to determine the next ACTION
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

The framework is currently **Phase 1 complete (data foundation, prose
layer, and assembler) and Phase 2 in progress (clankercourts bootstrap
complete; first PLAN action upcoming). Phase 3 (autonomous loop tooling)
not yet built.** See the next section for what works today.

---

## Build status

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1.1 | Schemas, `state.py`, `validate.py`, tests, fixture, smoke test | ✅ |
| 1.2 | Instruction files (plan, execute, review, close), WORKER_SPEC, adapter templates | ✅ |
| 1.2.5 | `ARCH_assembler.md` contract spec | ✅ |
| 1.2.6 | `templates/.claude/commands/` slash wrappers | ✅ |
| 1.3 | `assemble_context.py` - context assembler | ✅ |
| 2 | Clankercourts pilot in supervised mode | in progress |
| 3 | `state_machine.sh`, `run-iteration.sh` updates — autonomous loop | upcoming |
| 4 | Codexbot StateReader + dispatcher (Telegram or Discord) | upcoming |
| 5 | (Optional) Diplomat migration from e2e | deferred |

### What works today

- All `.state/` operations through `state.py` (writes, validation,
  atomic guarantees)
- Schema validation on every write; `validate.py` can also re-validate
  any existing file
- **Worker prompt assembly** through `assemble_context.py` — full
  per-action prompts (`--action {plan,execute,review,close}` with
  `--mode {autonomous,supervised}`), status snapshots
  (`--section status`), and mid-step single-section requests
  (`--section {architecture,module,devlog}`). Conditional sections
  (dependency probe, integration check, autonomous-only paragraphs)
  strip deterministically per ARCH_assembler.md §7.
- Supervised use with humans/assistants reading the assembler output
  (the slash wrappers in `templates/.claude/commands/` are now
  functional end-to-end)
- The end-to-end smoke test exercises every CLI subcommand on a realistic
  fixture
- Cross-platform (tested on Windows / PowerShell + Python 3.12)

### What doesn't work yet

- **No autonomous loop.** `state_machine.sh` and `run-iteration.sh`
  updates are Phase 3.
- **No remote dispatch.** Codexbot integration is Phase 4. No
  Telegram/Discord control surface yet.

---

## Where to look next

- **Architectural rationale and decisions:**
  [`DESIGN_governance_v3.md`](DESIGN_governance_v3.md) - the design doc
  with all locked decisions (D1-D21).
- **Assembler contract:** [`ARCH_assembler.md`](ARCH_assembler.md) - the
  authoritative CLI surface, section catalog, assembly matrix, and error
  policy for `assemble_context.py`. Phase 1.3 implements against this.
- **Visual workflow:** [`WORKFLOW.md`](WORKFLOW.md) - actor topology,
  invocation flow, action map, supervised mode diagram.
- **Outstanding gaps and design notes:**
  [`FOLLOWUPS.md`](FOLLOWUPS.md) — rolling backlog with explicit
  triggers for when to act on each item.
- **Waymark refit roadmap:**
  [`FUTURE_waymark.md`](FUTURE_waymark.md) — VS Code extension over i2c
  `.state/`, deferred until i2c is built and piloted.
- **Source-of-truth source for replaced material:** the predecessor
  framework lives at `p:\shared\e2e\` (DEVPLAN-frontmatter,
  DEVLOG-markdown, COMMANDS/*.md). i2c is a clean break, not a
  migration — existing e2e projects continue using e2e.

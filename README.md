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

Active development: a multi-iteration loop, a pluggable backend interface
(so other providers can be added), and packaging as an installable library.
Today you use i2c by copying its framework files into your project (see
[Bootstrap](#bootstrap-a-new-i2c-project)); `pip install` is on the roadmap.

---

## Requirements

- **Python 3.10+** with [`jsonschema`](https://pypi.org/project/jsonschema/)
  (the only runtime dependency). Tests use the standard-library `unittest` —
  no extra test dependencies.
- **For autonomous runs:** a backend CLI on your `PATH` — the `claude` CLI
  (Claude Code) and/or the `codex` CLI — with the relevant provider API
  credentials configured in your environment.
- **For supervised runs:** any capable coding assistant that can run the
  `tools/*.py` commands; no backend CLI required.

---

## How it works

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

Schemas live in [`schemas/`](schemas/). All writes go through
[`tools/state.py`](tools/state.py) for atomicity and schema validation —
never `sed`, `echo >`, or a text editor. Every write validates the resulting
file against its schema first; a validation failure leaves the file untouched.

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

### `state.py` operations

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

For payloads containing `$` or newlines, pass `--from-file <path>` instead of
an inline string to avoid shell-quoting hazards.

---

## Two execution modes

### Autonomous

The loop runner ([`tools/run_iteration.py`](tools/run_iteration.py)) invokes
the worker once per ACTION. Before each invocation it:

1. Calls `state_machine.py` to determine the next ACTION.
2. Calls `assemble_context.py` to build the full prompt (instructions,
   adapter, project state, project docs, devlog tail).
3. Pipes the prompt into `claude -p` or `codex exec`.
4. Parses the worker's two-line exit signal and writes `summary.log`.

The worker reads zero governance files. It performs its action, writes
outcomes via `state.py`, emits the exit signal, and exits. The runner
re-invokes for the next action. (A multi-iteration driver — one invocation
covering several steps — is on the roadmap.)

At phase close the worker transitions to `state=audit_boundary` and the loop
halts. A human audits, then writes one of:

- `python3 tools/state.py set project.json phase=N+1 state=plan` — advance to
  the next phase, or
- `python3 tools/state.py set project.json state=done` — declare the project
  terminal,

and the loop resumes (or stops permanently on `done`).

### Supervised

A human works directly with an AI assistant. The same tools serve both modes —
only the caller differs.

```bash
# Cold-start orientation
python3 tools/assemble_context.py --section status

# Per-action context, framed for human approval
python3 tools/assemble_context.py --action plan --phase N --mode supervised
```

Under `--mode supervised` the assembler strips `autonomous_only` framing
(Output Contract, Behavioral Rules, Next State) and reframes `Action: $TYPE`
as `Active Action: $TYPE` so the assistant pauses for human approval before
committing. Both modes use the same `.state/`, `state.py`, schemas, and
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

## Bootstrap a new i2c project

1. **Create the project directory** and initialize git.

2. **Copy (or symlink) the framework files** from this repository:
   - `WORKER_SPEC.md`
   - `instructions/`
   - `schemas/`
   - `tools/`
   - `CLAUDE.md` and `CODEX.md` (as templates — fill in the placeholders)
   - `templates/.claude/commands/` — supervised-mode slash wrappers

3. **Write the project docs:**
   - `PROJECT.md` — scope, constraints, success criteria
   - `ARCHITECTURE.md` — component map, data flow, implementation sequence
   - `ARCH_<module>.md` for each module that needs a contract (see
     [`ref/SPEC_architecture.md`](ref/SPEC_architecture.md))

4. **Initialize `.state/`** by hand. Smallest viable starting state:

   ```bash
   mkdir .state
   echo '{"phase": 0, "state": "plan", "gotchas": []}' > .state/project.json
   echo '[]' > .state/phases.json
   echo '[]' > .state/steps.json
   echo '[]' > .state/decisions.json
   touch .state/devlog.jsonl
   ```

5. **Run the smoke test** to confirm the toolchain works in your environment:

   ```bash
   python3 examples/smoke_test.py
   ```

6. **Begin Phase 1** in supervised mode:

   ```bash
   python3 tools/assemble_context.py --action plan --phase 1 --mode supervised
   ```

   and follow the procedure in the assembled `Instructions` section.

To run autonomously instead, drive the loop with
`python3 tools/run_iteration.py --backend claude --max-budget-usd 5.00` from
the project root.

> A consumer project carries its own copy of the framework files today, which
> must stay in sync with this repository. Eliminating that copy-and-sync step
> by shipping i2c as an installable package is the main item on the roadmap.

---

## Repository layout

```
i2c/
├── README.md                     ← this file
├── LICENSE                       ← MIT
├── WORKER_SPEC.md                ← universal worker loop contract (backend-agnostic)
├── CLAUDE.md / CODEX.md          ← per-backend adapter templates + tool rules
│
├── instructions/                 ← per-action procedures (assembled into worker prompts)
│   ├── plan.md  execute.md  review.md  close.md
│
├── ref/                          ← human-facing reference for ARCH-file authoring
│   ├── SPEC_architecture.md      Pattern A / Pattern B templates
│   └── GUIDE_architecture.md     process walkthrough
│
├── schemas/                      ← JSON Schema for every state file
├── tools/
│   ├── state.py                  atomic, schema-validated write CLI (--from-file flag)
│   ├── validate.py               schema loader + validation helpers
│   ├── assemble_context.py       builds worker prompts and section snapshots
│   ├── state_machine.py          ACTION + NEXT computation (read-only)
│   ├── invariants.py             post-action invariant checks
│   └── run_iteration.py          single-iteration autonomous runner
│
├── templates/.claude/commands/   ← slash-command wrappers for supervised mode
├── tests/                        ← unit tests (stdlib unittest)
└── examples/
    ├── initial_state/            canonical fixture: a mid-phase project
    └── smoke_test.py             end-to-end CLI walkthrough
```

A project *using* i2c keeps its own `.state/`, `PROJECT.md`, `ARCHITECTURE.md`,
`ARCH_<module>.md`, filled-in adapters, and (today) a synced copy of the
framework files; `logs/loop/` holds runner output in autonomous mode and is
gitignored.

---

## Reference docs

- [`ARCH_assembler.md`](ARCH_assembler.md) — the context assembler's contract:
  CLI surface, section catalog, assembly matrix, error policy.
- [`ref/SPEC_architecture.md`](ref/SPEC_architecture.md) +
  [`ref/GUIDE_architecture.md`](ref/GUIDE_architecture.md) — how to author the
  ARCH files that drive planning.
- [`WORKFLOW.md`](WORKFLOW.md) — actor topology and dispatch-flow diagrams.

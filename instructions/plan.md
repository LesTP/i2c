# Plan — Set Up the Next Phase

Defines the work for the upcoming phase: identifies regime, records the phase
metadata, breaks the work into the right shape for that regime, and writes
the state that lets EXECUTE start. The state machine has already decided
this action is appropriate; do not re-decide.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: PLAN`. Pair this procedure with the `Worker Contract` section
already included in your prompt for the loop/escalation/output rules, and
with the conventions in `instructions/execute.md` for commit and devlog
shape (this file inherits those).

---

## Procedure

### 1. Identify the phase being planned

Read `project.json.phase` from the assembled `Project State` section. That is
the phase number you are planning. (If you arrived from `audit_boundary`,
the human/wrapper just set `phase=N+1` atomically with `state=plan` per the
boundary-clear transition documented in `instructions/close.md` step 12.)

Then look at the assembled `Phases` section (sourced from
`.state/phases.json`):

- **No record exists for this phase number** → this is a new phase. You will
  create the phase record (with `status: "pending"`) in step 4. The assembler
  rendered a stub phase heading for this case; see
  `DESIGN_state_lifecycle_v1.md` §6.4.
- **Record exists with `status: "pending"`** → the phase was outlined
  upstream (typically by a prior CLOSE action that scheduled future phases).
  Leave it as-is; you may refine `dependencies`, `regime`, or `title` via
  `update-record` if needed.
- **Record exists with `status: "complete"`** → the phase is already closed.
  PLAN was mis-dispatched against a completed phase. **Escalate**: set
  `state=audit_escalation` and emit `EXIT 2` (reason "plan called on
  completed phase"); the human/wrapper likely forgot to advance
  `project.json.phase` when clearing `audit_boundary`.

### 2. Determine scope and outcomes

Read the assembled `Project Scope` section (PROJECT.md), the
`Architecture` section (ARCHITECTURE.md), and the `Module Contract` section
(`ARCH_<module>.md` for the phase's module). State the phase's scope and
specific outcomes in your own words before continuing. This is a "Discuss"
step — no state writes yet, just reasoning.

If scope is unclear or contradictory, **stop and escalate** (`EXIT 2`,
reason "unclear spec"). Do not invent scope.

### 2.5. Escalation triggers — when else to halt PLAN

Beyond "unclear spec" (step 2) and "plan called on completed phase"
(step 1), the following conditions warrant immediate escalation. If you
detect any during the rest of this procedure, stop, write a devlog
entry naming the trigger, set `state=audit_escalation`, and emit
`EXIT 2` with a `REASON` matching the trigger. The human/wrapper
resolves before the loop resumes.

**Project-general triggers (apply to every PLAN run):**

| Trigger | Detect when | Reason string | Resolution path |
|---------|-------------|---------------|-----------------|
| Source-vs-ARCH drift | The canonical source (rules doc, external spec, sibling project ARCH, upstream API) contradicts what `ARCH_<module>.md` claims. | `"source-arch drift: <where>"` | Operator reconciles source vs. ARCH; commits corrected ARCH; re-plans. |
| Multi-regime scope | Phase as scoped mixes regimes (Build + Refine, Build + Explore, etc.). | `"multi-regime scope: split needed"` | Operator splits into sequential single-regime phases; re-plans phase N. |
| Cross-module breakage at plan time | This phase as scoped would change a contract that another *built* module already depends on. `close.md` step 8 handles this at close time; surfacing it at plan time saves the rewind. | `"cross-module breakage: <module>"` | Operator picks: (a) defer this phase, (b) plan a contract-update phase for the affected module first, or (c) restrict scope so the contract is untouched. |
| Step-shape ambiguity (Build only) | ARCH's `## Phasing in This Pilot` lacks discrete steps, or multiple equally-good decompositions exist with no objective tiebreaker. | `"step-shape ambiguity"` | Collaborative ARCH-authoring session to refine Phasing per `ref/SPEC_architecture.md` + `ref/GUIDE_architecture.md`. |
| Dep-probe contract mismatch (non-leaf only) | Step 5 dep probe surfaces a Mismatch on a critical dependency interface — not a minor signature drift the plan can absorb as one extra step. | `"dep probe: <module> contract mismatch"` | Operator picks: (a) update ARCH and re-plan, (b) adapt the fake first, or (c) defer the phase. |

**Module-specific triggers.** Your assembled `Module Contract` section
may include a `## Escalation Triggers` list — module-specific
conditions that augment the table above (e.g., "halt if v9 §6 source
contradicts the rule mapping below" for CC's validator). Apply those
alongside the project-general triggers.

If the assembled Module Contract has no `## Escalation Triggers`
section at all, that is itself a yellow flag — the ARCH may not be
authored under the autonomous-PLAN-ready template
(`ref/SPEC_architecture.md`). Continue planning but note the gap in
your plan devlog summary; FU-32 Δ5 (deferred) will turn the absence
into a hard precondition check.

**How to escalate:**

```bash
python3 tools/state.py append devlog.jsonl '{
  "phase": 11, "step": null, "action": "plan", "outcome": "blocked",
  "summary": "Escalating: <trigger>. <what you observed>. <what unblocks>.",
  "contracts": [], "timestamp": "2026-06-04T07:30:00Z"
}'
python3 tools/state.py set project.json state=audit_escalation
# Emit exit signal: EXIT 2; REASON matches the trigger.
```

Do not pre-emptively escalate. Triggers fire on **observed**
conditions, not suspicions. If unsure whether one applies, continue
planning — the dep probe (step 5) or scope analysis (step 2) will
surface most real issues concretely.

### 3. Identify the work regime

Ask: *Can the implementer verify this is correct without showing it to a
human?*

| Regime | Criterion | Budget mechanism |
|--------|-----------|------------------|
| **Build** | Yes — tests, type checks, objective criteria settle correctness | Step count (`project.json.steps_remaining`) |
| **Refine** | No — perception, taste, or human judgment settles correctness | Wall-clock time (`project.json.time_budget_seconds`) |
| **Explore** | The output is a decision, not shipping code | Wall-clock time, with an explicit decision as exit condition |

Common examples:
- Data models, parsers, integration wiring, build config → **Build**
- Visual design, copy, interaction feel, layout → **Refine**
- Technology selection, architecture alternatives → **Explore**

A feature can pass through multiple regimes across its lifetime (Explore →
Build → Refine). Plan one phase, one regime. If you find yourself wanting to
mix regimes in one phase, split it.

### 4. Write or update the phase record

If the phase record does not yet exist in `phases.json`, append it:

```bash
python3 tools/state.py append-record phases.json '{
  "id": 11,
  "module": "orchestrator",
  "title": "Pipeline + event loop",
  "regime": "build",
  "dependencies": ["event_store"],
  "status": "pending"
}'
```

Required fields: `id`, `title`, `regime`, `status`. Recommended: `module`,
`dependencies`. See `schemas/phases.schema.json` for the full surface.

`dependencies` lists other module names this phase's module reads or calls.
Use an empty array for leaf modules. Non-empty `dependencies` triggers the
Pre-plan Dependency Probe section below (if assembled into your prompt) and
the Pre-close Integration Check in `instructions/close.md`.

If the record already exists as `pending`, leave it as-is. Phase status is
binary (`pending` until `complete`); the active phase is identified by
`project.json.phase`, not by an in-flight status field. If you need to amend
`dependencies` / `regime` / `title` on the existing record, use
`state.py update-record phases.json --match id=N ...`.

### 5. *(Conditional)* Pre-plan: Dependency Probe - non-leaf modules only
<!-- assembler:requires=dependencies_nonempty -->

**Include this step only when** the current phase's `dependencies` array in
`phases.json` is non-empty. If the assembler has stripped this section from
your prompt, skip ahead.

A "dependency" here is anything the worker cannot modify: third-party
libraries, sibling project libraries (e.g., `toolkit/`), external APIs,
filesystems with non-standard semantics. **The test:** does the autonomous
agent control both sides of the interface? If no, it's external.

Procedure:

1. **Inventory.** For each name in `dependencies`, identify the concrete
   surface used by this module: which functions, types, files. Read the
   dependency's `ARCH_*.md` if it's a sibling project; read source headers /
   type definitions for libraries.
2. **Identify fake assumptions.** Find the corresponding fake/stub/mock in
   the test suite. State what the fake assumes: method signatures, return
   shapes, error modes, data formats, initialization requirements.
3. **Probe or spec.** For each dependency:
   - *Real dependency available:* run a minimal real-input invocation,
     compare against the fake. Classify each as **Match**, **Mismatch**, or
     **Surprise** (real behavior the fake doesn't model — rate limits,
     wrapping, content filters, etc.).
   - *Real dependency unavailable:* produce a probe spec — what to call,
     with what inputs, what to assert. The spec must be executable by a
     later session that has access.
4. **Report via devlog.** One entry per probe run. Use `action: "probe"` and
   set `step: null` (probes are phase-level, not step-bound):

   ```bash
   python3 tools/state.py append devlog.jsonl '{
     "phase": 11,
     "step": null,
     "action": "probe",
     "outcome": "complete",
     "summary": "Probed event_store.append: 2 matches, 1 mismatch (idempotency_key kwarg not in fake), 0 unknowns.",
     "contracts": [],
     "timestamp": "2026-06-04T07:30:00Z"
   }'
   ```

   `outcome` choices for probes: `complete` (probe ran, results recorded),
   `partial` (some dependencies probed, others left as spec), `blocked`
   (no real dependency available for the most critical surface — log a
   spec and exit), `failed` (probe execution errored — investigate).

5. **Do not fix mismatches in this step.** Report them. The fix is either a
   step in the upcoming plan (if straightforward) or a decision and
   contract change (if not). Mismatches surface as **risks** the plan must
   account for, not as silent bug fixes.

When the probe finds critical mismatches that change the phase's scope:
revise step 2 (scope) before continuing to step 6 below.

### 6. Specify the work for the chosen regime

#### 6a. Build regime

Break the phase into the smallest testable steps. Each step:

- Has one clear outcome (a tested change, not a "and also").
- Is small enough that a worker can complete it in a single
  EXECUTE invocation under normal budget.
- Has at least one test specification (you don't have to write the tests
  yet, but you should know what they verify).

Write one record per step, in order:

```bash
python3 tools/state.py append-record steps.json '{
  "phase": 11,
  "step": 1,
  "title": "Wire pipeline topology",
  "status": "pending",
  "notes": "Construct the DI graph; no behavior yet. Tests verify the wiring shape."
}'
python3 tools/state.py append-record steps.json '{
  "phase": 11,
  "step": 2,
  "title": "Event loop with debounced extraction",
  "status": "pending"
}'
# ...one append-record per step...
```

Step numbering: sequential within the phase, starting at 1. Do not reuse
step numbers across phases — `(phase, step)` is the natural key.

Set `project.json.budget_type=steps` if it isn't already:

```bash
python3 tools/state.py set project.json budget_type=steps
```

`steps_remaining` is managed by the state machine; you do not set it
directly from PLAN.

#### 6b. Refine regime

Do **not** pre-list steps. Refine work emerges iteratively from
show → react → adjust cycles. Plan a **time budget**, not a step count.

1. State the goal in the phase record's `title` (already written in
   step 4) and add a longer-form goal description as a decision:

   ```bash
   python3 tools/state.py append-record decisions.json '{
     "id": "D-14",
     "title": "Phase 14 goal: telegram message formatting",
     "status": "closed",
     "priority": "high",
     "decision": "MarkdownV2 escaping handles all edge cases observed in the past week of group activity; messages render correctly across iOS, Android, and Web clients.",
     "rationale": "Recurring formatting bugs in production. Refine because correctness is perceptual."
   }'
   ```

   Use `status: "closed"` for goal statements — they are immediate decisions
   already made. Reserve `status: "open"` for choices the phase will resolve.

2. Set the time budget on `project.json`:

   ```bash
   python3 tools/state.py set project.json budget_type=time time_budget_seconds=10800 time_started_at=2026-06-04T07:30:00Z
   ```

   Choose `time_budget_seconds` based on the phase's perceived size.
   Reasonable bounds for a Refine phase: 1–8 hours of focused work (3600s
   to 28800s). The state machine compares wall-clock elapsed against this.

3. Identify the **first item to show**: the smallest concrete output that
   exemplifies the goal. Record it as a decision with `status: "open"`:

   ```bash
   python3 tools/state.py append-record decisions.json '{
     "id": "D-15",
     "title": "First item: render a 3-paragraph workflow update",
     "status": "open",
     "priority": "high",
     "decision": "First Refine iteration will produce a 3-paragraph workflow update with code fences, bold, and a bullet list; show to operator; collect reactions.",
     "rationale": "Smallest representative input that exercises the markdown surface."
   }'
   ```

Do not write step records. EXECUTE under Refine regime works from the goal
and the current state of the phase, not from a pre-listed step list.

#### 6c. Explore regime

The goal is a closed decision, not shipping code. Plan a **time box** and
the decision the phase must produce.

1. Write the decision record with `status: "open"`:

   ```bash
   python3 tools/state.py append-record decisions.json '{
     "id": "D-16",
     "title": "Storage backend for clankercourts events",
     "status": "open",
     "priority": "critical",
     "decision": "TBD — one of: SQLite local file, JSONL append-only files, embedded LMDB.",
     "rationale": "Need crash-safety + ordered iteration + low ops burden.",
     "revisit_if": "Volume exceeds 10k events / day."
   }'
   ```

   `decision` may be `"TBD"` for an open Explore; the closing iteration
   will rewrite it.

2. Set the time box on `project.json`:

   ```bash
   python3 tools/state.py set project.json budget_type=time time_budget_seconds=7200 time_started_at=2026-06-04T07:30:00Z
   ```

   Explore time boxes are typically tighter than Refine: 30 minutes to
   4 hours (1800s to 14400s). The point is a decision, not exhaustive
   exploration.

3. Identify the alternatives the phase will compare. Optionally record one
   step per alternative in `steps.json` so EXECUTE has a concrete loop to
   follow, but this is optional — Explore EXECUTE may also work
   open-endedly toward the decision.

### 7. Log non-trivial scope decisions

Any decision made during planning that you would want a cold-start session
to know about → append to `decisions.json`. Examples:

- "Chose Build over Refine because tests can verify message ordering."
- "Deferring the OAuth flow to a later phase — current phase covers only
  the local token path."
- "Renamed module field from `agent` to `worker` to align with
  ARCHITECTURE.md."

```bash
python3 tools/state.py append-record decisions.json '{
  "id": "D-17",
  "title": "Phase 11 split: pipeline vs. control loop",
  "status": "closed",
  "priority": "medium",
  "decision": "Phase 11 covers pipeline construction; control-loop semantics deferred to phase 12.",
  "rationale": "Pipeline is testable in isolation; control-loop needs the pipeline to exist before its tests make sense."
}'
```

Trivial decisions (file naming, comment style, formatter choice) do not
need a decision record. Use judgment.

### 8. Append a devlog entry for the plan action

One entry per PLAN invocation. `action: "plan"`, `step: null` (plans are
phase-level), `outcome: "complete"` for a finished plan:

```bash
python3 tools/state.py append devlog.jsonl '{
  "phase": 11,
  "step": null,
  "action": "plan",
  "outcome": "complete",
  "summary": "Phase 11 (orchestrator, Build): 4 steps covering pipeline, event loop, slash commands, E2E test. Dependency probe on event_store: 2 matches, 1 mismatch logged. Decisions D-17 records the split with phase 12.",
  "contracts": [],
  "timestamp": "2026-06-04T07:45:00Z"
}'
```

Use `outcome: "blocked"` if you finished partial scope but need human input
before continuing (typically after a probe surfaced a contract gap that
needs decision before steps can be written).

### 9. Commit

One commit per plan invocation. Message format: `phase: plan — short title`.

```bash
git add .state/
git commit -m "11: plan — orchestrator pipeline + event loop"
```

Always pass `-m`. The full prohibitions on interactive git commands apply
(see the Shell command discipline section in your Worker Contract).

### 10. Transition state

Set `project.json.state=execute`. The state machine will then dispatch the
next invocation as EXECUTE.

```bash
python3 tools/state.py set project.json state=execute
```

Then emit the exit signal (5-line block, see Worker Contract §6). Do not
start the first execute step in this invocation.

---

## What this action does NOT do

- Implement any code (that's EXECUTE)
- Run tests (that's EXECUTE, then REVIEW for phase-level)
- Promote gotchas, propagate contracts, run integration checks
  (those are CLOSE; the dep-probe in step 5 is a separate procedure with
  different scope)
- Plan future phases (only the current one — speculative
  multi-phase plans go stale)
- Read governance files — all needed context is in your assembled prompt
- Decide the next ACTION — the state machine does that after you exit

---

## Examples
<!-- assembler:omit_in_prompt -->

### Build phase, leaf module

Phase 5 (`event_store`, Build, no dependencies). Three steps.

```bash
python3 tools/state.py append-record phases.json '{"id":5,"module":"event_store","title":"Core storage","regime":"build","dependencies":[],"status":"pending"}'

python3 tools/state.py append-record steps.json '{"phase":5,"step":1,"title":"Append-only writer with fsync","status":"pending"}'
python3 tools/state.py append-record steps.json '{"phase":5,"step":2,"title":"Cursor-based reader","status":"pending"}'
python3 tools/state.py append-record steps.json '{"phase":5,"step":3,"title":"Crash-safety test suite","status":"pending"}'

python3 tools/state.py set project.json budget_type=steps

python3 tools/state.py append devlog.jsonl '{"phase":5,"step":null,"action":"plan","outcome":"complete","summary":"Phase 5 (event_store, Build, leaf): 3 steps covering writer, reader, crash-safety.","contracts":[],"timestamp":"2026-06-04T07:30:00Z"}'

git add .state/
git commit -m "5: plan — event_store core storage"

python3 tools/state.py set project.json state=execute
# Emit exit signal.
```

### Build phase, non-leaf module (with dependency probe)

Phase 11 (`orchestrator`, Build, depends on `event_store`). Probe runs
first; mismatch surfaces; one step added to handle the gap.

```bash
python3 tools/state.py append-record phases.json '{"id":11,"module":"orchestrator","title":"Pipeline + event loop","regime":"build","dependencies":["event_store"],"status":"pending"}'

# Probe finds idempotency_key kwarg is in the real surface but not the fake.
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":null,"action":"probe","outcome":"complete","summary":"Probed event_store: append() takes idempotency_key kwarg in real impl; fake omits it. Will adapt orchestrator to pass it; bug logged for fake.","contracts":["ARCH_event_store.md"],"timestamp":"2026-06-04T07:30:00Z"}'

python3 tools/state.py append-record decisions.json '{"id":"D-22","title":"Orchestrator passes idempotency_key","status":"closed","priority":"high","decision":"Generate idempotency_key from (worker_id, action_id, timestamp_minute). Pass through on every event_store.append call.","rationale":"Probe surfaced gap; adopting the real surface now avoids a retrofit."}'

python3 tools/state.py append-record steps.json '{"phase":11,"step":1,"title":"Pipeline topology with DI","status":"pending"}'
python3 tools/state.py append-record steps.json '{"phase":11,"step":2,"title":"Event loop with debounced extraction","status":"pending"}'
python3 tools/state.py append-record steps.json '{"phase":11,"step":3,"title":"Slash command routing","status":"pending"}'
python3 tools/state.py append-record steps.json '{"phase":11,"step":4,"title":"Idempotency_key generation + boundary test","status":"pending","notes":"Added after dep-probe surfaced gap. Boundary test exercises real event_store through orchestrator."}'

python3 tools/state.py append devlog.jsonl '{"phase":11,"step":null,"action":"plan","outcome":"complete","summary":"Phase 11 (orchestrator, Build, non-leaf): 4 steps after dep-probe added step 4 for idempotency_key. D-22 records the decision.","contracts":[],"timestamp":"2026-06-04T07:45:00Z"}'

git add .state/
git commit -m "11: plan — orchestrator pipeline + event loop"

python3 tools/state.py set project.json state=execute
```

### Refine phase

Phase 14 (`formatting`, Refine, no dependencies). Goal-based, time-budgeted.

```bash
python3 tools/state.py append-record phases.json '{"id":14,"module":"formatting","title":"Telegram message formatting polish","regime":"refine","dependencies":[],"status":"pending"}'

python3 tools/state.py append-record decisions.json '{"id":"D-30","title":"Phase 14 goal","status":"closed","priority":"high","decision":"MarkdownV2 escaping handles all edge cases observed in last week of group activity; messages render correctly on iOS, Android, Web.","rationale":"Recurring formatting bugs in production; correctness is perceptual."}'

python3 tools/state.py append-record decisions.json '{"id":"D-31","title":"First item: 3-paragraph workflow update","status":"open","priority":"high","decision":"First iteration produces a 3-paragraph workflow update with code fences, bold, bullet list; show to operator.","rationale":"Smallest input that exercises the markdown surface."}'

python3 tools/state.py set project.json budget_type=time time_budget_seconds=10800 time_started_at=2026-06-04T07:30:00Z

python3 tools/state.py append devlog.jsonl '{"phase":14,"step":null,"action":"plan","outcome":"complete","summary":"Phase 14 (formatting, Refine): 3-hour time budget. Goal D-30 closed; first item D-31 open. No step pre-plan.","contracts":[],"timestamp":"2026-06-04T07:45:00Z"}'

git add .state/
git commit -m "14: plan — telegram formatting Refine"

python3 tools/state.py set project.json state=execute
```

---

## Known tooling gaps referenced above
<!-- assembler:omit_in_prompt -->

- **No `state.py set` on array files.** `update-record` covers single-record
  field updates (used above to amend `dependencies` etc. on existing phase
  records). Generic-set across N records is not supported. Tracked as
  **FU-3** in `FOLLOWUPS.md`.

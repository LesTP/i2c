# Close — Wrap Up the Phase

Final phase action. Runs phase-level checks, promotes learnings to gotchas,
propagates contract changes, marks the phase complete, and hands off to the
human for audit by transitioning to `state: audit_boundary`. The state
machine has already decided this action is appropriate (review just
transitioned the project to `close`); do not re-decide.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: CLOSE`. Pair this procedure with the `Worker Contract` section in
your prompt for loop/escalation/output rules, and inherit the commit and
devlog conventions from `instructions/execute.md`.

---

## Procedure

### 1. Identify the phase being closed

Read `project.json.phase` from the assembled `Project State` section. That
is the phase you are closing. Confirm:

- The phase's record in `phases.json` has `status: "pending"` (binary; PLAN
  leaves it pending until CLOSE flips it to `complete` in step 8).
- Every step in `steps.json` for this phase has `status: "complete"`
  (review just ran, so this should be true).

If either is false, the state machine mis-dispatched; **escalate**
(`EXIT 2`, reason "close called with incomplete state").

### 2. Run phase-level tests

Run the full test suite for the phase's module (and any boundary tests
that exercise the module from outside). All must pass. If any fail:

- Tests broken by something review missed: **stop**, log via devlog
  `outcome: "failed"`, `EXIT 2`.
- Tests broken by ambient state (network, environment): note in the
  devlog summary, but do not block close on flakes. If you cannot tell
  the difference, stop.

For Build phases, run `pytest tests/<module>` or equivalent. For
Refine phases, defer the check (devlog `outcome: "blocked"`, reason
"awaiting Refine sign-off") and continue to step 3.

### 3. *(Conditional)* Pre-close: Integration Check - non-leaf modules only
<!-- assembler:requires=dependencies_nonempty -->

**Include this step only when** the current phase's `dependencies` array
in `phases.json` is non-empty. If the assembler has stripped this section
from your prompt, skip ahead.

This mirrors the dependency probe from `instructions/plan.md` but runs the
other direction: now that this module's code exists, verify it correctly
uses its declared dependencies.

For each name in `dependencies`:

1. **Type compatibility.** The dependency's output types match what this
   module's input expects. Read `ARCH_<dependency>.md` for the producer's
   contract; read this module's call sites for the consumption shape.
   List any mismatches.

2. **Boundary tests.** Feed the dependency's *actual* outputs into this
   module's *actual* functions (not the fake — the real producer through
   the real consumer call path). Add or run boundary tests that exercise
   this. Report pass/fail.

3. **Bridge logic.** If any adapter, conversion, or coercion is needed
   between the two, document what it is and where it lives. Bridge logic
   that lives in the *consumer* is fine; bridge logic that *should* live
   in the producer is a contract change to flag.

4. **Import discipline.** Confirm:
   - This module does not import from any integration / orchestration
     layer above it.
   - No direct cross-imports between subsystems except for the shared
     types declared in the dependency's ARCH file.

5. **Report via devlog.** Use `action: "integration_check"`,
   `step: null`:

   ```bash
   python3 tools/state.py append devlog.jsonl '{
     "phase": 11,
     "step": null,
     "action": "integration_check",
     "outcome": "complete",
     "summary": "Integration check orchestrator <- event_store: types match; boundary test passes; no bridge needed; no import-discipline violations.",
     "contracts": [],
     "timestamp": "2026-06-04T10:50:00Z"
   }'
   ```

   `outcome` choices:
   - `complete` — all dependencies checked, all pass
   - `partial` — some checked, some couldn't run in this environment
     (e.g., real dep unavailable); record what was deferred
   - `failed` — a check fails and indicates a real bug; **escalate**
     (`EXIT 2`) instead of continuing close
   - `blocked` — a mismatch surfaces that needs a decision before close
     can complete; **escalate**

If integration-check finds critical issues: stop the close. Do not
promote gotchas or mark the phase complete with a known
cross-module bug.

### 4. DEVLOG learning review — promote gotchas

Read this phase's devlog entries (the assembled `Phase Devlog` section
in your prompt covers them). Look for **trial-and-error patterns**: places
where one approach failed and a different approach worked. Extract a
one-line lesson per pattern and promote it to `project.json.gotchas`.

Criterion: a gotcha is something a fresh worker would do *wrong by default*
in a future iteration. If the lesson is "we picked X because Y", that's a
**decision**, not a gotcha. If the lesson is "do X, not the obvious Y,
because Y silently fails on Z", that's a gotcha.

```bash
python3 tools/state.py append-gotcha project.json \
  "JSONL appends must end with '\\n' or jq will conflate adjacent records"
```

Aim for short, prescriptive, future-proof phrasing. Worse: "Tried X then
switched to Y because Z bit us in step 11.2." Better: "Y for X-cases — Z
fails silently."

Do not promote everything that happened. Be selective. Gotchas accumulate
across phases and are read on every worker invocation; quality matters
more than quantity.

### 5. Contract scan — propagate changes

Read this phase's devlog entries with non-empty `contracts` arrays:

```bash
jq -c --argjson p $PHASE \
  'select(.phase == $p and (.contracts // [] | length) > 0)' \
  .state/devlog.jsonl
```

For each affected `ARCH_*.md` file, verify the propagation actually
happened. Two cases:

- **Immediate propagation** (the contract change committed alongside the
  source change per `instructions/execute.md`): confirm the ARCH file
  was updated in the same commit. If yes, nothing to do here.
- **Phase-boundary propagation** (deferred to close per
  `instructions/execute.md`): edit the ARCH file now, in one commit
  per affected file.

If you find a contract was logged in devlog but no ARCH file edit
exists in *any* commit of this phase: that's a **propagation gap**. Edit
the ARCH file now or escalate (your judgment on severity).

If a contract change in this phase affects a **downstream module that is
already built** (not just this module's own ARCH file): **escalate**.

### 6. Close decisions resolved by this phase

Read open decisions (the assembled `Decisions` section in your prompt
filtered to `status: "open"`). For each one that this phase resolved:

```bash
python3 tools/state.py update-record decisions.json \
  --match id=D-16 \
  status=closed \
  decision="Chose JSONL append-only files; benchmarked at 50k events/day under expected load." \
  rationale="Crash-safety + ordered iteration met; ops burden minimal vs SQLite or LMDB."
```

Required when closing: `status=closed`, and a final `decision` field that
captures what was decided (not "TBD"). Optional: update `rationale` if it
sharpened during implementation.

For decisions that the phase touched but **did not resolve**, leave them
open. Do not mark superseded unless a different decision genuinely
replaced this one (rare at close time).

### 7. Update ARCHITECTURE.md

The per-module `ARCH_<module>.md` files cover module-internal contract
(step 5). `ARCHITECTURE.md` is the project-wide doc — Component Map,
Implementation Sequence table, coupling notes, key-decision summaries —
and its Implementation Sequence table necessarily goes stale every phase
unless updated here.

**Required:**

- **Implementation Sequence status.** Find the row for this phase in the
  Implementation Sequence table and flip its `Status` column to
  `Complete`. If the table has no row for this phase (PLAN added the
  phase after `ARCHITECTURE.md` was last touched), add one matching the
  table's existing column shape.

**Optional (only if this phase changed it):**

- **Component Map.** If the phase clarified the module's responsibility
  or its dependency list, update its row.
- **Coupling Notes.** If implementation surfaced a coupling not
  previously documented (or contradicted one that was), update the
  relevant note.
- **Key Decisions summary block.** If step 6 closed a decision that's
  paraphrased in this section, update the summary line. Full decision
  text stays in `.state/decisions.json`.

If nothing changed beyond the Implementation Sequence status flip, that
one edit is the only one needed.

`ARCHITECTURE.md` is markdown, not structured state — direct file edit,
no `state.py` call. The edit ships in the close commit (step 10).

### 8. Update PROJECT.md risks (optional)

If this phase resolved an item listed in `PROJECT.md`'s Risks section,
edit the file to move that risk to Resolved (or remove). `PROJECT.md` is
markdown, not structured state — direct file edit, no `state.py` call.

Skip if this phase didn't touch risks.

### 9. Mark the phase complete

```bash
python3 tools/state.py complete phases.json --phase $PHASE
```

This sets `phases.json[id=$PHASE].status = "complete"`. No commit hash
argument here — phases are not tied to a single commit.

### 10. Commit close artifacts

One commit for the close action's writes (contract propagation edits,
ARCHITECTURE.md update, PROJECT.md risk edits, gotcha promotions).
Format: `phase: close — short title`.

```bash
git add ARCH_<module>.md ARCHITECTURE.md PROJECT.md .state/
git commit -m "11: close — propagate orchestrator contract, promote 2 gotchas"
```

Always pass `-m`. The full prohibitions on interactive git commands
apply (see the Shell command discipline section in your Worker Contract).

### 11. Append a CLOSE devlog entry

One entry per CLOSE invocation. `action: "close"`, `step: null`,
`outcome: "complete"` for a clean close. Summary records the artifacts
touched and counts.

```bash
python3 tools/state.py append devlog.jsonl '{
  "phase": 11,
  "step": null,
  "action": "close",
  "outcome": "complete",
  "summary": "Phase 11 closed: tests pass; integration check vs event_store passes; 2 gotchas promoted; D-16, D-22 closed; ARCH_orchestrator.md propagated.",
  "contracts": ["ARCH_orchestrator.md"],
  "timestamp": "2026-06-04T11:00:00Z"
}'
```

`outcome` choices for close:
- `complete` — phase done, ready for human audit
- `blocked` — phase done but waiting on Refine sign-off (step 2 deferred)
- `escalate` — integration check / propagation gap / cross-module
  breakage; you emitted `EXIT 2`
- `failed` — phase-level tests broken (step 2 failed); you emitted `EXIT 2`

### 12. Set the gate

```bash
python3 tools/state.py set project.json state=audit_boundary
```

`state=audit_boundary` halts the loop; the operator (or wrapper)
advances from there. **Do not advance `phase`** in this close action.

Then emit the exit signal (2-line block, see Worker Contract §4).
Exit code is `0` — close always terminates normally.

---

## What this action does NOT do

- Implement code (that was EXECUTE)
- Find and apply code review fixes (that was REVIEW)
- Plan the next phase (that's the next PLAN, after the human audit)
- Advance `project.json.phase`
- Declare project terminus (`state=done`) on its own

---

## Examples
<!-- assembler:omit_in_prompt -->

### Leaf-module close, no contract changes

Phase 5 (`event_store`, leaf). One open decision resolved; one gotcha
promoted.

```bash
# Phase-level tests pass.
pytest tests/event_store
# 24 passed in 1.8s

# No integration check (dependencies == []).

# Promote a gotcha:
python3 tools/state.py append-gotcha project.json \
  "fsync after every append; the OS write cache will lose tail entries on crash without it"

# Close the open decision D-16:
python3 tools/state.py update-record decisions.json \
  --match id=D-16 \
  status=closed \
  decision="JSONL append-only files. 24 tests, including injected-interrupt crash test, pass."

# No contract propagation needed (no devlog entries in phase 5 with non-empty contracts).

# Update ARCHITECTURE.md: flip the event_store row in the Implementation
# Sequence table from "In progress" (or whatever) to "Complete". No other
# sections changed this phase. (Direct file edit.)

# No PROJECT.md risk to close.

# Mark phase complete:
python3 tools/state.py complete phases.json --phase 5

# Commit:
git add ARCHITECTURE.md .state/
git commit -m "5: close — event_store core storage, D-16 resolved"

# Devlog:
python3 tools/state.py append devlog.jsonl '{"phase":5,"step":null,"action":"close","outcome":"complete","summary":"Phase 5 closed: 24 tests pass; D-16 resolved (JSONL backend); 1 gotcha promoted (fsync rule); ARCHITECTURE.md event_store row → Complete. No ARCH_<module> contract changes.","contracts":[],"timestamp":"2026-06-04T10:00:00Z"}'

# Set the gate:
python3 tools/state.py set project.json state=audit_boundary

# Emit exit signal (EXIT 0).
```

### Non-leaf close with integration check + contract propagation

Phase 11 (`orchestrator`, depends on `event_store`). Integration check
passes; one contract change from step 11.4 needs phase-boundary
propagation; two decisions closed.

```bash
# Phase-level tests pass.
pytest tests/orchestrator tests/boundary
# 31 passed in 4.2s

# Integration check (because dependencies == ["event_store"]):
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":null,"action":"integration_check","outcome":"complete","summary":"orchestrator <- event_store: types match; boundary test exercising real event_store through orchestrator.dispatch_action passes; no bridge; no import violations.","contracts":[],"timestamp":"2026-06-04T10:50:00Z"}'

# Gotcha promotion (one learning from the phase):
python3 tools/state.py append-gotcha project.json \
  "idempotency_key generation must include the timestamp_minute to survive retries across loop iterations"

# Contract propagation — devlog step 11.4 logged contracts=['ARCH_orchestrator.md']
# but did the immediate-propagation update happen?
git --no-pager show $(git log --pretty=%H --grep="^11\.4:" -n 1) --stat | grep ARCH_orchestrator
# (Output confirms ARCH_orchestrator.md was in the commit.)
# No additional propagation needed.

# Update ARCHITECTURE.md: flip orchestrator row to Complete; also update
# the Coupling Notes paragraph that mentioned orchestrator↔event_store
# now that the boundary test surfaced an extra adapter shim. (Direct
# file edit; both edits in one pass.)

# Close two decisions:
python3 tools/state.py update-record decisions.json \
  --match id=D-22 \
  status=closed \
  decision="idempotency_key shipped. Generated as sha256(worker_id || action_id || timestamp_minute)[:16]. Pass-through verified by boundary test."

python3 tools/state.py update-record decisions.json \
  --match id=D-17 \
  status=closed \
  decision="Phase 11 stayed scoped to pipeline + event loop; control-loop semantics deferred to phase 12 as planned."

# Mark phase complete:
python3 tools/state.py complete phases.json --phase 11

# Commit (ARCH_orchestrator.md was propagated immediately in step 11.4;
# ARCHITECTURE.md picks up the status flip + coupling-note update):
git add ARCHITECTURE.md .state/
git commit -m "11: close — orchestrator complete, 2 decisions resolved"

# Devlog:
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":null,"action":"close","outcome":"complete","summary":"Phase 11 closed: 31 tests pass; integration check vs event_store passes; 1 gotcha (idempotency_key composition); D-22, D-17 closed; ARCHITECTURE.md orchestrator row → Complete plus coupling-note refresh. ARCH_orchestrator.md propagation confirmed in step 11.4 commit.","contracts":[],"timestamp":"2026-06-04T11:00:00Z"}'

# Set the gate:
python3 tools/state.py set project.json state=audit_boundary
```

### Close blocked by integration check

Phase 8, dependency `event_store`. Integration check finds that
orchestrator's call signature doesn't match what `event_store.append`
actually expects.

```bash
# Phase-level tests pass (they use the fake).
pytest tests/orchestrator
# 18 passed

# Integration check fails:
python3 tools/state.py append devlog.jsonl '{"phase":8,"step":null,"action":"integration_check","outcome":"failed","summary":"orchestrator -> event_store: orchestrator passes idempotency_key as positional arg; event_store.append requires kwarg. Boundary test errors with TypeError. Bug in orchestrator; needs fix before close.","contracts":["ARCH_event_store.md","ARCH_orchestrator.md"],"timestamp":"2026-06-04T11:15:00Z"}'

# Do NOT promote gotchas, do NOT close decisions, do NOT mark phase complete.
# Emit EXIT 2 with reason "integration check failed: orchestrator/event_store call signature mismatch".
```

---

## Known tooling gap referenced above
<!-- assembler:omit_in_prompt -->

- **`state.py` lacks a read-side query helper**
  devlog.jsonl --phase 11 --where 'contracts != []'`). The contract scan
  in step 5 uses raw `jq` instead. This is intentional for now — reads
  don't need atomicity, and the assembler is the eventual home for
  pre-formatted queries. Tracked as **FU-14** in `FOLLOWUPS.md`.

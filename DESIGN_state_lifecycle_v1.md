# State-Lifecycle Redesign v1

> Design memo. Replaces the current `(state, blocked)` two-variable
> model with a single seven-value `state` enum that covers the full
> project lifecycle, including audit gates and terminus. Closes the
> recurring phase-boundary friction surfaced during the clankercourts
> pilot and unblocks a future autonomous-wrapper layer that needs to
> distinguish automatable halts from human-only halts using state
> alone.
>
> Status: **shipped 2026-06-08.** Implementation landed across commits
> 224aaf5 (memo), e2a71ec (Stack A — schema + state_machine), 9e53e62
> (Stack B — assembler renderer + tolerance), a4d88b5 (Stack C —
> instruction files), 7693330 (Stack E — FOLLOWUPS / README). Mirrored
> to clankercourts as commit 1c126db (Stack D). 50+ tests added; CC's
> `.state/project.json` migrated in place. Closes FU-30. Resolves the
> doc/code contradiction surfaced at the clankercourts post-Phase-3
> boundary (close.md ↔ plan.md ↔ assembler).
>
> Authors: operator + assistant, 2026-06-07 (memo) → 2026-06-08 (shipped).
> This memo is preserved as the architectural record of *why* the
> redesign happened; the implementation is canonical.

---

## 1. Problem

The current state model on `.state/project.json` uses two variables to
encode the lifecycle:

```python
{
  "phase":   int,                                  # which phase
  "state":   "plan" | "execute" | "review" | "close",
  "blocked": bool,                                  # halt the loop?
}
```

`state` advances cleanly through the four actions within a phase. The
problem is at the seams — between phases, on escalation, and at the end
of the project — where `blocked` ends up doing all the heavy lifting.

### 1.1 `blocked` is overloaded (three concerns, one boolean)

| Concern | Today's encoding |
|---|---|
| Phase boundary audit pending (post-CLOSE, expected) | `state=close, blocked=true` |
| Mid-phase escalation pending (worker hit a wall) | `state=execute\|review, blocked=true` |
| Project terminus (no more phases planned) | `state=close, blocked=true` AND (operator must inspect phases.json to know) |

Two of these are recoverable through workflow continuation; one is
permanent. The state file cannot distinguish them. FU-30 captures the
specific symptom: clearing `blocked=false` after the final phase
dispatches `CLOSE` again (the state machine has no way to know we're
done forever).

### 1.2 No single owner for the phase-boundary handoff

To start a new phase, four things must be true:

1. `project.json.state == "plan"`
2. `project.json.blocked == false`
3. `project.json.phase == N+1` (the new phase)
4. A `phases.json` record with `id == N+1` exists

Today these are split across multiple actors and the ownership is
contradictory:

- `instructions/close.md` step 12: *"Do not advance `phase`"* (close
  doesn't do #3)
- `instructions/close.md` "What this action does NOT do" section:
  *"Advance `project.json.phase` (the human / orchestrator does that
  implicitly when clearing the gate)"* (human does #3)
- `instructions/plan.md` step 4: *"If the phase record does not yet
  exist in `phases.json`, append it"* (PLAN does #4)
- `tools/assemble_context.py:691`: hard-errors if record missing before
  PLAN even runs (PLAN cannot do #4 because the assembler refuses to
  build the PLAN prompt without it)

Result: every phase boundary requires a manual stub (`append-record
phases.json '{...}'`) just to placate the assembler — work that
duplicates what PLAN is documented to do. We've been calling these
костыли. The clankercourts pilot has hit the same friction at every
phase boundary; each time we've improvised.

### 1.3 Why fix it now

CC is between Phase 3 close and Phase 4 plan. Either we patch around
the friction one more time, or we resolve it structurally before
planning any further phases. The autonomous-wrapper goal (a separate
future workstream) magnifies the cost of the current overload — a
wrapper cannot disambiguate "automatable boundary halt" from "human-
required escalation halt" using state alone, because they look
identical in `project.json`. Fixing the state model now makes the
wrapper a thin layer over the existing protocol later.

---

## 2. Goal

Single var, single concern. `state` is the only lifecycle variable.
Its enum is wide enough to express every lifecycle moment, including
the moments where the loop is halted and the project is terminal.

Specifically:

- **Drop `blocked`** from `project.json` entirely.
- **Expand `state`** to seven values that cover normal dispatch (4),
  human-attention pauses split by recovery shape (2), and terminus (1).
- **One owner per write.** The human (or autonomous wrapper) owns
  transitions out of audit/done states. Workers own transitions into
  them and within-phase advancement.
- **Assembler tolerates the legitimate "no record yet" state** that
  follows phase advancement and precedes PLAN's record creation.

---

## 3. New state model

### 3.1 The seven values

| Value | Meaning | Set by |
|---|---|---|
| `plan` | Next action is PLAN | Human (at `audit_boundary` clear) |
| `execute` | Next action is EXECUTE | PLAN worker; or human (resume from escalation) |
| `review` | Next action is REVIEW | EXECUTE worker (last step done); or human (resume) |
| `close` | Next action is CLOSE | REVIEW worker |
| `audit_boundary` | Phase done; human/wrapper decides next phase or terminus | CLOSE worker (always, as final act) |
| `audit_escalation` | Worker hit an escalation; human required to resolve | EXECUTE or REVIEW worker on escalation |
| `done` | Project terminal; no further dispatch | Human (at `audit_boundary` clear, when no more phases planned) |

Naming: the `audit_` prefix groups the two "human attention required"
values. They sort together, grep together, and read as a family.

### 3.2 Why split `audit_boundary` from `audit_escalation`

These two halt-the-loop conditions look identical to the state machine
(both → EXIT) but differ sharply in their recovery procedure and in
their automatability:

| Property | `audit_boundary` | `audit_escalation` |
|---|---|---|
| When it fires | After every CLOSE (expected, structural) | On three-strikes / contract drift / scope expansion (unexpected, off happy-path) |
| Frequency | Once per phase | Rare |
| Recovery action | `set phase=N+1 state=plan` OR `set state=done` | `set state=execute\|review\|...` (resume) — typically requires reading devlog + applying a fix or decision first |
| Automatable by a wrapper | Yes (advance with a policy check) | No (by definition the agent could not resolve it) |

If both shared one value, every consumer (human, autonomous wrapper,
status tooling) would have to read devlog to disambiguate. With them
split, the state file alone is the source of truth for what kind of
attention is needed.

### 3.3 Why `done` is distinct from `audit_boundary`

A project at `audit_boundary` is "between phases — maybe more, maybe
not, human decides." A project at `done` is "no more work planned." The
two correspond to genuinely different downstream behaviors:

- A wrapper polling for projects-needing-attention should treat
  `audit_boundary` as "maybe automatable" and `done` as "ignore
  forever."
- A status dashboard or `assemble_context.py --section status` rendering
  should label `done` distinctly so an operator returning after a break
  can immediately see "this one is finished" without inspecting
  phases.json.
- Restarting a "done" project later (adding phase N+1 after we declared
  N final) is a deliberate user action — `set phase=N+1 state=plan` —
  that should not happen accidentally by an auto-clear.

---

## 4. State-machine dispatch matrix

`tools/state_machine.py:decide()` becomes a flat 7-way switch (no
boolean overlay):

| `state` | pending steps for current phase | ACTION | NEXT |
|---|---|---|---|
| `plan` | (any) | PLAN | execute |
| `execute` | > 1 | EXECUTE | execute |
| `execute` | == 1 | EXECUTE | review |
| `execute` | == 0 | REVIEW | close |
| `review` | (any) | REVIEW | close |
| `close` | (any) | CLOSE | audit_boundary |
| `audit_boundary` | (any) | EXIT | audit_boundary |
| `audit_escalation` | (any) | EXIT | audit_escalation |
| `done` | (any) | EXIT | done |

The `STOP_BEFORE_REVIEW=true` short-circuit (current FU-7 / runner env
var) remains: any REVIEW dispatch becomes `EXIT` with `NEXT=review`.

The `NEXT` column for the EXIT-dispatching states is "stay where you
are" — it's a no-op for the runner, since EXIT halts the loop. Kept
explicit for symmetry and to satisfy the existing dispatch-matrix
docstring shape.

---

## 5. Per-action protocol changes

### 5.1 CLOSE worker (`instructions/close.md`)

Conservative-closure rule: **CLOSE always sets `state=audit_boundary`.**
It never sets `state=done` directly. The "done vs. another phase"
decision belongs to the human/wrapper at the boundary, since they alone
know whether more phases are planned.

Step changes:

- **Step 12** ("Set the gate"): replace `set project.json blocked=true`
  with `set project.json state=audit_boundary`. Leaves no other field
  to touch (no `blocked` field exists anymore).
- **"What this action does NOT do"** section: remove the line
  *"Advance `project.json.phase` (the human / orchestrator does that
  implicitly when clearing the gate)"* and replace with a short note:
  the human/wrapper transitions out of `audit_boundary` by either
  `set phase=N+1 state=plan` (advance) or `set state=done` (terminus).
- **Worked examples**: update the `python3 tools/state.py set
  project.json blocked=true` lines to the new form.

### 5.2 PLAN worker (`instructions/plan.md`)

Minimal behavioral change — PLAN still owns phases.json record creation.
Step 1 framing should reflect that `project.json.phase` was advanced by
the human/wrapper at the `audit_boundary` clearing transition, not
implicitly by close.

- **Step 1**: keep the three-case logic (no record / pending record /
  complete record). Add a sentence: *"If you arrived here from
  `audit_boundary`, the human/wrapper set `project.json.phase` and
  `state=plan` in one atomic transition. The phases.json record for
  this new phase does not yet exist; you will create it in step 4."*
- Step 4 unchanged.
- The "escalate via EXIT 2" branch for `status: complete` records
  becomes: `set state=audit_escalation` (not `blocked=true` — the field
  doesn't exist anymore) and emit EXIT 2.

### 5.3 EXECUTE worker (`instructions/execute.md`)

Escalation transitions move from `blocked=true` to
`state=audit_escalation`:

- **Cross-module breakage example**: `set state=review blocked=true` →
  `set state=audit_escalation` (state alone suffices; no separate flag).
- **Three-strikes example**: add an explicit
  `set state=audit_escalation` before the EXIT 2 emission (today's
  example only writes a devlog entry; under the new model the state
  transition is required so the state machine returns EXIT on the next
  dispatch).
- "Set blocked=true" in the Refine-regime "Goal needs human sign-off
  mid-phase" branch becomes `set state=audit_escalation` (or
  `audit_boundary` — depends on whether mid-Refine sign-off is the same
  shape as escalation; design recommendation: `audit_escalation` since
  the recovery is "resume execute," not "advance phase").

### 5.4 REVIEW worker (`instructions/review.md`)

Same shape as EXECUTE: any `blocked=true` write becomes
`state=audit_escalation`.

- **Contract-drift escalate example**: state transition added.
- **Must-fix-balloons-into-architecture example**: same.

### 5.5 WORKER_SPEC.md, CLAUDE.md, CODEX.md

Scan for any references to `blocked` or the `(state, blocked)` matrix
and update. Most likely small — the lifecycle invariants are stated
once and referenced from instruction files.

---

## 6. Code surface changes

### 6.1 `schemas/project.schema.json`

```diff
   "type": "object",
-  "required": ["phase", "state", "blocked"],
+  "required": ["phase", "state"],
   "additionalProperties": false,
   "properties": {
     "phase": { ... unchanged ... },
     "state": {
       "type": "string",
-      "enum": ["plan", "execute", "review", "close"],
+      "enum": [
+        "plan", "execute", "review", "close",
+        "audit_boundary", "audit_escalation", "done"
+      ],
       "description": "Current lifecycle position. Drives state machine dispatch. audit_boundary/audit_escalation are 'halt' states (loop EXITs, human or wrapper resolves); done is terminal."
     },
-    "blocked": { ... removed ... },
     ...
```

`required` shrinks from 3 fields to 2. `blocked` removed entirely
(no compatibility shim — single consumer, planned migration).

### 6.2 `tools/state_machine.py`

`decide()` becomes a 7-branch switch matching §4's matrix. The
`blocked` branch removed. Add branches for `audit_boundary`,
`audit_escalation`, `done` → all return `("EXIT", state)`.

Module docstring's dispatch-matrix table updated to match §4.

Test coverage: add cases for each new state value (3 new tests minimum:
one per new value, asserting EXIT dispatch).

### 6.3 `tools/invariants.py`

`check_post_action(root, action)`:

- For `action == "close"`: replace `blocked == true` assertion with
  `state == "audit_boundary"` and current-phase `status == "complete"`.
- For `action == "review"`: replace `state == "close"` assertion with
  `state in ("close", "audit_escalation")` (review may escalate).
- For `action == "execute"`: replace `state in {"execute", "review"}`
  with `state in {"execute", "review", "audit_escalation"}`.
- For `action == "plan"`: keep `state == "execute"`; add tolerance for
  `state == "audit_escalation"` (plan-time escalation per §5.2).

### 6.4 `tools/assemble_context.py`

Three render functions touch the phase record. Each needs a tolerance
branch when `--action plan` AND no record:

- `render_phase_heading()` (line 686-): when no record AND
  `ctx.action == "plan"`, return `## Phase: {N} — (record to be created
  by PLAN)` instead of `error_exit`. For any other action, keep the
  hard error — it indicates a real misdispatch.
- `render_current_phase()` (line 765-): when no record AND
  `ctx.action == "plan"`, emit a placeholder table noting "PLAN will
  populate this record."
- `_eval_dependencies_nonempty()` (line 208-): when no record, return
  `False` (no dep-probe section in the PLAN prompt). PLAN's procedure
  step 5 handles the case where the operator picks a non-leaf module
  via `update-record`.

Module-level test additions: 3-4 cases covering "PLAN action, fresh
phase, no record" for each affected renderer.

Documentation: update `ARCH_assembler.md` §4.2 "Required input failure"
table to note that the phases.json record requirement is conditional on
action (`--action plan` excepted).

### 6.5 `tools/state.py`

Likely no change needed — `state.py set` already accepts arbitrary
key=value pairs and validates against the schema on write. Once
`blocked` is removed from the schema, `set project.json blocked=true`
will fail validation, which is the desired behavior. No
backwards-compat shim.

Worth a quick scan for any `blocked`-specific helpers or hardcoded
references. None expected.

### 6.6 Tests

A grep for `"blocked"` across the i2c tree will surface every place
that needs touching:

- `tests/test_state.py` — likely round-trip cases that include `blocked`
- `tests/test_state_machine.py` — dispatch matrix tests
- `tests/test_invariants.py` — post-action invariant tests
- `tests/test_assemble_context.py` — fixture state files
- `examples/initial_state/` — fixture; should be updated to the new
  shape
- `examples/smoke_test.py` — end-to-end script; verify it still runs

---

## 7. Migration

### 7.1 Existing clankercourts state

CC's current `.state/project.json`:

```json
{
  "phase": 3,
  "state": "close",
  "blocked": true,
  "gotchas": [...],
  "budget_type": "steps"
}
```

Becomes:

```json
{
  "phase": 3,
  "state": "audit_boundary",
  "gotchas": [...],
  "budget_type": "steps"
}
```

One-time edit. Could be a `state.py set state=audit_boundary` (drops
`blocked` implicitly only if schema rejects unknown additional
properties; otherwise manual JSON edit). The schema has
`additionalProperties: false`, so the next `set` will fail to write the
file with `blocked` still present — meaning the operator must remove it
in the same edit. Easiest path: a quick manual edit of
`.state/project.json`, then verify with `state.py validate`.

Could also ship as a `tools/migrate_v1.py` script for future i2c
adopters, but with one current consumer it's overkill.

### 7.2 Order of operations

The change set is small but ordered:

1. **i2c framework changes** (Stack A): schema + state_machine + invariants
   land together as one commit. Tests pass.
2. **Assembler tolerance** (Stack B): the render_phase_heading and
   related changes. Can ship independently of #1 but is part of the
   same fix; sequence with #1.
3. **Instruction file edits** (Stack C): close.md, plan.md, execute.md,
   review.md, WORKER_SPEC.md, CLAUDE.md, CODEX.md.
4. **Cross-project sync** (Stack D): mirror updated instruction files
   from i2c to clankercourts. Same pattern as past syncs (`chore: sync
   ... from i2c`).
5. **CC state migration** (also Stack D): one-line edit of
   `.state/project.json`. Verify state_machine + assembler both behave
   correctly under the new state.
6. **Bookkeeping** (Stack E): close FU-30, add a release note in
   `FOLLOWUPS.md` referencing this memo, update i2c README's state-model
   description.

Estimated total: 6–8 commits, ~150-200 LOC of real code + tests + doc
churn. Risk profile: low — single consumer, no backwards-compat
constraint, all tests can be authored alongside.

---

## 8. Autonomous wrapper sketch (out of scope; design check only)

The wrapper is NOT built in this work. This section exists to verify
that the new state model serves the longer-term goal.

A minimal autonomous-mode wrapper, given the new state model, looks
like:

```python
# Pseudocode, not the implementation.
import json, time
from pathlib import Path

POLICY = {
    "auto_advance_at_boundary": True,
    "wake_on_escalation": True,        # never auto-resolve
    "ignore_done": True,
}

def supervise(project_root: Path):
    while True:
        state = json.loads((project_root / ".state/project.json").read_text())["state"]

        if state in ("plan", "execute", "review", "close"):
            run_iteration(project_root)         # invoke i2c runner once
        elif state == "audit_boundary":
            if POLICY["auto_advance_at_boundary"]:
                next_phase = pick_next_phase(project_root)   # from a phase queue
                if next_phase is None:
                    set_state(project_root, state="done")
                else:
                    set_state(project_root, phase=next_phase.id, state="plan")
            else:
                wake_human(project_root, reason="boundary audit")
                break
        elif state == "audit_escalation":
            wake_human(project_root, reason="escalation")
            break
        elif state == "done":
            break

        time.sleep(POLLING_INTERVAL)
```

Every transition the wrapper makes is one `state.py set` call. No
devlog parsing required — state alone tells it what to do. The
phase-queue input (`pick_next_phase`) is a separate concern (could be
a JSON file, could be operator-curated, could be LLM-driven). The
wrapper proper is ~30 lines.

This validates that the proposed state model is sufficient for the
autonomous goal. If the model fails to support a real wrapper case
when we build it, that's a signal to revisit; for now the sketch
covers every transition.

---

## 9. Decisions captured

Recorded inline so they survive past this memo:

| ID | Decision | Rationale |
|---|---|---|
| **D-state-1** | Drop `blocked`; expand `state` to 7 values | Single var, single concern; removes 3-way overload of one boolean |
| **D-state-2** | Split audit into `audit_boundary` and `audit_escalation` | Recovery shape and automatability differ sharply; state alone disambiguates |
| **D-state-3** | Conservative closure: CLOSE always sets `audit_boundary`; never sets `done` directly | "More phases or done?" is a deliberate human/wrapper decision; close worker has no business inferring |
| **D-state-4** | Use `audit_` prefix to group the two pause states | Greppable, alphabetical sort groups them, visual family marker |
| **D-state-5** | Assembler tolerates missing phase record only under `--action plan` | The only legitimate case for missing record; any other action hitting it is misdispatch |
| **D-state-6** | No backwards-compat shim; one-time CC state migration | Single consumer; cost of compatibility code exceeds cost of one-line manual edit |
| **D-state-7** | `done` is recoverable only by deliberate `set state=plan` | Adding a phase after declaring done is a real workflow but must be explicit; never an accident |

These are captured in this memo. i2c is supervised work, not
self-governed, so there is no separate `.state/decisions.json` to write
them to — the memo itself is the canonical record. The implementation
commits should reference D-state-N IDs in their commit messages so
future readers can grep back to this document.

---

## 10. Open questions / followups

- **Q1: Should `audit_escalation` carry sub-reason structure?**
  Devlog already captures `outcome: escalate` with a summary; state
  alone gives the "what kind of attention" axis. Sub-reason is at
  most a nice-to-have for status tooling. **Recommend**: defer until
  a real consumer needs it; the state value + last-devlog-entry
  pairing is sufficient for now.
- **Q2: What happens if state.py is called with a removed/renamed
  field (e.g. `blocked=true`) post-migration?** The schema's
  `additionalProperties: false` will reject the write atomically. Good
  error message? Worth confirming — if not, a minor patch.
- **Q3: Does `WORKER_SPEC.md` mention `blocked` in its Output
  Contract section?** Scan during implementation. If yes, update the
  contract to reference the new state values explicitly.
- **Q4: Should `state.py set` warn when the operator writes a state
  transition that's invalid per the matrix (e.g.
  `state=audit_boundary` while phase has pending steps)?** Probably
  not for v1 — operators sometimes intentionally write unusual
  transitions for recovery. `invariants.py` is the canonical place
  for post-hoc checking. File as a followup if pattern emerges.
- **Q5: Anything to do about the `STOP_BEFORE_REVIEW` env var?**
  Stays. Composes cleanly with the new matrix; no logic change.

---

## 11. Implementation order (commit-by-commit)

Treat this as the phase plan for the change set itself. Each row is
one commit, in dependency order.

| # | Commit subject | Files touched |
|---|---|---|
| 1 | `schema+machine: introduce 7-state lifecycle, drop blocked (D-state-1..7)` | `schemas/project.schema.json`, `tools/state_machine.py`, `tools/invariants.py`, `tests/test_state_machine.py`, `tests/test_invariants.py`, `tests/test_state.py`, `examples/initial_state/project.json`, `examples/smoke_test.py` |
| 2 | `assembler: tolerate missing phase record under --action plan` | `tools/assemble_context.py`, `tests/test_assemble_context.py`, `ARCH_assembler.md` |
| 3 | `close.md: set state=audit_boundary; conservative closure` | `instructions/close.md` |
| 4 | `plan.md/execute.md/review.md: escalation uses state=audit_escalation` | `instructions/plan.md`, `instructions/execute.md`, `instructions/review.md` |
| 5 | `WORKER_SPEC/CLAUDE/CODEX: drop blocked references; reflect new states` | `WORKER_SPEC.md`, `CLAUDE.md`, `CODEX.md`, possibly `templates/.claude/commands/*.md` |
| 6 | `FOLLOWUPS: close FU-30; ref DESIGN_state_lifecycle_v1` + README state-model update | `FOLLOWUPS.md`, `README.md` |
| 7 | (CC repo) `chore: sync instructions from i2c (state lifecycle v1)` | clankercourts `instructions/*.md` |
| 8 | (CC repo) `chore: migrate .state/project.json to new schema` | clankercourts `.state/project.json` |

After commit 8, the CC operator can resume Phase 4 planning on the new
model with no manual stubbing.

---

## 12. Why this is the right time

- CC is at a clean stopping point (post-Phase-3 close). No code in
  flight to disrupt.
- The friction repeats every phase boundary; the cost of one more
  костыль ≈ the cost of finishing this design.
- The autonomous-wrapper goal is real and benefits directly.
- Single consumer means no compatibility burden; the migration is one
  line.
- The change preserves every existing workflow (PLAN still creates
  records, CLOSE still gates, escalations still pause) — it just makes
  each one expressible with a single variable.

The change is small. The clarity gained is structural.

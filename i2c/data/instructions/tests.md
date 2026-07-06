# Tests — Author the Phase's Acceptance Suite

Writes a **phase-level acceptance suite** from the module contract, *before*
and *independently of* the implementation, so "did the implementation work"
becomes objectively answerable rather than self-graded. The state machine has
already decided this action is appropriate (PLAN set `state=tests` for a Build
phase); do not re-decide.

This action runs **only for Build phases** (D-tests-5). PLAN routes
Refine/Explore phases straight to `execute`, so if you were dispatched here the
phase is Build.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: TESTS`. Pair this procedure with the `Worker Contract` section already
in your prompt for loop/escalation/output rules, and inherit the devlog
conventions from `instructions/execute.md`. As in EXECUTE, the runner — not you
— commits; do not run `git`.

---

## Procedure

### 1. Confirm the phase and regime

Read `project.json.phase` from the assembled `Project State` section and the
phase record from the `Current Phase` section (`phases.json`). Confirm
`regime == "build"`. If the regime is Refine or Explore, TESTS was
mis-dispatched (PLAN should have routed straight to `execute`): **escalate** —
set `state=audit_escalation` via `i2c state`, log a devlog entry with
`outcome: "escalate"`, and `EXIT 2` (reason "tests dispatched on non-Build
phase").

### 2. Read the contract

Read the `Module Contract` section of your prompt (`ARCH_<module>.md` for this
phase's module) and the `Current Phase` / `Current Phase Steps` sections. The
**contract is the primary input**: write acceptance tests for the phase's
observable success criteria — the module's public surface, its guarantees,
its error contracts — *not* for the internal shape of any single planned step.

The planned step table is available for context, but keep the suite
**decomposition-independent**: it expresses *what the phase must achieve*, so it
stays valid regardless of how EXECUTE sequences the work.

### 3. Write the acceptance suite

Author the suite under the path convention (D-tests-3):

```
tests/acceptance/phase_<N>/
```

where `<N>` is the current phase number. Each test expresses one observable
criterion from the contract. Guidelines:

- **Test the contract, not the implementation.** Assert on public behavior and
  documented guarantees, not private helpers.
- **Expected to be red (or partial-red).** No implementation exists yet for
  this phase's new surface, so the suite should fail (or fail in part). That is
  correct — EXECUTE's job is to make it green. Phases build on prior phases, so
  some tests may already pass against shared infra; partial-red is fine.
- **Cover interactions.** If the phase introduces a set of rules / handlers /
  cases, add at least one test for the cross-product (what happens when several
  apply at once), not only one test per case.
- Do **not** implement production code here. TESTS writes tests only; if a test
  needs a helper/fixture, put it under the acceptance dir.

### 4. Do not commit — the runner does

Leave the new suite files in the working tree; do **not** run `git`. After you
exit, the deterministic runner commits the files you added (fenced off from any
unrelated working-tree changes) as `<phase>.tests: <your devlog summary>`. The
distinct `N.tests:` prefix marks the frozen acceptance suite so the oracle /
integrity check can find it. This is why EXECUTE must never edit the suite.

### 5. Append a devlog entry for the tests action

One entry per TESTS invocation. `action: "tests"`, `step: null` (the suite is
phase-level), `outcome: "complete"` for a finished suite:

```bash
i2c state append devlog.jsonl '{
  "phase": 2,
  "step": null,
  "action": "tests",
  "outcome": "complete",
  "summary": "Phase 2 acceptance suite: 8 contract tests under tests/acceptance/phase_2/ covering append/read guarantees, fsync durability, and cursor ordering. Red as expected (event_store not implemented yet).",
  "contracts": [],
  "timestamp": "2026-07-06T07:30:00Z"
}'
```

`outcome` choices for tests:
- `complete` — the acceptance suite is authored and ready to freeze
- `partial` — out of budget mid-authoring; the state machine redispatches TESTS
  while `state` stays `tests` (record what's left for the next invocation)
- `blocked` — the contract is too ambiguous to write acceptance tests against;
  set `state=audit_escalation` and `EXIT 2`
- `escalate` — regime mismatch (step 1) or another judgment call; you emitted
  `EXIT 2`

### 6. Transition state

Transition based on the `outcome` you recorded in step 5:

- **`complete`** — the suite is authored and ready to freeze. Set
  `project.json.state=execute`; the state machine dispatches EXECUTE next.

  ```bash
  i2c state set project.json state=execute
  ```

- **`partial`** — out of budget mid-authoring. **Leave `state=tests`** (do not
  transition); the state machine redispatches TESTS so you can finish the suite.

- **`blocked`** / regime mismatch — set `state=audit_escalation` (per step 5)
  and `EXIT 2`.

Then emit the exit signal (2-line block, see Worker Contract §4). Do not start
implementing against the suite in this invocation — that is EXECUTE.

---

## What this action does NOT do

- Implement production code (that's EXECUTE — it makes the suite green)
- Write fine-grained unit tests for the implementation (EXECUTE may add those;
  they are *not* the oracle)
- Plan steps or create the phase record (that was PLAN)
- Run `git` / commit — the runner commits your suite as `<phase>.tests:` after
  you exit
- Read governance files — all needed context is in your assembled prompt
- Decide the next ACTION — the state machine does that after you exit

---

## Examples
<!-- assembler:omit_in_prompt -->

### Build phase, leaf module

Phase 2 (`event_store`, Build). Write the acceptance suite from the contract,
log the devlog entry, transition to execute.

```bash
# Write tests/acceptance/phase_2/test_event_store_contract.py etc.
# Do NOT run git — the runner commits them as "2.tests: <summary>".

i2c state append devlog.jsonl '{"phase":2,"step":null,"action":"tests","outcome":"complete","summary":"Phase 2 acceptance suite: 8 contract tests under tests/acceptance/phase_2/ covering append/read guarantees, fsync durability, cursor ordering. Red as expected.","contracts":[],"timestamp":"2026-07-06T07:30:00Z"}'

i2c state set project.json state=execute
# Emit exit signal (EXIT 0).
```

### Regime mismatch — escalate

Dispatched TESTS but the phase record says `regime: "refine"`. PLAN should have
routed straight to execute. Halt.

```bash
i2c state set project.json state=audit_escalation
i2c state append devlog.jsonl '{"phase":14,"step":null,"action":"tests","outcome":"escalate","summary":"TESTS dispatched on a Refine phase; acceptance-suite authoring is Build-only (D-tests-5). PLAN should have set state=execute. Needs human to correct the phase regime or state.","contracts":[],"timestamp":"2026-07-06T07:30:00Z"}'

# Emit EXIT 2 with reason "tests dispatched on non-Build phase".
```

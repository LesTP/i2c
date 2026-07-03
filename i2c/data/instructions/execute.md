# Execute — Do the Next Step

Performs one unit of implementation work, records the result, and either yields
back to the runner (single-step mode) or continues to the next step
(multi-step mode). The state machine has already decided this action is
appropriate; do not re-decide.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: EXECUTE`. Pair this procedure with the `Worker Contract` section
already included in your prompt for the loop/escalation/output rules.

---

## Procedure

Two branches by regime. Identify the current phase's regime from `phases.json`
in the assembled `Current Phase` section, then follow the matching branch.

### Build / Explore regime — step-based

1. **Pick the next pending step.** From the `Current Phase Steps` section in
   your prompt (sourced from `.state/steps.json`), find the lowest-numbered
   step where `status == "pending"` for the current phase. That is your work
   target. Refer to it as **`phase.step`** in commits and devlog entries
   (e.g., `11.3`).

   Step status is binary (`pending` until `complete`).

2. **Read context.** Read the source files and tests relevant to the step.
   Re-read **immediately before editing** so edits reflect the latest
   source. Governance state already arrived fresh in your prompt; this
   rule applies to source files only.

3. **Implement and test.** Make the change. Run the test suite (or the
   relevant subset). Passing tests is the gate to commit. If they fail,
   fix in place or escalate per the rules in Escalation Conditions.

4. **Do not commit — the runner does.** Leave your edits in the working tree;
   do **not** run `git`. After you exit, the deterministic runner commits the
   files you changed (fenced off from any unrelated working-tree changes) as
   `phase.step: <your devlog summary>` and records the commit hash for you. This
   removes the interactive-hang / wrong-scope / forgotten-commit hazards and
   guarantees the `phase.step:` format recovery relies on.

5. **Mark the step complete.**

   ```bash
   i2c state complete steps.json --phase N --step M
   ```

   The CLI atomically rewrites `steps.json` after validating the schema. Do
   **not** pass `--commit` — the runner back-fills the hash after it commits.

6. **Append a devlog entry.** One entry per step, JSON envelope matching
   `schemas/devlog_entry.schema.json`. Required fields: `phase`, `step`,
   `action`, `outcome`, `summary`, `timestamp`. Optional: `contracts`.

   ```bash
   i2c state append devlog.jsonl '{
     "phase": 11,
     "step": 3,
     "action": "execute",
     "outcome": "complete",
     "summary": "Wired orchestrator slash commands through CodexClient with 7 new tests.",
     "contracts": [],
     "timestamp": "2026-06-04T04:30:00Z"
   }'
   ```

   `outcome` enum: `complete` (work done), `partial` (some progress, not
   finished), `blocked` (waiting on input), `escalate` (judgment call needed),
   `failed` (work didn't land — investigate before claiming complete on the
   step). For a normal "step done, tests pass" iteration, use `complete`.

   `contracts` lists any `ARCH_*.md` files modified or whose contracts were
   logically changed. Empty array if none — see Contract Changes below.

   `timestamp` is ISO 8601 UTC. Use the current time.

7. **Decide what's next.**

   - **More pending steps in this phase?** State stays `execute`. In
     single-step mode, emit the exit signal — the runner will re-invoke for
     the next step. In multi-step mode, loop back to step 1 of this
     procedure.
   - **All steps in this phase are now `complete`?** Transition the project
     state to `review`:

     ```bash
     i2c state set project.json state=review
     ```

     Then emit the exit signal. The next invocation will be a REVIEW action.

### Refine regime — time-based, iteration-driven

Refine work is open-ended toward a stated goal. Steps emerge as you go rather
than being pre-listed. Budget is wall-clock, not step count
(`project.json.time_budget_seconds` with `time_started_at`).

1. **Read the goal.** From the `Module Contract` section of your prompt
   (`ARCH_module.md`) and the phase title in `phases.json`. The goal is
   evaluated by human perception, not by tests.

2. **Choose the next small increment.** Something showable in a single
   commit. Refine sessions plan one increment at a time, not a fixed list.

3. **Implement.** Same code/edit discipline as Build (fresh reads, no
   `$EDITOR`, etc.). Tests where they apply, but absence of automated tests
   is expected for perceptual work.

4. **Do not commit — the runner does.** Leave your increment in the working
   tree; the runner commits it after you exit as `phase: <your devlog summary>`
   (Refine has no step number). Do **not** run `git`.

5. **Append a devlog entry.** Use `"step": null` (devlog schema allows it for
   non-step-bound entries) or use the iteration number you used in the
   commit. `outcome` choices for Refine: `complete` (this increment landed
   and shipped), `partial` (made progress, more to do this phase),
   `blocked` (need human input before next iteration).

   ```bash
   i2c state append devlog.jsonl '{
     "phase": 14,
     "step": null,
     "action": "execute",
     "outcome": "partial",
     "summary": "First pass at telegram message formatting. MarkdownV2 escape edge cases need a second look.",
     "timestamp": "2026-06-04T05:00:00Z"
   }'
   ```

6. **Decide what's next.**

   - **Time budget remaining and more work to do?** Stay in `execute`.
     The state machine handles the time check; if it returns EXECUTE again,
     loop back to step 2.
   - **Time exhausted OR phase goal met?** Transition to `review`:

     ```bash
     i2c state set project.json state=review
     ```

   - **Goal needs human sign-off mid-phase?** Transition to escalation and exit:

     ```bash
     i2c state set project.json state=audit_escalation
     ```

     Emit exit signal with outcome=`blocked`. The state machine returns
     EXIT on the next dispatch; human/wrapper resolves the escalation and
     restores `state=execute` to resume.

---

## Contract Changes

A "contract" is anything another module reads to know how to call yours: the
public API of `ARCH_<module>.md`, type signatures, side-effect guarantees,
error contracts. **Edits to internal-only code are not contract changes.**

When a step modifies a contract:

1. **List affected docs** in the devlog entry's `contracts` array:

   ```json
   "contracts": ["ARCH_event_store.md", "ARCH_query_interface.md"]
   ```

2. **Decide propagation timing:**

   - **Immediate** (same commit as the implementation): cross-module API
     signature changes, type changes, anything where a cold-start session
     on a *consumer* module would generate wrong code reading the current
     `ARCH_*.md`. Edit the affected `ARCH_*.md` files now — the runner commits
     them together with the code change.
   - **Phase boundary** (defer to close): purely additive changes,
     documentation-only updates, anything that doesn't break consumers. The
     close action scans `devlog.jsonl` for non-empty `contracts` arrays and
     handles propagation then.

3. **Cross-module breakage** (a consumer module that is already built will
   stop compiling/passing with this change): **hard stop.** Do not commit.
   Set `state=audit_escalation` and emit `EXIT 2` with reason
   "contract change affects built module: \<name\>". The state machine
   returns EXIT on the next dispatch; the human/wrapper routes the
   escalation for decision.

4. **Test propagation** when a contract crosses into a built module:
   - Update the consumer's test double to match the new signature
   - Add or update a boundary test that exercises the real producer through
     the consumer's call path
   - Both updates land in the runner's commit alongside the contract change

---

## Scope rules

If, mid-step, you discover work that wasn't in the original step plan:

- **Trivially in scope** (a one-line fix the step obviously requires): do it,
  mention in the devlog `summary`.
- **Adjacent work** (related but not strictly required): finish the current
  step normally, then flag the adjacent work in the devlog `summary` with a
  prefix like `Deferred:`. The next PLAN action will surface deferred items
  for scheduling. (`i2c state` has no `add-step` subcommand by design — step
  authoring is the PLAN action's responsibility, not EXECUTE's.)
- **Beyond this phase's scope:** hard stop. Emit `EXIT 2` with reason
  "scope expansion". Log via devlog entry `outcome: "escalate"`.

Do **not** silently absorb new work into the current step.

---

## Error escalation (during execute)

Three-strikes rule on the same problem:

1. First failure → diagnose and apply a targeted fix.
2. Same failure → try a fundamentally different approach.
3. Third failure → stop. Set `state=audit_escalation` via `i2c state`,
   append devlog entry with `outcome: "escalate"` and a summary of what
   you tried. Emit `EXIT 2`. Do not commit a half-working state.

Other escalation triggers fire from the Escalation Conditions section of your
Worker Contract (regime shift, unclear spec, all modules complete, etc.).

---

## Examples
<!-- assembler:omit_in_prompt -->

### Normal step completion (Build)

Step 11.3 in phase 11 (orchestrator). All tests pass. No contract changes.

```bash
# Edit the files. Do NOT run git — the runner commits after you exit.
i2c state complete steps.json --phase 11 --step 3
i2c state append devlog.jsonl '{"phase":11,"step":3,"action":"execute","outcome":"complete","summary":"Wired orchestrator slash commands through CodexClient. 7 new tests pass.","contracts":[],"timestamp":"2026-06-04T04:30:00Z"}'

# Last step of the phase? Transition:
i2c state set project.json state=review

# Emit exit signal (2-line block, see Worker Contract §4).
# The runner then commits your edits as "11.3: Wired orchestrator slash commands...".
```

### Step with an immediate-propagation contract change

Step 5.2 changes the signature of `EventStore.append` from
`append(event)` → `append(event, *, idempotency_key)`. Consumer module
`orchestrator` is already built.

This is **cross-module breakage** — hard stop. Do not commit the signature
change.

```bash
i2c state set project.json state=audit_escalation
i2c state append devlog.jsonl '{"phase":5,"step":2,"action":"execute","outcome":"escalate","summary":"Idempotency key on EventStore.append would break orchestrator (already built). Needs decision: bump consumer or pick non-breaking shape.","contracts":["ARCH_event_store.md"],"timestamp":"2026-06-04T04:45:00Z"}'

# Emit EXIT 2 with reason "contract change affects built module: orchestrator".
```

### Failed step — three strikes

Step 8.4 keeps failing the same flaky test across three tries.

```bash
i2c state set project.json state=audit_escalation
i2c state append devlog.jsonl '{"phase":8,"step":4,"action":"execute","outcome":"escalate","summary":"test_orchestrator_recovery fails on third attempt with same TimeoutError. Tried: (1) bumping timeout, (2) seeding deterministic clock, (3) running test in isolation. Pattern suggests deeper race in PatchManager.","timestamp":"2026-06-04T05:15:00Z"}'

# Do NOT mark the step complete. Do NOT commit a half-working fix.
# Emit EXIT 2 with reason "3 consecutive failures on step 8.4".
```

### Refine increment

Phase 14, message formatting Refine. Iteration 3 of N.

```bash
# Edit the files. Do NOT run git — the runner commits after you exit.
i2c state append devlog.jsonl '{"phase":14,"step":null,"action":"execute","outcome":"partial","summary":"Iteration 3: closed the parens-in-link bug. Still surfacing one MarkdownV2 edge case with nested code blocks.","timestamp":"2026-06-04T05:00:00Z"}'

# Time remaining, more iterations planned. Stay in execute, emit exit signal.
# The runner then commits your edit as "14: Iteration 3: closed the parens-in-link bug...".
```

---

## What this action does NOT do

- Plan new steps (that's PLAN action; only triggered between phases)
- Review the whole phase (REVIEW action; runs after all steps complete)
- Close the phase / propagate contract changes / promote gotchas
  (CLOSE action; runs after review)
- Read governance files — all needed context is in your assembled prompt
- Decide the next ACTION — the state machine does that after you exit
- Run `git` / commit — the runner commits your changes deterministically after you exit

Stay in your lane: do the step, record the result, exit.

---

## Known tooling gaps referenced above
<!-- assembler:omit_in_prompt -->

- **Mid-phase step append:** `i2c state` has no `append-step` subcommand.
  Adjacent work surfaces in the next PLAN action via `Deferred:` flag in
  devlog summary. Tracked as **FU-2** (`i2c fu`).

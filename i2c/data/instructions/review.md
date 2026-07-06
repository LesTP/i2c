# Review — End-of-Phase Code Review

Reviews all code added or changed during the current phase, applies the
fixes worth applying, logs the ones intentionally skipped, and transitions
to close. The state machine has already decided this action is appropriate
(all steps in the phase are `complete`); do not re-decide.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: REVIEW`. Pair this procedure with the `Worker Contract` section
in your prompt for loop/escalation/output rules, and inherit the devlog
conventions from `instructions/execute.md`. As in EXECUTE, the runner — not
you — **commits**; never run `git` to add/commit (read-only `git log` / `git
diff` for inspecting the phase's history is fine, and steps 2 and 3.5 use it).

---

## Procedure

### 1. Identify the phase under review

Read `project.json.phase` from the assembled `Project State` section. Filter
`steps.json` to the records for that phase — all should have
`status: "complete"`. If any are still `pending`, the state machine
mis-dispatched; **escalate** (`EXIT 2`, reason "review called with pending
steps"). Do not silently re-route to execute.

### 2. Identify what to read

The phase touched a set of files; the commit history records them. Build
the file list with `git`:

```bash
git log --no-pager --name-only --pretty=format: \
  $(git log --no-pager --pretty=%H -n 1 --grep="^$(printf '%d' "$PHASE"):")^.. \
  | sort -u
```

Or, more simply, list every commit that belongs to this phase by message
prefix (`N: ...` per the convention in `instructions/execute.md`) and read
the union of their changed files. If your shell makes the pipeline above
awkward, fall back to `git log --oneline | grep "^[a-f0-9]* $PHASE:"` and
read commits one at a time. The point is: review the *code that landed
this phase*, not the entire module.

Also assemble in your head: the module contract from the `Module Contract`
section of your prompt (`ARCH_<module>.md`) and the overall architecture
from the `Architecture` section. The review is "code vs. contract", not
"code vs. style guide".

### 3. Form findings — Must / Should / Optional

Read the phase's code. Note issues in three buckets:

| Bucket | What goes here | Examples |
|--------|----------------|----------|
| **Must** | Correctness bugs, architecture violations, contract drift, security issues, broken invariants | Tests passing by accident; a public method whose signature no longer matches the ARCH contract; an unchecked error path; a security regression |
| **Should** | Dead code, unused imports, duplications, simplification opportunities | A 30-line helper that one call site uses for a 5-line task; redundant null checks; copy-pasted blocks |
| **Optional** | Style, naming, minor structure | A variable named `tmp` that could be `events_to_retry`; comment polish; a helper that would be nicer split |

Two priorities, in order:

1. **Preserve existing functionality.** Do not refactor for elegance at the
   cost of breaking behavior the tests don't fully cover.
2. **Simplify and reduce code.** All else equal, less code wins.

If you find drift from `ARCH_<module>.md`, the review is over — that's a
**contract change** discovered after the fact. Stop the review, set
`state=audit_escalation` via `i2c state`, log the finding via devlog with
`outcome: "escalate"`, and `EXIT 2`. The human/wrapper reconciles
contract and code; restoring `state=review` resumes.

### 3.5. Acceptance-suite integrity check (Build phases)

If this phase ran a TESTS action, a frozen acceptance suite lives under
`tests/acceptance/phase_<N>/`, committed as `<phase>.tests: …` *before* the
EXECUTE commits. EXECUTE is prohibited from editing it (D-tests-4); verify that
held. Diff the acceptance dir against the TESTS commit:

```bash
git diff $(git log --pretty=%H --grep="^$PHASE\.tests:" -n 1) HEAD \
  -- tests/acceptance/phase_$PHASE/
```

- **No diff** → the suite is intact; nothing to do.
- **Any change** (weakened/removed/`xfail`ed assertions, deleted tests, loosened
  comparisons) → treat as a **Must** finding, unless the change is already
  justified by a decision record (`decisions.json`) with a rationale. An
  unjustified weakening of the oracle is a Must-fix: restore the acceptance test
  to its frozen form (fix the *implementation* instead), or, if the acceptance
  test was genuinely wrong, log a decision recording why the change is correct.

If no `tests/acceptance/phase_<N>/` dir exists, this phase had no TESTS action —
skip this step.

### 4. Apply Must fixes and Should fixes

For each Must finding and each Should finding, apply the fix. Run tests
after each fix or batch (your judgment on batching). Apply every Must
finding; skipped Shoulds log a decision per step 5.

**Do not commit — the runner does.** Leave your fixes in the working tree; do
**not** run `git`. After you exit, the deterministic runner commits the files
you changed (fenced off from any unrelated working-tree changes) as
`<phase>: <your review devlog summary>` — one phase-level commit for the review.
This removes the interactive-hang / wrong-scope / forgotten-commit hazards.

If a Must fix balloons in scope mid-fix
the bug needs an architecture change to address): stop, set
`state=audit_escalation` via `i2c state`, and **escalate** (`EXIT 2`,
reason "review surfaced architecture issue"). The fix becomes a new
phase or a contract change.

### 5. Log skipped Optional items as decisions

For each Optional finding you choose **not** to apply now, write a
decision record so the choice survives the session:

```bash
i2c state append-record decisions.json '{
  "id": "D-25",
  "phase": 11,
  "title": "Skip rename: tmp -> events_to_retry in event_loop",
  "status": "closed",
  "priority": "low",
  "decision": "Leave the local name `tmp` in event_loop.run() as-is.",
  "rationale": "Renames in this file should batch with the next pass over event_loop; isolated rename adds noise to git blame for low signal.",
  "revisit_if": "Next significant edit to event_loop touches this function."
}'
```

Decision ID convention: continue the project's `D-N` sequence (look at
the `Decisions` section of your prompt for the current high-water mark).

`phase: <current phase id>` — marks the skipped-Optional as belonging
to the phase being reviewed, so it appears in that phase's audit
(`--section phase-summary --phase N`). Read the current phase from
the `Project State` section of your prompt.

If you apply an Optional finding, no decision record needed.

### 6. Append a devlog entry for the review

One entry per REVIEW invocation. `action: "review"`, `step: null` (review
is phase-level), `outcome: "complete"` when the review finished. Summary
should record the finding counts.

```bash
i2c state append devlog.jsonl '{
  "phase": 11,
  "step": null,
  "action": "review",
  "outcome": "complete",
  "summary": "Phase 11 review: 0 Must, 2 Should (dead helper, redundant null check) applied, 1 Optional skipped (D-25). All tests pass after fixes.",
  "contracts": [],
  "timestamp": "2026-06-04T10:30:00Z"
}'
```

`outcome` choices for review:
- `complete` — review done, all Must/Should applied, decisions logged
- `partial` — out of budget mid-review; record what's left for the next
  REVIEW invocation (the state machine will redispatch if state stays
  `review`)
- `escalate` — Must fix exceeded scope, or contract drift surfaced; you
  emitted `EXIT 2`
- `failed` — tests failed after a fix and the fix can't be backed out
  cleanly; investigate before claiming complete

If review made contract changes that affect the module's `ARCH_*.md`,
list them in `contracts`. (This is rare in review — contracts usually
stabilize in plan/execute and are propagated in close.)

### 7. Transition state

Set `project.json.state=close`. The state machine will dispatch CLOSE next.

```bash
i2c state set project.json state=close
```

Then emit the exit signal (2-line block, see Worker Contract §4).

---

## Behavior modes
<!-- assembler:omit_in_prompt -->

The assembled prompt may include framing for **supervised mode** (when the
runner was invoked with `--mode supervised`). Under supervised mode, the
framing tells you to pause for approval before applying fixes. Under the
default (autonomous) mode, apply fixes without waiting. **You do not
choose the mode**; the prompt's framing reflects what the runner already
selected.

If you cannot tell from the prompt which mode is active, default to
autonomous behavior (apply fixes, log decisions, transition) and note
the ambiguity in the devlog summary. This is intentional: the assembler
adds explicit supervised framing when applicable; absence of that framing
means autonomous.

---

## What this action does NOT do

- Run phase-level cross-cutting tests beyond what your fixes touch
  (that's CLOSE, step 1)
- Promote learnings from devlog into gotchas (that's CLOSE)
- Propagate contract changes across `ARCH_*.md` files (that's CLOSE)
- Mark the phase complete in `phases.json` (that's CLOSE)
- Transition to `audit_boundary` (that's CLOSE's final write)
- Plan the next phase (that's PLAN of the next invocation after
  close + human audit)
- Add or rename steps in `steps.json` (steps are PLAN's responsibility;
  if review uncovered a missed step, escalate)
- Run `git` / commit — the runner commits your fix-ups deterministically
  after you exit

---

## Examples
<!-- assembler:omit_in_prompt -->

### Clean review — no findings

Phase 5, three steps complete, code looks good. Single devlog entry, no
fixes, no decisions.

```bash
i2c state append devlog.jsonl '{"phase":5,"step":null,"action":"review","outcome":"complete","summary":"Phase 5 review: 0 Must, 0 Should, 0 Optional. Code matches ARCH_event_store.md; no dead code; tests pass.","contracts":[],"timestamp":"2026-06-04T10:00:00Z"}'

i2c state set project.json state=close
# Emit exit signal.
```

### Review with Must + Should + skipped Optional

Phase 11, found one Must (unchecked error path), two Should (dead helper,
redundant null check), one Optional (variable rename). Applied Must and
Should; skipped Optional.

```bash
# Edit the files to apply the Must + Should fixes. Do NOT run git —
# the runner commits your fix-ups after you exit as "11: <review summary>".

# Log skipped Optional:
i2c state append-record decisions.json '{"id":"D-25","title":"Skip rename: tmp -> events_to_retry","status":"closed","priority":"low","decision":"Leave the local name as-is.","rationale":"Renames in this file should batch with the next pass; isolated rename adds noise to git blame.","revisit_if":"Next significant edit to event_loop touches this function."}'

# Devlog entry:
i2c state append devlog.jsonl '{"phase":11,"step":null,"action":"review","outcome":"complete","summary":"Phase 11 review: 1 Must (unchecked error path), 2 Should (dead helper, redundant null check) applied. 1 Optional (rename) skipped, D-25. Tests pass after fixes.","contracts":[],"timestamp":"2026-06-04T10:30:00Z"}'

i2c state set project.json state=close
```

### Review surfaces contract drift — escalate

Phase 8, found the orchestrator's actual `dispatch_action` signature
diverged from `ARCH_orchestrator.md` (now takes a kwarg the contract
doesn't list). Halt.

```bash
i2c state set project.json state=audit_escalation
i2c state append devlog.jsonl '{"phase":8,"step":null,"action":"review","outcome":"escalate","summary":"Review halted: dispatch_action in code takes idempotency_key kwarg, ARCH_orchestrator.md does not list it. Drift originated in step 8.3 — devlog there should have flagged contract change. Needs decision: align code to ARCH or update ARCH.","contracts":["ARCH_orchestrator.md"],"timestamp":"2026-06-04T10:45:00Z"}'

# Do NOT apply any fixes. Do NOT transition to close.
# Emit EXIT 2 with reason "review surfaced contract drift".
```

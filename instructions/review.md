# Review — End-of-Phase Code Review

Reviews all code added or changed during the current phase, applies the
fixes worth applying, logs the ones intentionally skipped, and transitions
to close. The state machine has already decided this action is appropriate
(all steps in the phase are `complete`); do not re-decide.

This file is assembled into the worker's prompt when the state machine emits
`ACTION: REVIEW`. Pair this procedure with the `Worker Contract` section
in your prompt for loop/escalation/output rules, and inherit the commit
and devlog conventions from `instructions/execute.md`.

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
**contract change** discovered after the fact. Stop the review, log the
finding via devlog with `outcome: "escalate"`, and `EXIT 2`. The next
PLAN action (or human) will reconcile contract and code.

### 4. Apply Must fixes and Should fixes

For each Must finding and each Should finding, apply the fix. Run tests
after each fix or batch (your judgment on batching). **Do not skip a Must
finding** — that's a hard rule. Skipping a Should requires logging the
skip decision in step 5.

Commit per logical batch of fixes (one Must fix, one Should refactor, or
one related set of cleanups). Commit message format: `phase: review — short title`.

```bash
git add <paths>
git commit -m "11: review — drop dead helper from event loop"
```

If a Must fix balloons in scope mid-fix (you start fixing a bug and find
the bug needs an architecture change to address): stop, **escalate**
(`EXIT 2`, reason "review surfaced architecture issue"). The fix becomes
a new phase or a contract change, not a sneaked refactor.

### 5. Log skipped Optional items as decisions

For each Optional finding you choose **not** to apply now, write a
decision record so the choice survives the session:

```bash
python3 tools/state.py append-record decisions.json '{
  "id": "D-25",
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

If you apply an Optional finding instead of skipping it, no decision
record is needed — the commit speaks for itself.

### 6. Append a devlog entry for the review

One entry per REVIEW invocation. `action: "review"`, `step: null` (review
is phase-level), `outcome: "complete"` when the review finished. Summary
should record the finding counts.

```bash
python3 tools/state.py append devlog.jsonl '{
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
python3 tools/state.py set project.json state=close
```

Then emit the exit signal (5-line block, see Worker Contract §6).

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
- Set `blocked: true` to gate on the human (that's CLOSE)
- Plan the next phase (that's PLAN of the next invocation after
  close + human audit)
- Add or rename steps in `steps.json` (steps are PLAN's responsibility;
  if review uncovered a missed step, escalate)

---

## Examples
<!-- assembler:omit_in_prompt -->

### Clean review — no findings

Phase 5, three steps complete, code looks good. Single devlog entry, no
fixes, no decisions.

```bash
python3 tools/state.py append devlog.jsonl '{"phase":5,"step":null,"action":"review","outcome":"complete","summary":"Phase 5 review: 0 Must, 0 Should, 0 Optional. Code matches ARCH_event_store.md; no dead code; tests pass.","contracts":[],"timestamp":"2026-06-04T10:00:00Z"}'

python3 tools/state.py set project.json state=close
# Emit exit signal.
```

### Review with Must + Should + skipped Optional

Phase 11, found one Must (unchecked error path), two Should (dead helper,
redundant null check), one Optional (variable rename). Applied Must and
Should; skipped Optional.

```bash
# Apply Must fix:
git add codexbot/orchestrator.py
git commit -m "11: review — check return path in dispatch_action"

# Apply Should fixes:
git add codexbot/event_loop.py
git commit -m "11: review — drop dead helper from event loop"

git add codexbot/orchestrator.py
git commit -m "11: review — remove redundant null check"

# Log skipped Optional:
python3 tools/state.py append-record decisions.json '{"id":"D-25","title":"Skip rename: tmp -> events_to_retry","status":"closed","priority":"low","decision":"Leave the local name as-is.","rationale":"Renames in this file should batch with the next pass; isolated rename adds noise to git blame.","revisit_if":"Next significant edit to event_loop touches this function."}'

# Devlog entry:
python3 tools/state.py append devlog.jsonl '{"phase":11,"step":null,"action":"review","outcome":"complete","summary":"Phase 11 review: 1 Must (unchecked error path), 2 Should (dead helper, redundant null check) applied. 1 Optional (rename) skipped, D-25. Tests pass after fixes.","contracts":[],"timestamp":"2026-06-04T10:30:00Z"}'

python3 tools/state.py set project.json state=close
```

### Review surfaces contract drift — escalate

Phase 8, found the orchestrator's actual `dispatch_action` signature
diverged from `ARCH_orchestrator.md` (now takes a kwarg the contract
doesn't list). Halt.

```bash
python3 tools/state.py append devlog.jsonl '{"phase":8,"step":null,"action":"review","outcome":"escalate","summary":"Review halted: dispatch_action in code takes idempotency_key kwarg, ARCH_orchestrator.md does not list it. Drift originated in step 8.3 — devlog there should have flagged contract change. Needs decision: align code to ARCH or update ARCH.","contracts":["ARCH_orchestrator.md"],"timestamp":"2026-06-04T10:45:00Z"}'

# Do NOT apply any fixes. Do NOT transition to close.
# Emit EXIT 2 with reason "review surfaced contract drift".
```

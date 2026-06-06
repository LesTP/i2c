# PILOT: clankercourts Phase 1 — Supervised Execution Debrief

> Phase 1 of the clankercourts project ran end-to-end in i2c supervised mode
> between 2026-06-05 (bootstrap) and 2026-06-06 (close). This is the first
> real production use of i2c. This doc captures what happened, what worked,
> what didn't, and what the data implies for Phase 3 (autonomous loop).

---

## TL;DR

- **Phase 1 shipped successfully.** 4 planned steps, 4 executed cleanly,
  30/30 tests pass, 12 commits. Worker contract held through the full
  plan → execute(×4) → review → close lifecycle without a single
  escalation.
- **Two real friction sources surfaced**, both with sharp implications
  for autonomous mode:
  1. **PowerShell `$`-interpolation ate JSON tokens** (`$defs`, `$refs`)
     when the worker quoted a multi-line payload via `state.py
     append-gotcha`. State was mangled but schema-valid — silent
     corruption. Required two follow-up commits to detect and fix.
     (FU-12 manifesting in production.)
  2. **Worker missed step 11 of `instructions/close.md`** — the initial
     close commit had `blocked: false`. Fixed in the same follow-up
     commit chain. In supervised mode the human caught both; in
     autonomous mode they would have silently rolled forward into a
     broken Phase 2.
- **No structural issues with the framework.** Assembler output is clean
  (~71 KB, 973 lines for `--action plan --phase 2 --mode supervised`),
  the slash commands worked, state writes were atomic, schema
  validation caught what it was supposed to catch. The two bugs above
  are tooling/discipline gaps, not design flaws.
- **Phase 3 design is informed, not blocked.** Two concrete additions:
  a `state.py append --from-file` payload path (FU-12 closer), and a
  post-close invariant assertion in the runner (`blocked == true` and
  current phase status changed).

---

## Phase 1 by the numbers

| Metric | Value |
|---|---|
| Worker actions performed | 7 (plan, exec ×4, review, close) |
| Commits in phase 1 | **12** (1 bootstrap + 1 plan + 4 exec + 1 review fix + 1 close + 2 close-follow-ups + 2 pre-phase) |
| Tests at phase end | 30/30 pass |
| Decisions logged in phase | 5 (D-8 through D-12) |
| Gotchas promoted | 4 (1 added as a 4th-commit recovery) |
| Open decisions remaining | 2 (D-5 transport, D-6 fog-of-war mirror — both pre-existing) |
| Escalations (EXIT 2) | 0 |
| Phase-level test runs needed | 1 (close step 2 passed first try) |
| Integration checks | 0 (leaf module, dep array empty — assembler correctly stripped the section per FU-5 resolution) |

Steps that were planned vs. what executed:

| Step | Planned title | Executed cleanly? | Notes |
|------|---------------|:-----------------:|-------|
| 1.1 | Package skeleton + pyproject + test harness | ✅ | 1 test passing |
| 1.2 | Structured logging_config module | ✅ | +7 tests (8 total); promoted PyYAML dep mid-step (logged in devlog summary, trivially in scope) |
| 1.3 | Map JSON schema | ✅ | +11 tests (19 total) |
| 1.4 | Example map + schema validation test | ✅ | +11 tests (30 total) |

No steps were dropped, deferred, or rescoped. The plan-to-execute fit was
clean.

---

## What worked

### Assembler in production

Confirmed against the real clankercourts project:

- `--section status` produces an accurate snapshot: phase, state, blocked,
  module, dependencies, current-phase steps with commit hashes, gotchas
  (all 4), last 3 devlog entries, open decisions. No data drift between
  what's in `.state/` and what the assembler emits.
- `--action plan --phase 2 --mode supervised` produces a ~71 KB / 973-line
  prompt with all four banners (WORKER CONTRACT, ACTION CONTEXT,
  PROJECT CONTEXT, TOOL RULES). Supervised stripping removed the Output
  Contract, Autonomous Behavioral Rules, and Next State subsections as
  specified. Action heading reframed to "Active Action: PLAN".
- Conditional `requires=dependencies_nonempty` mechanism worked
  end-to-end. Phase 1 is a leaf module (`dependencies: []`), so the
  Pre-plan Dependency Probe section was correctly stripped — the worker
  never saw it.

### State integrity

- All `state.py` writes were atomic. No corrupted JSON files, no
  validation failures (schema-valid throughout — see below for the
  one place this guarantee fell short of what we want).
- Schema validation caught zero spurious issues. Project ran 12 commits
  through 5 state files (project/phases/steps/decisions/devlog) without
  rejecting a single valid write.
- `update-record` (added in i2c phase 1.2.5) ran cleanly when the
  follow-up commits patched the mangled gotchas in place.

### Worker contract held

- 7 worker actions, 0 escalations. The three-strikes rule, scope
  expansion rule, and contract-change-affecting-built-module rule never
  fired (because the work didn't require them).
- One mid-step contract refinement: the worker discovered during step 1.2
  that the plan-time framing of Diplomat's logger as "JSON formatter" was
  inaccurate, paused, confirmed faithful text-format port with the
  operator, then proceeded. Recorded in the devlog summary for 1.2 and
  promoted as the third gotcha at close time. Exactly the loop the prose
  expects.
- Review action found 0 Must, 3 Should, 4 Optional. Applied 1 Should,
  retracted 1 (false alarm — good honesty signal), skipped 1 Should and
  all 4 Optionals as decisions (D-8..D-12). The decision IDs picked up
  correctly from the existing D-7 high-water mark.

### Decision discipline

D-7 (adopt i2c) was already in `decisions.json` before phase 1 started.
The worker added D-8 through D-12 during review and properly continued
the sequence. No collisions.

The skipped Should and the four Optionals were all logged as decisions
with `revisit_if` predicates — exactly the pattern `instructions/review.md`
prescribes.

---

## What went wrong

### Issue 1: PowerShell `$`-interpolation eating JSON tokens (FU-12)

**What happened.** During the close action, the worker ran:

```powershell
python tools/state.py append-gotcha .state/project.json "..."
```

with a multi-line gotcha string containing `$defs` and `$refs`. PowerShell's
double-quoted-string semantics interpret `$defs` and `$refs` as variable
references (which were empty), substituted them with empty strings, and
left the surrounding `\` intact. The persisted state read:

```
"JSON Schema \\ subschemas containing internal \\ to sibling defs cannot be validated standalone..."
```

instead of the intended `$defs ... $refs`. State was schema-valid (gotchas
is `array of strings`; the corrupted string is still a string), so neither
the assembler nor `state.py` flagged it. Caught by the human reading the
post-close transcript.

**Recovery cost.** Two follow-up commits (`e637695`, `7c0f060`):
- `e637695`: rewrote the two mangled strings via `update-record`-style
  fixes + corrected `blocked: false` → `true` (see Issue 2).
- `7c0f060`: added a 4th gotcha specifically about this PowerShell
  behaviour, so future workers don't hit it.

**Why it's worse in autonomous mode.** A loop runner has no operator
to spot the corruption. The state would be persisted, the worker would
exit normally, the next iteration would consume corrupted gotchas, and
the bug would compound across phases.

**Mitigation paths (in order of recommended priority):**

1. **Add `state.py append-gotcha --from-file <path>`.** Worker writes the
   gotcha to a temp file (where shell quoting doesn't apply), then passes
   the path. No `$` interpretation possible. Closes FU-12. Mirrors what
   we already do in the i2c repo's commit workflow (writing long commit
   messages to `.git/COMMIT_EDITMSG_*` and using `git commit -F`).
2. Same `--from-file` flag on `append`, `append-record`, `update-record` —
   any subcommand that takes a multi-line text payload.
3. Adapter `Tool Rules` note (in `CLAUDE.md` / `CODEX.md`) explicitly
   warning about PowerShell `$` semantics with explicit single-quote /
   `--from-file` recommendations. (Documentation-only mitigation.)

Recommended bundle: **(1) + (3)**. (2) is a natural extension that can
follow once (1) is shipped.

### Issue 2: Worker missed step 11 of `instructions/close.md`

**What happened.** The initial close commit (`587554f`) set
`project.json.blocked` to `false` (or rather, didn't touch it from the
prior `false`). Step 11 of `close.md` is explicit: "Set the gate" via
`state.py set project.json blocked=true`. The worker either skipped the
step or quietly conflated it with the `state` transition.

Caught when the human noticed the post-close status snapshot still showed
`Blocked: no` — which contradicts the contract that CLOSE always leaves
the project in `blocked: true`. Fixed in the same `e637695` follow-up
commit that addressed Issue 1.

**Why it's worse in autonomous mode.** The runner uses
`project.json.blocked` as the halt signal between phases. If a close
action leaves `blocked: false`, the runner would advance straight into
Phase 2 PLAN without human audit — silently violating the most important
invariant in the i2c lifecycle.

**Mitigation paths:**

1. **Runner-side post-close invariant check.** After every CLOSE
   invocation, the runner reads `project.json` and asserts
   `blocked == true` and the current phase's `status == "complete"`. If
   either fails: log to `summary.log`, halt the loop, do not advance.
   This is **runner-level enforcement** and doesn't require any change
   to `state.py` or the worker.
2. **CLOSE action devlog entry includes a structured `gate_set: bool`
   field.** Worker must include `gate_set: true` in the close devlog
   entry; runner reads the devlog and reconciles against `project.json`.
   More ceremonial; (1) is sufficient.
3. **Make `instructions/close.md` step 11 unmissable** — move it earlier
   in the procedure (before the commit), or fold it into the `state.py
   complete phases.json --phase $PHASE` op itself. Both are riskier
   changes (reorder = different mental model; conflated op = loss of
   composability).

Recommended: **(1)** alone, with (3) only if (1) detects this drift
repeatedly across the next several phases.

### Smaller observations (not bugs)

- The assembler's `--section status` doesn't render the **Budget** line
  for projects in `budget_type: "steps"` without an explicit
  `steps_remaining` field. clankercourts' `project.json` has
  `budget_type: "steps"` but no `steps_remaining` — so the line is
  silently omitted. Not wrong (per ARCH §8 the line is rendered when the
  field is present), but a stronger snapshot would render
  `**Budget:** steps (no remaining count)` so the operator sees the mode
  even when the runner hasn't populated the count. Minor; file as a new
  FU.
- The slash commands ended up at `.llms/commands/` with `i2c-` prefix
  (Devmate personal-commands convention) rather than the
  `.claude/commands/` template location. The frontmatter `name:` field
  still says the unprefixed value (`name: cold-start`). Works fine in
  practice but the templates README in i2c assumes the
  `.claude/commands/` location. Worth a templates README footnote.

---

## Implications for Phase 3 (autonomous loop)

The friction above translates directly into Phase 3 design requirements.
Listed in priority order:

### Must-have for Phase 3 v1

1. **`state.py append --from-file` (+ siblings).** Closes FU-12.
   Estimated work: ~30 LOC + tests in the existing `state.py`. No
   schema changes, no contract changes. Becomes the recommended path
   for any payload longer than one line.
2. **Post-close runner-side invariant check.** The runner's
   "after-worker-exit" logic asserts: (a) project.json validates against
   schema (already true via worker writes), (b) when action was CLOSE,
   `project.json.blocked == true` and the current phase's record has
   `status == "complete"`, (c) project.json.state matches what the state
   machine would dispatch next. If any fails: log, halt, surface to
   operator. This sits in `run-iteration.sh` (or the eventual Python
   runner) and complements `exit_signal.schema.json` validation.
3. **`state_machine.sh` covers the dispatch matrix.** Reads
   project.json + current phase record (from phases.json) + pending
   steps count (from steps.json) + budget; emits ACTION (plan / execute
   / review / close / exit) and NEXT. The plan in
   `i2c/FOLLOWUPS.md` Cold-start summary points at this.

### Nice-to-have for Phase 3 v1

4. **Exit-signal schema tightening (FU-7).** Once the runner is real and
   knows what fields it depends on, lock `additionalProperties: false`
   and define the required field set explicitly.
5. **`--next` flag on the assembler.** Today the assembler computes NEXT
   from a built-in state-transition table per D-impl-3. Phase 3 will
   typically know NEXT from the state-machine output and can pass it
   explicitly. The built-in table stays as a fallback for supervised use.

### Out of scope for Phase 3 v1 (defer)

- Multi-step mode in autonomous runs. v1 stays single-step (one ACTION
  per worker invocation). This dodges the LOOP / re-call-state-machine
  discipline entirely.
- Pre-action quoting validation. The `--from-file` path closes the
  highest-frequency case without requiring inspection of worker-emitted
  shell.
- Cross-platform shell abstraction. The runner's invocation surface
  to the worker is the prompt (text). Shell choice is the worker's
  problem; per-backend adapters can document.

---

## New follow-ups to add

The three items below should land in `FOLLOWUPS.md` once this debrief is
committed:

- **FU-21**: `state.py append-gotcha --from-file <path>` (and same flag
  on `append`, `append-record`, `update-record`) — closes FU-12 in
  practice. Triggered by this pilot. Phase 3.
- **FU-22**: Runner post-close invariant check — assert
  `blocked == true` and current phase status `complete` after every
  CLOSE; halt and surface on failure. Triggered by this pilot. Phase 3.
- **FU-23**: Assembler `--section status` should render `**Budget:**`
  even when neither `steps_remaining` nor `time_budget_seconds` is
  populated (when `budget_type` is set). Show the mode without the
  counter. Triggered by this pilot. Low priority — cosmetic.

The templates-README clarification about slash-command locations folds
into the existing FU-20 (which already covers Devmate command-path
behaviour discovered during the bootstrap session).

---

## What this pilot does NOT tell us

Honest limits on what one phase of a leaf bootstrap module can
demonstrate:

- **No conditional dep-probe was exercised.** Phase 1 had
  `dependencies: []`. The first true test of the conditional mechanism
  will be Phase 4 (`orchestrator`) once the resolver phases land.
  Tests cover this in `tests/test_assemble_context.py`, but production
  exercise is still pending.
- **No real integration check.** Same reason — close-time integration
  check only runs for non-leaf modules.
- **No multi-step worker.** All invocations were single-step. Loop
  discipline rules (WORKER_SPEC.md §2 multi-step subsection) and
  mid-step `--section` calls weren't exercised. Phase 3 will need to
  ship multi-step support before this gets a live test.
- **No Refine or Explore regime.** All planned phases are Build. Refine
  prompts, time-budget transitions, and the iteration-counter pattern
  (FU-9) won't get exercised until a Refine phase plans.
- **One operator, one project.** No cross-operator handoff,
  cross-project drift, or long-cold-resume scenarios tested.

---

## Recommended next session

1. Land the four new FU entries above into `i2c/FOLLOWUPS.md`.
2. Author Phase 3 plan covering: `state.py append --from-file` (FU-12 /
   FU-19), runner post-close invariant check (FU-20), `state_machine.sh`
   build, `run-iteration.sh` updates. Treat the build like the
   assembler implementation — incremental milestones, per-component
   tests, smoke-level autonomous loop against the clankercourts fixture.
3. Once Phase 3 ships, clankercourts Phase 2 (`resolver` data types) can
   be the first autonomous-loop run — a leaf-module Build phase against
   tooling the operator just lived with for 24 hours.

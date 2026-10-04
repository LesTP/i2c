# DESIGN: Action Contracts v1

**Status:** Accepted, v1 implemented - 2026-10-02 (increments 1 + 2; see §7.1
for what shipped and how the audit changed the draft).
**Scope:** new `i2c/contracts.py`; `run_iteration.py` (post-worker gate);
`invariants.py` (per-action checks folded in); new `i2c check` command;
`assemble_context.py` (render the contract into the prompt); per-action
`instructions/*.md`; goldens.
**Supersedes:** FU-74 (CLOSE runs the acceptance suite only if one exists), FU-75
(runner flags worker-created test files at CLOSE).
**Depends on:** FU-40 (the runner owns all commits), the pre/post dirty-path
snapshot in `run_iteration._worker_dirty_paths`, `invariants.py`, the state
machine.

---

## 0. Summary

Every lifecycle action (PLAN, TESTS, EXECUTE, REVIEW, CLOSE) gets a **contract**,
a single declarative record of:

- **what it may change** - an allowlist of paths. Everything else is denied
  (default-deny), and a global **protected set** overrides even broad
  allowlists;
- **what must be true when it claims success** - postconditions on `.state/`
  and the allowed next states.

The one contract table is consumed three ways, so the prompt, the worker's
self-check and the runner's gate can never disagree:

1. **Prompt** - the assembler renders the contract into the action's prompt.
2. **Self-check** - `i2c check` evaluates it, and the worker runs it before
   emitting `EXIT: 0`.
3. **Runner gate** - the runner evaluates it after the worker exits and before
   anything is committed. A failure means exit 2, no commit, and a summary
   naming the offending paths or unmet conditions.

`EXIT: 0` becomes a *claim* the runner verifies, not a fact it trusts.

---

## 1. Problem

### 1.1 Evidence - FU-70 (pidev-spike, 2026-10-02; archived at `p:\shared\pidev-samples\pidev-spike`)

The first end-to-end runs through the real runner on cheap OpenRouter models
(`pidev` backend) produced four worker failures in four iterations. The runner
caught only one of them:

| Iter | Action / model | What the worker did | Caught? |
|---|---|---|---|
| 1 | REVIEW / deepseek-chat | Printed a tool call as text, did nothing, no exit block | Yes - exit 2 (exit signal missing) |
| 2 | REVIEW / deepseek-chat | Real review, `EXIT: 0`, but **no devlog entry** (`review.md` step 6 requires one) | **No** |
| 3 | CLOSE / gpt-4o-mini | Closed phase 1 **and created a stub `tests/acceptance/phase_2/` suite** (`assert True`); the runner committed it (`da7b577`) | **No** - removed by hand (`4f5608e`) |
| 4 | PLAN / deepseek-chat | Wrote the six steps as JSON *in its reply*, never called `i2c state`, then emitted `EXIT: 0` - no steps, state still `plan` | **No** - reported as success |

Iteration 3 is the worst: it wrote into the folder where the *next* phase's
TESTS action freezes its oracle. The FU-43 digest is recorded at the `N.tests`
commit, so the stub would have been frozen into phase 2's oracle.

### 1.2 Findings in the code

- **Per-action checks exist but only CLOSE's run.** `invariants.py` has
  `_check_plan`, `_check_execute` and `_check_review`, but `run_iteration.py`
  calls `check_post_action` only for CLOSE (~L1131).
- **`_check_plan` is stale.** It requires `state in (execute, audit_escalation)`,
  which predates the `tests` action: a correct Build PLAN now sets
  `state=tests`. So it cannot simply be switched on; it would fail every
  correct Build PLAN.
- **No TESTS check exists**, and the existing checks look only at
  `project.json.state`, not at steps, devlog entries or files.
- **Nothing restricts which files an action may change.** EXECUTE, REVIEW and
  CLOSE all commit through `commit_execute`, which takes *every* non-`.state`
  path the worker newly dirtied (`_worker_dirty_paths(after) - pre_dirty`).
- **The instructions are uneven.** `execute.md` (L173) and `review.md` (L94)
  say what to do when `tests/acceptance/phase_<N>/` does not exist; `close.md`
  step 2 does not, which is the gap iteration 3 fell into.

### 1.3 Why a denylist is the wrong shape

FU-74 and FU-75 each patched one observed failure ("don't create acceptance
tests at CLOSE"). Weak models fail in open-ended ways, so a denylist only grows
after each incident. An allowlist states what each action is *for*; anything
outside it is a violation by default, including failures nobody has seen yet.

---

## 2. Goals / Non-goals

**Goals**
- Each lifecycle action has one declarative contract: write scope +
  postconditions + allowed next states.
- One source of truth, consumed by the prompt, `i2c check` and the runner gate.
- A false `EXIT: 0` (claimed success, conditions unmet) cannot reach a commit
  or advance the loop; `/batch` already halts on any non-zero exit.
- Writes outside an action's scope are never committed, and are reported.
- Deterministic and git-light: reuse the existing dirty-path snapshot and pure
  `.state/` reads; no new runtime dependency.

**Non-goals (v1)**
- Moving bookkeeping out of the worker (runner-owned state writes); see section 8.
- Sandboxing the worker's file access during the run. Backend tool limits are
  optional defense in depth (section 5.4), not the guarantee.
- Recovery actions (`diagnose`, `reconcile`), which are exempt in v1 as they are
  for the drift advisory.
- Per-step file plans for EXECUTE (flagging edits outside the files a step
  named). Deferred; see Q8.

---

## 3. The allowlist pattern

1. **Default-deny with two severities.** A path the worker changed (created,
   modified, deleted, renamed) is allowed only if it matches the action's
   `write_scope`. A **protected** path changed (see 2) **fails the iteration**.
   Any other path outside the scope is **flagged**: left uncommitted and
   reported, but the iteration still succeeds (§7.1: the fleet audit found a
   legitimate CLOSE writing build output).
2. **Protected set overrides.** A path in the protected set is denied even if a
   broad `write_scope` (EXECUTE/REVIEW `**`) matches it, unless the contract
   explicitly owns it (TESTS owns `tests/acceptance/phase_{phase}/**`).
3. **Noise is neither allowed nor denied.** Build by-products (`__pycache__/`,
   `*.pyc`, `.pytest_cache/`, `node_modules/`, ...) are ignored by the gate and
   **never committed**, even when a project's `.gitignore` misses them.
4. **`.state/` is not path-scoped.** Workers write it only through `i2c state`
   (schema-validated). The contract checks its *content* (postconditions);
   blocking hand edits is FU-71.
5. **Postconditions are checked against a baseline.** The runner snapshots
   `.state/` and the dirty-path set before invoking the worker, so a
   postcondition can say "exactly one step went from pending to complete *in
   this iteration*", not just describe the end state.

**Known limit:** the diff comes from `git status --porcelain`, so a write to a
gitignored path is invisible to the gate. That's acceptable, because ignored
paths are never committed (Q6).

---

## 4. Contract definition

### 4.1 Shape

A small, typed, static table in `i2c/contracts.py`. It's Python, not TOML,
because postconditions are named predicate functions, and keeping it static
keeps the assembler pure (provenance Tier 1).

```python
@dataclass(frozen=True)
class ActionContract:
    action: str                          # "plan" | "tests" | "execute" | "review" | "close"
    write_scope: tuple[str, ...]         # globs, project-relative; "{phase}" substituted
    owns: tuple[str, ...] = ()           # protected globs this action may write
    success_states: Mapping[str, tuple[str, ...]]   # regime -> allowed state on EXIT 0
    requires: tuple[str, ...] = ()       # named postconditions (registry below)

PROTECTED = (
    "tests/acceptance/**",               # the frozen oracles (D-tests-4)
    "i2c.toml",
    "CLAUDE.md", "CODEX.md", "PIDEV.md", # backend adapters
    ".git/**",
)
NOISE = ("**/__pycache__/**", "**/*.pyc", ".pytest_cache/**", "node_modules/**")
ESCALATE_STATE = "audit_escalation"
```

A postcondition is a pure function
`(before: StateSnapshot, after: StateSnapshot, phase: int) -> str | None`,
which returns a failure message or None. Registered by name, so the prompt can
render a human sentence for each and `i2c check` can report each by name.

### 4.2 The contracts (v1)

Phase `N` = `project.json.phase` at dispatch.

| Action | `write_scope` | `owns` | Success state (`EXIT: 0`) | `requires` |
|---|---|---|---|---|
| **PLAN** | *(none)* | - | Build: `tests`; Refine/Explore: `execute` | `phase_record_exists`, `build_phase_has_pending_steps`, `devlog_appended` |
| **TESTS** | `tests/acceptance/phase_{N}/**`, `tests/acceptance/__init__.py`, `tests/__init__.py` | `tests/acceptance/phase_{N}/**`, `tests/acceptance/__init__.py` | `execute`, or `tests` when partial | `acceptance_suite_nonempty` (only when moving to `execute`), `devlog_appended` |
| **EXECUTE** | `**` | - | `execute` if steps remain, else `review` | `one_step_completed` (Build/Explore with steps), `next_state_matches_remaining_steps`, `devlog_appended` |
| **REVIEW** | `**` | - | `close` | `devlog_appended` |
| **CLOSE** | `ARCHITECTURE.md`, `ARCH_*.md`, `PROJECT.md` | - | `audit_boundary` (no escalation state) | `phase_marked_complete`, `devlog_appended`, `acceptance_suite_unchanged` (existing FU-43 check) |

Every action except CLOSE may instead escalate: `EXIT: 2` with
`state=audit_escalation`. CLOSE stops with `EXIT: 2` and never sets an
escalation state (`close.md`; D-state-3). On escalation the success
postconditions are not required, but the write-scope check still applies, so
protected-path writes still fail and nothing out of scope is committed.

Notes:
- **EXECUTE/REVIEW** legitimately edit arbitrary source, so their allowlist is
  broad and the protected set does the real work. This is where a pure
  allowlist meets reality; per-step file plans could narrow it later (Q8).
- **`next_state_matches_remaining_steps`** makes EXECUTE's transition
  deterministic: the runner already knows how many steps remain (the state
  machine computes `NEXT` from it), so a worker setting `review` with steps
  pending, or `execute` with none left, is a violation.
- **REVIEW** is told not to send work back to EXECUTE (`review.md` L25), so
  `close` is its only success state.
- **The existing `_check_*` state checks are replaced** by `success_states`;
  `_check_plan`'s stale `execute` requirement goes away. The acceptance-digest
  check (`_check_acceptance_integrity`) stays and becomes CLOSE's
  `acceptance_suite_unchanged`.

---

## 5. Enforcement - one table, three consumers

### 5.1 Runner gate (the deterministic backstop)

Where: in `run_iteration`, after the exit signal is parsed and before every
runner commit (TESTS, EXECUTE, REVIEW fix-ups, CLOSE docs, and the `.state/`
tail). It replaces the CLOSE-only invariant block (~L1131).

1. **Baseline (before invoking the worker):** the existing `pre_dirty` set,
   plus a `StateSnapshot` of `project.json`, `phases.json`, `steps.json` and
   the devlog line count.
2. **After the worker:**
   - `changed = _worker_dirty_paths(root) - pre_dirty`, minus `NOISE`.
   - Classify each path: allowed / protected / out of scope.
   - Evaluate `success_states` and `requires` against the before/after
     snapshots.
3. **Outcomes:**

| Worker said | Contract result | Runner does |
|---|---|---|
| `EXIT: 0` | all pass | commit as today; flagged and noise paths excluded |
| `EXIT: 0` | a postcondition or success state fails | **exit 2**, no commit; reason `post-<ACTION> invariants failed: <failures>` |
| any | protected path changed | **exit 2**, no commit; reason lists the paths |
| any | only flagged (out-of-scope) paths | worker's exit code stands; flagged paths left uncommitted + NOTE |
| `EXIT: 2` | paths ok | exit 2 as today |

4. **No auto-revert and no lifecycle mutation** (D-ac-3). Offending files stay
   in the working tree for a human. The next iteration's `pre_dirty` fences
   them off, so they are never swept into a later commit either. The runner
   doesn't touch `project.json`: a false-success PLAN leaves `state=plan`, so a
   rerun redoes PLAN, and `/batch` halts on the exit 2.
5. **Noise** is excluded from every runner commit, including the existing
   EXECUTE and REVIEW paths.
6. **Telemetry:** the row records `contract_violation` (the blocking failure
   messages, else null), an additive nullable field like `drift_flag`.

### 5.2 Worker self-check - `i2c check`

`i2c check [--action A] [--json]` evaluates the same contract and prints one
line per failed condition, or `OK`.

- The runner writes the baseline snapshot to
  `logs/loop/iteration_NNN_baseline.json` and passes its path in an env var
  (e.g. `I2C_CHECK_BASELINE`). That gives `i2c check` the same before/after
  view the runner gate will use.
- Without a baseline (supervised mode), it checks only end-state conditions
  (success state, phase has pending steps, suite non-empty, phase complete)
  and skips iteration-relative ones (Q5).
- Every action's instructions gain a final step: *run `i2c check`; if it
  reports failures, fix them; if you cannot, escalate with `EXIT: 2`.*

This is the upstream guardrail. A weak model gets deterministic feedback while
it can still act on it. FU-70 iteration 4 would have seen
`phase_has_pending_steps: no pending steps recorded for phase 2` and could have
recorded them.

### 5.3 Prompt rendering

The assembler renders an `## Action Contract` section from the table into each
action's prompt, next to the action context:

> **You may change:** `tests/acceptance/phase_2/**` - nothing else outside
> `.state/`. **You must not change:** the protected paths
> (`tests/acceptance/**` except this phase's folder, `i2c.toml`, adapters).
> **Before `EXIT: 0`, all of these must be true** (verify with `i2c check`):
> the suite folder contains at least one test; a `tests` devlog entry was
> appended; `project.json.state` is `execute`.

- It's generated, not hand-written, so the prompt can't drift from the gate.
- It's static per (action, regime, phase), so the assembler stays pure and the
  goldens are deterministic (`{phase}` substitution only).
- The hand-written "What this action does NOT do" lists stay as prose context
  but are no longer the guarantee. `close.md` step 2 gets the conditional
  wording `execute.md` and `review.md` already use (FU-74's content).

### 5.4 Backend tool limits (optional, defense in depth)

Where a backend CLI supports it, map `write_scope = ()` actions (PLAN) to a
read-only or no-edit tool mode, and narrow-scope actions to whatever the CLI
can express (claude tool permissions, codex sandbox modes; pi.dev unverified).
It's uneven across backends, so it's never the guarantee; the runner gate is.

---

## 6. Decisions

```
D-ac-1: Writes are default-deny per action (allowlist); a global protected set
        overrides broad allowlists; an action may write a protected path only
        if its contract owns it. Protected writes fail the iteration; other
        out-of-scope writes are flagged (uncommitted + reported).
Status: Accepted (2026-10-02)
Rationale: weak models fail in open-ended ways; a denylist only grows after
each incident (FU-74/75 were two such patches). The fleet audit (7.1) found
one legitimate out-of-scope CLOSE write (build-a-stew dist/), so blocking
every out-of-scope path would have halted a real project.
```
```
D-ac-2: One contract table is the single source for the prompt section,
        `i2c check`, and the runner gate.
Status: Accepted (2026-10-02)
Rationale: hand-written instructions and enforcement drift apart; generating
both from one table makes drift impossible.
```
```
D-ac-3: A contract failure means exit 2, no commit, no auto-revert, and no
        lifecycle-state mutation by the runner.
Status: Accepted (2026-10-02)
Rationale: matches today's CLOSE-invariant behavior; auto-revert can destroy
useful work; leaving the state lets a rerun redo the action and /batch halt.
```
```
D-ac-4: EXIT 0 is a claim. The runner verifies success states and
        postconditions for every lifecycle action, not only CLOSE.
Status: Accepted (2026-10-02)
Rationale: FU-70 iterations 2-4 - three false or incomplete successes passed.
```
```
D-ac-5: Build by-products (NOISE) are never committed and never count as
        violations, regardless of the project's .gitignore.
Status: Accepted (2026-10-02)
```
```
D-ac-6: Recovery actions (diagnose / reconcile) are exempt in v1.
Status: Accepted (2026-10-02)
```
```
D-ac-7: Runner-owned bookkeeping (structured worker results) is deferred to v2,
        decided on data from v1.
Status: Accepted (2026-10-02)
```

---

## 7. Implementation plan

### 7.1 What shipped (2026-10-02) and what the audit changed

**History audit** (read-only, 174 successful autonomous iterations across
diplomat, clankercourts, build-a-stew, masorah, toolkit, pidev-spike; files
committed in each iteration's `start_commit..end_commit`, outside `.state/`):

- PLAN committed nothing outside `.state/` (Q1 resolved: empty scope).
- TESTS also creates `tests/__init__.py` and `tests/acceptance/__init__.py`
  (diplomat, masorah) -> added to its scope.
- CLOSE wrote `ARCHITECTURE.md` / `ARCH_*.md`, plus one build-a-stew CLOSE that
  committed `dist/` build output -> led to the **flag, don't block** severity
  for non-protected out-of-scope paths (Q2 resolved without widening the scope).
- EXECUTE/REVIEW edits to frozen acceptance suites appear only in build-a-stew
  (the known oracle-correction incidents, which since 2026-07-28 require a
  human) -> protected, blocking.
- Every successful action wrote its own devlog entry, except exactly the two
  FU-70 failures (Q3 resolved: required for all five).
- No EXECUTE commit spanned more than one step; every successful REVIEW was
  followed by CLOSE -> `one_step_completed` and REVIEW's `close`-only end
  state are safe.

**Shipped:** `i2c/contracts.py` (table, protected set, noise, postconditions,
classifier, prompt renderer); `invariants.check_iteration` (the gate) with
`check_post_action` now taking end states from the table (fixes the stale
`_check_plan`, adds TESTS); the runner gate before every commit (via the
stubbable `run_iteration.check_contract`), the baseline file +
`$I2C_CHECK_BASELINE`, and noise/flagged exclusion; `i2c check`; the prompt
`## Action Contract` section and contract-derived `## Next State` (the old
table showed `execute` after Build PLAN and `plan` after CLOSE); a final
self-check line in all five instruction files and FU-74's conditional wording
in `close.md` step 2; telemetry `contract_violation`; `tests/test_contracts.py`
replaying FU-70 iterations 2-4. Not shipped: increment 3 (backend tool limits)
and v2.

### 7.2 Original plan

**Before increment 1: audit history to calibrate the scopes.** Check every
runner-made TESTS and CLOSE commit (and a sample of EXECUTE) in diplomat and
clankercourts against the proposed `write_scope`s. Any legitimate write that
would have been blocked (e.g. CLOSE touching README/CHANGELOG/docs) either
widens the scope or is a finding. This replaces a feature flag: the shared
`i2c` checkout is used by every project, so the gate must not start halting
real runs on day one.

**Increment 1 - contracts + runner gate.**
- `i2c/contracts.py`: table, protected set, noise, postcondition registry, path
  classifier.
- `run_iteration.py`: baseline snapshot; gate before every commit; noise
  excluded from commits; telemetry field.
- `invariants.py`: per-action state checks replaced by the contract; the
  acceptance-digest check kept.
- Telemetry schema: additive nullable `contract_violation`.
- Tests: table-driven path classification per action; postcondition unit
  tests; runner integration reproducing FU-70 iterations 2-4 with fake
  invokers (each must end exit 2 with no commit); existing suites green.

**Increment 2 - prompt + self-check.**
- `i2c check` (+ baseline file + env var).
- Assembler `## Action Contract` section; final self-check step in every
  action's instructions; the `close.md` step 2 conditional; goldens
  regenerated.
- Re-run the weak-model loop on the `examples/bench_calc/` fixture to compare
  cheap-model pass rates before/after.

**Increment 3 (optional) - backend tool limits** (section 5.4).

**v2 (separate memo) - runner-owned bookkeeping** (section 8).

---

## 8. Looking ahead - v2: take bookkeeping away from the model

The most upstream, most deterministic guardrail is not to ask the model to do
bookkeeping at all. The worker returns a structured result (PLAN: the steps;
EXECUTE: the step id + summary; REVIEW/CLOSE: summary + findings), and the
runner validates it against a schema and performs the `.state/` writes and the
state transition itself. Same direction as FU-40 (commits) and FU-69
(timestamps).

FU-70 iteration 4 is direct evidence: deepseek's reply contained a correct
six-step JSON list; only the `i2c state` calls were missing. A runner parsing a
result block would have advanced the phase.

Cost: the largest change here - every instruction file, the adapters, the
goldens, supervised mode, and a result format the model must still produce
(though a malformed result becomes an explained rejection, not a silent no-op).
Decide after v1 shows how often contract failures still happen with the
self-check in place.

---

## 9. Open questions

- **Q1** ~~Does PLAN ever legitimately write outside `.state/`?~~ Resolved: no
  (§7.1 audit).
- **Q2** ~~CLOSE scope: README / CHANGELOG / `docs/**`?~~ Resolved: not seen in
  the fleet; the one out-of-scope CLOSE write (build output) is handled by the
  flag severity rather than a wider scope.
- **Q3** ~~Is a PLAN devlog entry required on success?~~ Resolved: yes, for all
  five actions (every successful fleet iteration already wrote one).
- **Q4** ~~TESTS `partial`~~ Resolved: `tests.md` says partial leaves
  `state=tests` with `EXIT: 0`; `tests` is a success state and the non-empty
  suite check applies only when moving to `execute`.
- **Q5** Supervised mode: which postconditions can `i2c check` evaluate without
  a runner baseline?
- **Q6** Writes to gitignored paths are invisible to the gate. Acceptable
  (never committed), or should the gate also scan `--ignored` within the
  write scope?
- **Q7** Should a contract failure set `audit_escalation`, so the bot and
  dashboard show it as an escalation, rather than leave the state for a rerun
  (D-ac-3)?
- **Q8** EXECUTE per-step file plans: should PLAN declare files per step, so
  edits outside them are *flagged* (warn, not block)?

---

## 10. References

- FU-70 evidence: pidev-spike iterations 1-4 (`logs/loop/`), commits `da7b577`
  (CLOSE stub suite) and `4f5608e` (manual removal), archived with full git
  history at `p:\shared\pidev-samples\pidev-spike` (index:
  `p:\shared\pidev-samples\ARCHIVE.md`).
- `i2c/invariants.py` (`check_post_action`, `_check_*`,
  `_check_acceptance_integrity`); `i2c/run_iteration.py` (`_worker_dirty_paths`,
  `commit_execute`, CLOSE invariant block); `i2c/state_machine.py` (`decide`).
- FU-40 (runner-owned commits), FU-43 / D-tests-4 (frozen-oracle digest),
  FU-69 (CLI-owned timestamps), FU-71 (no hand-edit `.state/`), FU-74 / FU-75
  (superseded).
- `DESIGN_provenance_v1.md` (assembler purity, Tier 1),
  `DESIGN_benchmark_v1.md` (cheap-model routing, the reason weak workers matter).

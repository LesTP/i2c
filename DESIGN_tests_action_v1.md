# DESIGN — `tests` Action (test/impl separation) v1

> **Status:** Draft / proposed (spec only; no code). Adds a regime-conditional,
> phase-level acceptance-test authoring step so the implementation is graded
> against tests it did not write — turning the increment-2 `tests_pass` field
> from a self-graded signal into a real oracle. Decisions tagged D-tests-*.
>
> **One-line goal:** freeze a contract-derived acceptance suite **before**
> EXECUTE and **independently of** it, so "did the implementation work" becomes
> objectively answerable (for the benchmark, and as a quality gate).

---

## 1. Motivation

i2c's EXECUTE worker writes implementation **and its own tests in the same
commit**. So "tests pass" is self-graded — a weak model can write weak tests and
look successful. `DESIGN_benchmark_v1.md` §5 calls this the oracle-contamination
problem; `DESIGN_telemetry_v1.md` §6 ships `tests_pass` with an explicit
self-grading caveat.

The fix is test/impl separation: author the acceptance suite **separately from
and before** the implementation. Three benefits, all pulling the same way:

1. **Oracle** — replaying an EXECUTE step against a *frozen* acceptance suite it
   didn't author is a clean measurement (the linchpin the whole benchmark rests
   on).
2. **Quality** — test-first yields better-specified, more testable code.
3. **Routing** — test authoring becomes its own dispatched unit, so it can run
   on its own backend (`[run.backends].tests`) and gets its own telemetry rows.

This is Option A from the design discussion: a new dispatched `tests` action,
**phase-level**, **conditional on the Build regime**.

---

## 2. What "separation" actually requires (keep two notions distinct)

- **The oracle** needs the acceptance tests in a **separate, earlier commit**
  than the implementation, **not authored by** the implementing step.
- **Routing** needs `tests` to be a **separately dispatched** action (its own
  loop call) so backend resolution can pick a different model.

A new dispatched state delivers both. (A mere "conditional section inside PLAN" —
like today's `probe`/`integration_check`, which are logged as distinct actions
but run inside one PLAN/CLOSE invocation on one backend — would give the oracle
but **not** an independent backend. Hence a real state.)

---

## 3. ⚠️ Ordering decision (needs sign-off) — D-tests-1

You asked for `tests → plan → execute` (tests *before* plan). There's a
chicken-and-egg: **PLAN is what chooses the regime and creates the phase
record.** A pre-PLAN `tests` state therefore has (a) no regime to gate Build-only
conditionality on, and (b) no `phases.json` record to attach to (telemetry's
`phase_meta` would be null; `dependency-probe` has nothing to hang on).

**Primary design (recommended): `plan → tests → execute → review → close`.**
PLAN chooses the regime, writes the phase record, decomposes steps, then routes
to `tests` (Build) or straight to `execute` (Refine/Explore). This keeps the
bootstrap and phase-record creation exactly as today and preserves the full
oracle property — the acceptance suite is still **frozen before EXECUTE** and
**not authored by EXECUTE**. The only concession vs. literal "before plan": the
TESTS worker sees the planned step list. The `instructions/tests.md` contract
counters this by directing it to write **contract-level acceptance tests from
ARCH_\<module\>.md**, deliberately decomposition-independent.

**Literal `tests → plan` variant (more invasive).** To honor pre-plan ordering,
regime + phase-record creation must move out of PLAN and into the **phase-advance
gate** (sourced from `ARCHITECTURE.md`'s implementation-sequence regimes): the
advance writes the `phases.json` record and sets `state=tests` (Build) or
`state=plan` (else); PLAN shrinks to "decompose into steps only." This is cleaner
*conceptually* (regime comes from the architecture, not re-decided each PLAN) but
touches the advance protocol, the bootstrap (phase 1 is born from the phase-0
PLAN today), and PLAN's contract. Deferred unless you want it.

**The rest of this spec assumes the primary design.** Flip D-tests-1 to switch.

---

## 4. Lifecycle

```
Build phase:            plan → tests → execute (×N) → review → close → [gate]
Refine / Explore phase: plan →         execute/time-budget → review → close → [gate]
```

- **PLAN** (unchanged responsibilities) now sets the next state to **`tests`**
  when `regime == "build"`, else `execute` as today.
- **TESTS** (new): reads the ARCH contract, writes a **phase-level acceptance
  suite** (expressing the phase's observable success criteria), commits it as its
  own commit, logs a devlog `tests` record, and transitions to **`execute`**.
  The suite is expected to be **red** (no/partial implementation yet).
- **EXECUTE** (amended): implements steps to make the acceptance suite **green**.
  May still write its own fine-grained unit tests — those are *not* the oracle.
  **Must not modify the frozen acceptance suite** (§7, integrity rule).
- **REVIEW / CLOSE** (essentially unchanged): CLOSE's phase-level test run
  includes confirming the acceptance suite is green.

### Lifecycle-state table addition

| State | Meaning | Next dispatch | Recovery write (when halted) |
|-------|---------|---------------|------------------------------|
| `tests` | Next action is TESTS (Build only) | TESTS | `set state=execute` (resume after authoring) |

TESTS transitions to `execute` on success and to `audit_escalation` on
escalation (same pattern as EXECUTE/REVIEW).

---

## 5. The phase-level acceptance suite

**Granularity (D-tests-2, chosen): phase-level.** One acceptance suite per Build
phase, derived from the module contract — not per-step test/impl pairs. This
matches the benchmark's per-phase clean oracle (`DESIGN_benchmark_v1.md` §5), is
cheaper (one extra loop call per phase, not per step), and keeps tests
decomposition-independent.

**Identification (D-tests-3, open).** The oracle and the benchmark must know
*which* tests are the frozen acceptance suite vs. EXECUTE's incidental unit
tests. Options:

- **(a) Path convention** — acceptance tests live under a known dir, e.g.
  `tests/acceptance/phase_<N>/` (recommended: simplest; the oracle runs that dir,
  the integrity check watches it).
- **(b) Marker** — a pytest marker / naming convention (`test_acc_*`).
- **(c) Recorded path** — TESTS records the suite path(s) in the phase record or
  a sidecar; the oracle reads it.

Recommend **(a)**; it makes both the `tests_pass` oracle target and the §7
integrity check trivial. The bundled `instructions/tests.md` codifies the
convention; projects can override.

**"Red" caveat.** Phases build on prior phases, so a fresh acceptance suite may
be *partially* green already (shared infra). TESTS writes tests for *this
phase's* new contract surface; partial-red is expected and fine.

---

## 6. Oracle / telemetry connection

This is the payoff. With a `tests` action:

- The benchmark replays an EXECUTE step against the **TESTS commit's** acceptance
  suite — tests the EXECUTE model didn't author → clean per-phase oracle.
- The increment-2 `tests_pass` field becomes meaningful: scope the
  `[telemetry].test_cmd` (or a new default) to the **acceptance suite** so
  `tests_pass` grades the contract, not self-written unit tests.
- `tests` gets its **own telemetry rows** (`action: "tests"`), letting the
  benchmark measure test-authoring difficulty separately from implementation.

---

## 7. Oracle integrity rule (D-tests-4) — important

If EXECUTE can edit the acceptance suite, a weak model can "pass" by weakening
the oracle. **EXECUTE must not modify the frozen acceptance suite.** Enforcement
options (recommend the first two together):

- **REVIEW check** — REVIEW flags any change to the acceptance suite since the
  TESTS commit; unjustified weakening is a Must-fix.
- **CLOSE invariant** — a post-CLOSE invariant (alongside the existing FU-22
  checks) verifies the acceptance suite path was not modified after the TESTS
  commit, or that any change is logged as a decision with rationale.
- (Deferred) a pre-commit guard in EXECUTE.

Legitimate cases exist (a genuinely wrong acceptance test). The rule is *not*
"never change," it's "changes are surfaced and justified," consistent with i2c's
decision-logging culture.

---

## 8. Surface to change (primary design) — verified against code 2026-06-30

**Schemas** (all additive enum values → backward-compatible):
- `i2c/data/schemas/project.schema.json` — add `"tests"` to the `state` enum
  **and** update the `state` field's description text (it enumerates "plan|
  execute|review|close dispatch the matching ACTION").
- `i2c/data/schemas/devlog_entry.schema.json` — add `"tests"` to `action` enum.
- `i2c/data/schemas/telemetry_entry.schema.json` — add `"tests"` to `action` enum.

**State machine** (`i2c/state_machine.py`) — three edits, plus one nuance:
- `VALID_STATES` tuple `+= "tests"`.
- `decide()` — add `state == "tests"` → `("TESTS", "execute")`.
- Module-docstring decision matrix + the `ACTION:`/`NEXT:` header enum lines.
- ⚠️ **Routing nuance (D-tests-1a):** `decide()` does **not** route `plan→tests`,
  because at plan-dispatch the upcoming phase's record (and thus its `regime`)
  doesn't exist yet — **PLAN creates it**. The `plan→tests` hop is done by the
  **PLAN worker writing `state=tests`** for Build (an `instructions/plan.md`
  change), not by `decide()`. `NEXT` is advisory only — the worker owns the real
  `i2c state set`, and the runner uses `NEXT` solely for the EXIT summary line —
  so `decide()`'s `plan→execute` nominal `NEXT` is fine to leave. The
  `tests→execute` hop *is* in `decide()`.

**Runner** (`i2c/run_iteration.py`) — **no change**. Dispatch is action-agnostic:
it assembles `--action tests`, invokes the per-action backend, parses the
standard 2-line exit signal, and writes a telemetry row with `action="tests"`.
The only action-specific branch is the CLOSE invariant check, which TESTS doesn't
touch. (A useful confirmation the design fits the existing seam.)

**Instructions** (package data, project-overridable; resolve via `resolve_asset`):
- `i2c/data/instructions/tests.md` — **new**: contract → phase-level acceptance
  suite → commit → set `state=execute`. Mark `autonomous_only` sections as the
  other instruction files do.
- `i2c/data/instructions/plan.md` — PLAN sets `state=tests` for Build, else
  `execute`.
- `i2c/data/instructions/execute.md` — implement against the frozen acceptance
  suite; **do not edit it** (§7).
- `i2c/data/instructions/review.md` + `close.md` — acceptance-suite integrity
  check (§7).

**Assembler** (`i2c/assemble_context.py`):
- `ACTIONS` tuple `+= "tests"` (currently
  `("plan","execute","review","close","diagnose","reconcile")`). This also
  auto-adds `instructions/tests.md` to `scaffold.EJECTABLE`.
- Add a per-action **assembly recipe** for `tests` (the "Action recipes"
  section) selecting the sections the TESTS prompt needs (WORKER_SPEC contract,
  `instructions/tests.md`, ARCH contract/module, project state, devlog tail).
  `instructions/tests.md` resolves automatically once the file exists.

**Config** (`i2c/config.py`): add `"tests"` to `_RUN_ACTIONS` so
`[run.backends].tests` validates.

**Worker contract / adapters:**
- `i2c/data/WORKER_SPEC.md` — add a `TESTS` row to the ACTION table and to the
  `multi_step` pseudo-code action list.
- `i2c/data/adapters/{claude,codex}.md` — **likely no change** (they don't
  enumerate the lifecycle actions; `PLAN`/`REVIEW` appear only as examples).
  Verify; update only if an enumerated list is present.

**Golden prompts** (`tests/`):
- `tests/test_prompt_golden.py` — add `"tests"` to its local `ACTIONS` tuple.
- Regenerate with `I2C_REGEN_GOLDEN=1` **after** `instructions/tests.md` exists →
  creates `prompt_tests_{claude,codex}_{autonomous,supervised}.md` (4 files). The
  FU-35 split golden locks only `execute`, so no `prefix_/body_` tests files are
  required.

**Integrity (if enforced in CLOSE, D-tests-4):**
- `i2c/invariants.py` — add an acceptance-suite-unchanged-since-TESTS check to
  the post-CLOSE invariants.

**Docs:**
- `README.md` — "The four worker actions" → five (heading, table, and the
  at-a-glance lifecycle line).
- `WORKFLOW.md` — dispatch-flow diagrams.
- `DECISIONS.md` — index the `D-tests-*` decisions, pointing here.

---

## 9. Routing & backend (D-tests-6)

`[run.backends].tests` selects the TESTS backend independently. **Default it to a
capable tier, not the cheapest** — a bad acceptance suite has the same high blast
radius as a bad PLAN/REVIEW (it poisons every EXECUTE via false failures). Per
`DESIGN_benchmark_v1.md` §4.1, TESTS belongs in the "pin to good tier" bucket.
Whether a cheaper model is safe for test authoring is a question for the
benchmark to answer with real `action=tests` data — **do not pre-optimize.**

---

## 10. Migration (D-tests-7)

All schema edits are **additive enum values**, so existing `.state/` files keep
validating and existing projects simply never enter `tests`. No data transform is
required.

The reason to **bump `CURRENT_SCHEMA_VERSION`** is **forward-compat**, not
traceability: once a project writes `state="tests"`, an *older* i2c (whose
`VALID_STATES` lacks it) would error on that `.state/`. Stamping a new version
lets `i2c migrate`'s existing guard — *".state targets a newer schema than the
installed i2c → exit 2 (upgrade i2c)"* — catch that cleanly instead of a raw
state-machine crash. The migration transform itself is a **no-op** (it only
stamps the version). Cost: existing projects report "migration needed" under
`i2c migrate --check` until they run the no-op `i2c migrate`. Recommend the bump
for the guard.

---

## 11. Worked flow (Build phase, primary design)

1. Operator advances to phase N (or phase-0 PLAN births phase 1).
2. **PLAN** — regime=build, writes the phase record, decomposes steps, probes
   deps if non-leaf, sets `state=tests`.
3. **TESTS** — reads `ARCH_<module>.md`, writes `tests/acceptance/phase_N/…`
   (red), commits `N.tests: …`, devlog `{action: tests}`, sets `state=execute`.
4. **EXECUTE** (×N) — implements steps to green; own unit tests allowed; does not
   touch the acceptance suite; commits per step.
5. **REVIEW** — findings + acceptance-suite-integrity check.
6. **CLOSE** — phase tests incl. acceptance suite green; sets `audit_boundary`.
7. Benchmark later replays step 4 against step 3's commit → clean oracle.

---

## 12. Open questions

- **Q-tests-1 (ordering, D-tests-1):** primary `plan → tests → execute`, or the
  literal `tests → plan` via regime-at-advance? (Spec assumes primary.)
- **Q-tests-2 (suite identification, D-tests-3):** path convention
  (`tests/acceptance/phase_<N>/`) vs marker vs recorded path. (Lean path.)
- **Q-tests-3:** does the `tests_pass` oracle run *only* the acceptance suite, or
  the whole suite? (Lean: acceptance suite for the oracle; whole suite at CLOSE.)
- **Q-tests-4:** integrity enforcement — REVIEW-only, or also a CLOSE invariant?
  (Lean both.)
- **Q-tests-5:** should `tests` ever apply to Refine (e.g. regression tests for a
  refactor)? (Lean: no in v1; Build-only, D-tests-5.)
- **Q-tests-6:** version bump vs leave-unchanged (D-tests-7).

---

## 13. Relationship to existing work

- **Upgrades** `DESIGN_telemetry_v1.md` `tests_pass` from self-graded to a real
  oracle; adds `action=tests` telemetry rows.
- **Unblocks** the clean per-phase oracle in `DESIGN_benchmark_v1.md` §5.
- **Mirrors** existing conditional-action precedent (`probe`,
  `integration_check`) — but as a *dispatched state* because an independent
  backend is wanted (§2).
- **Routing** plugs into the increment-2 pricing/telemetry and the FU-38 backend
  set without new mechanism.
- Noted in `FOLLOWUPS.md` Active Roadmap §7 ("Move tests into a separate step?").
```

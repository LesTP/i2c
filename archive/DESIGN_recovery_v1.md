# DESIGN — Recovery v1 (reconcile-first)

> **Status: built (v1), archived.** This is the recovery v1 design *as
> implemented* (graduated from [`FUTURE_recovery.md`](../FUTURE_recovery.md)).
> The Phase-0 empirical sweep that grounds it is in the Appendix below. The
> current "what" lives in [`../README.md`](../README.md) (Recovery section) +
> [`../DECISIONS.md`](../DECISIONS.md) (D-recovery-*); the deferred `fix`
> code-repair agent (§8) is tracked in
> [`../FUTURE_recovery.md`](../FUTURE_recovery.md).

## 1. Problem & scope

i2c needs a **recovery** capability for failed/stuck iterations. The empirical
escalation + loop-log sweep (full detail in the Appendix below) established the
shape of the problem:

- **~7–8% of loop iterations exit non-clean** across diplomat (e2e),
  clankercourts and toolkit (i2c) — real and recurring, in i2c as well as e2e.
- The **#1 real i2c trigger** is an **ambiguous/malformed exit signal**: the
  worker (especially codex) finishes without a parseable result block, so the
  loop can't tell what state the work landed in.
- **~26 of diplomat's 33 incidents are orthogonal** to recovery — real code
  bugs, env/platform limits, design ambiguity — caught by REVIEW + human
  judgment, not by any state format. **Recovery ≠ "fix failures broadly."**
- **Workflow/state drift is the one class recovery can own**, and i2c already
  has the detection primitive: `invariants.check_post_action` halts on a
  post-CLOSE inconsistency. Recovery extends *detect-and-halt* into
  *detect-and-reconcile*.

**In scope (v1):** a deterministic drift audit; a `diagnose` entry point that
runs the audit first and classifies; human-gated `reconcile` for the
deterministically-fixable drift; and an out-of-band dispatch seam so these
operator-targeted actions run without normal state-machine progression.

**Out of scope (deferred):** a full `fix` code-diagnosis/repair agent;
self-healing auto-rerun chains; a persisted `.state/diagnoses.json` record.

## 2. Components (as built)

| Concern | Where | Notes |
|---|---|---|
| Drift audit (detection) | `i2c/recovery.py` | `audit_state` ([S]), `audit_git` ([G]/[D]), `audit` (both). Returns `DriftFinding`s. |
| git/disk helper | `i2c/recovery.py` | The **one** place i2c shells git (rev-parse/log/status/diff). Degrades to `[]` off a repo / off PATH. |
| Diagnosis (classify) | `control.diagnose` | Read-only projection; runs the audit first, returns a `Diagnosis`. |
| Reconcile (remediate) | `control.reconcile` | Dry-run by default; `apply=True` is the human gate; mutates via `state.py`. |
| Surfaces | `i2c diagnose`, `i2c reconcile [--apply]`; Telegram `/diagnose [N]`, `/reconcile [apply]` | + JSON via `--json`. Renderers in `render.py`; bot dispatch in `surfaces/telegram_core.py`. |
| Out-of-band dispatch | `run_iteration` + `i2c run --action … --target N` | Bypasses `state_machine.decide`. |
| Worker prompts | `i2c/data/instructions/diagnose.md`, `reconcile.md` | For the residual LLM judgment. |
| Runner advisory | `run_iteration` (§8b) | Non-fatal: surfaces reconcilable drift after each lifecycle action. |

## 3. Drift signals

Grounded in the schema. **[S]** = `.state` only, **[G]** = needs git, **[D]** =
needs the working tree. Each finding is `reconcilable` (a deterministic fix
exists) or judgment-class (surfaced, never auto-applied).

| Signal | Class | Reconcilable | Fix |
|---|---|---|---|
| `step_complete_without_commit` | [S] | no | (git may upgrade it) |
| `execute_state_not_advanced` | [S] | yes | `set project.json state=review` |
| `close_gate_not_set` | [S] | yes | `set project.json state=audit_boundary` |
| `commit_absent_from_git` | [G] | no | judgment (stale/wrong hash) |
| `commit_exists_step_pending` | [G/S] | yes | `complete steps.json --phase P --step S --commit H` (the canonical toolkit-5.3 case) |
| `step_complete_dirty_tree` | [D] | no | judgment (real work vs instrumentation) |

**False-positive guards (mandatory, from the logs).** The git/disk audit
normalizes line endings (`--ignore-cr-at-eol`) and ignores trailing-whitespace
diffs (`--ignore-space-at-eol`) so CRLF-only / EOL-whitespace churn on NTFS
doesn't cry wolf (diplomat #30/#31) — but it does **not** ignore all whitespace,
since leading indentation is semantic in Python (a real re-indentation must
still register as dirty). It also tolerates a missing git binary / non-repo dir
by returning no findings. A reconcile that cries wolf will be ignored.

## 4. `diagnose` — deterministic-first entry point

`i2c diagnose [--target N]` (default target: the latest iteration in
`logs/loop/summary.log`) assembles the failure context and **runs the drift
audit first**:

- **audit found drift** → `classification = workflow-drift` (the class recovery
  owns); `reconcilable` set if any finding carries a proposal.
- **target iteration failed, no drift** → `classification = unknown`; hand to
  the human / the LLM `diagnose` worker to bucket as code/spec/env (v1 does not
  auto-fix these).
- **no drift, no failed iteration** → `classification = none`.

The `Diagnosis` view also carries the **malformed-exit-signal** flag (the #1
real trigger): when the target failed because its 2-line exit block couldn't be
parsed, the true state is unknown and the drift audit reconstructs it. `diagnose`
is **read-only** — it mutates nothing.

## 5. `reconcile` — human-gated remediation

`i2c reconcile` is **dry-run by default**; `--apply` is the human gate. It runs
the audit, applies each reconcilable proposal through the sanctioned `state.py`
path (atomic + schema-validated — recovery never writes `.state/` directly), and
**surfaces** judgment-class findings without touching them. Reconcile **does not
mark an unfinished (code-blocked) step complete** — it closes the
state-vs-reality gap so the loop can re-attempt the action.

The operator choosing to run `reconcile` (after reviewing `diagnose`) is itself
the gate for the out-of-band worker action; the deterministic `i2c reconcile`
adds the explicit `--apply` confirmation on top.

## 6. Out-of-band dispatch

`i2c run --action diagnose|reconcile --target N` bypasses
`state_machine.decide` and dispatches the named recovery action directly against
the target iteration. Normal runs (no `--action`) are unchanged. The assembler
recognises the recovery actions (extended `ACTIONS`), renders a **failure-context
Region-3 section** (the drift audit + the target signal, via `control.diagnose`),
emits **no Next State** (recovery doesn't drive linear progression), and ships
packaged `instructions/diagnose.md` / `reconcile.md` (ejectable like the other
procedures). `[run.backends]` may map a backend per recovery action
(e.g. `diagnose = claude`).

## 7. Deterministic vs LLM split

- **Deterministic (no LLM):** the drift audit, the workflow-drift
  classification, and the mechanical reconcile mutations. This is most of v1 —
  cheap, auditable, unit-tested.
- **LLM (bounded worker action):** only the residual judgment — "is this commit
  really this step's work?", "finish vs restart?", and classifying non-drift
  (`unknown`) failures into code/spec/env.

## 8. Deferred (still FUTURE)

- A full **`fix`** code-diagnosis/repair agent — code bugs are the majority of
  failures but orthogonal to recovery (REVIEW regime + normal dev handle them).
- **Self-healing chains** (auto-rerun) — keep the human gate until reliability
  is proven.
- A persisted **`.state/diagnoses.json`** record — only needed when `fix` lands.

## 9. Corrections to the original concept (now fixed in this design)

- The concept doc's "5-line result block" is historical — the **current** exit
  signal is the **2-line** `EXIT:` / `REASON:` block
  (`run_iteration.py`, `schemas/exit_signal.schema.json`). The clankercourts
  logs say "5-line" because they ran on an older i2c.
- "`project.json.blocked` must be true" is stale — the current schema has **no
  `blocked` field**; the close gate is `state == audit_boundary`
  (`audit_escalation` for mid-phase escalation). v1 uses the state enum, not a
  boolean.

## 10. Verification

Unit-tested in `tests/test_recovery.py` (each drift signal incl. the CRLF-only
and non-repo false-positive guards), `tests/test_control.py`
(`diagnose`/`reconcile`, non-mutation of `diagnose`), `tests/test_cli.py`
(`i2c diagnose` / `i2c reconcile`, `i2c run --action … --target` dispatch),
`tests/test_telegram_core.py` (the `/diagnose` read-only + `/reconcile`
dry-run/apply bot commands and admin gating), and
`tests/test_prompt_golden.py` (recovery prompt byte-stability).

## Appendix: Phase 0 — empirical sweep (2026-06-29)

The evidentiary basis for the reconcile-first scope, mined before the design.
Mined the failure history of **diplomat** (e2e, ~49 phases + 21 self-play runs →
33 incidents), **clankercourts** (i2c, 124 devlog entries), and **toolkit** (i2c,
phases 5–6). Each incident bucketed reconcile / code / spec / env, and judged for
i2c-relevance (prevent / detectable / orthogonal).

**Distribution (diplomat, the volume source):** code 16 · env 7 · reconcile 6 ·
spec 3. **clankercourts:** 1 escalation (contract↔code drift, judgment-class),
~0 classic reconcile drift in the committed record (i2c discipline prevented it),
plus code/non-determinism caught at REVIEW. **toolkit:** the canonical reconcile
case — step 5.3 committed (`5b1fb2b`) but left `pending` because `i2c state
complete` hit a PATH bug; fixed by **reconciliation, not code**.

**Conclusions:**
1. **Reconcile is the only class a recovery feature can actually own.** ~26 of
   diplomat's 33 incidents (all code + env + spec) are **orthogonal** — real
   bugs / platform limits / design ambiguity that no state format prevents or
   detects; caught by REVIEW + human judgment, not recovery. Recovery ≠ "fix
   failures broadly"; recovery = **workflow-state drift**.
2. **The reconcile cases are real, recurring, deterministically tractable.**
   Dominant cause: the autonomous loop (esp. the **codex** backend) dies/cuts off
   **mid-iteration before bookkeeping** → commit-without-checkbox, uncommitted
   work, `steps_remaining` drift (diplomat #2/#3, toolkit 5.3). A structured
   `.state`-vs-git/disk audit catches these mechanically. **This is the v1.**
3. **i2c already prevents one e2e drift class** (diplomat #1: the bash
   checkbox-parsing state machine stalling a transition) — so i2c *reduces* but
   doesn't eliminate reconcile need (loop-death is orthogonal to state format).
4. **diagnose-first confirmed:** reconcile cases were only identifiable by
   inspecting state-vs-reality — i.e. a diagnosis. The deterministic drift-audit
   *is* the diagnosis for the reconcile class.

**Hard design caveats (from the logs):**
- **False positives:** CRLF-only diffs on NTFS + `python`/`i2c` PATH differences
  (diplomat #30/#31) will make a naive git-vs-disk audit cry wolf. Reconcile
  must normalize line-endings / ignore cosmetic-only diffs or operators won't
  trust it.
- **Multi-source context:** the devlog has a **post-success bias** (entries are
  written after a step succeeds) — a failure-context assembler must read
  `.state` + git/disk + the `phases.json` gate + gotchas + loop logs, not the
  devlog alone.
- **Human-gated:** "real fix vs temp instrumentation" in a dirty tree
  (diplomat #4) is a judgment call — reconcile *surfaces* drift, never
  auto-commits.

**Loop-log sweep.** Parsed the per-iteration `logs/loop/summary.log` of all three
projects (one structured line per iteration: signal/exit/backend/action/reason).
Non-clean exits: **diplomat 17/220 (8%)**, **clankercourts 7/97 (7%)**,
**toolkit 1/44 (2%)** — plus diplomat had 9 `ERROR`-signal iterations (codex
turn-health circuit-breaker forced-exits). This **contradicts the devlog's
near-zero reading** and confirms its post-success bias. Findings that matter:
- **The #1 i2c failure trigger is an ambiguous/malformed exit signal** — the
  worker (esp. **codex**) finished without emitting a parseable 2-line exit
  signal (clankercourts: 5 of 7 non-clean exits). The loop then can't tell what
  state the work is in → the operator must reconcile. The most common real i2c
  case.
- **i2c ALREADY has a deterministic state-drift detector.** clankercourts hit a
  post-CLOSE invariant failure (exit 2) — i2c runs post-action invariant checks
  and halts on violation. Reconcile is **not greenfield**: it extends existing
  detect-and-halt into detect-and-**reconcile**.
- **toolkit's one non-clean exit is the canonical 5.3 case at loop level:**
  `state CLI unavailable (i2c not found), so .state/ step completion could not be
  recorded` (env cause → reconcile remedy).
- **diplomat's codex turn-health forced-exits (7)** are reconcile-adjacent: work
  completed + committed but the iteration was cut at the turn guard, leaving
  bookkeeping uncertain.

Net: the reconcile/recovery trigger runs ~**7–8%** of iterations in *both* e2e
and i2c — real and recurring, just hidden from the success-biased devlog. This
grounded the **narrow reconcile-first v1** built above, with the full `fix` /
code-diagnosis agent deferred (code bugs are the majority of failures but are
orthogonal to recovery).

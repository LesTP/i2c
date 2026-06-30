# DESIGN — Recovery v1 (reconcile-first)

> **Status: built (v1).** Graduated from
> [`FUTURE_recovery.md`](FUTURE_recovery.md) (concept + the 2026-06-29 empirical
> sweep). This document is the design *as implemented*. The full `fix`
> code-repair agent remains FUTURE — see §8.

## 1. Problem & scope

i2c needs a **recovery** capability for failed/stuck iterations. The empirical
escalation + loop-log sweep (captured in `FUTURE_recovery.md` §"Phase 0
findings") established the shape of the problem:

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
normalizes line endings (`--ignore-cr-at-eol`) and ignores whitespace-only
diffs (`--ignore-all-space`) so CRLF-only churn on NTFS doesn't cry wolf
(diplomat #30/#31), and tolerates a missing git binary / non-repo dir by
returning no findings. A reconcile that cries wolf will be ignored.

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

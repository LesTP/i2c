# Recovery Actions — `reconcile` / `diagnose` / `fix` (FUTURE)

> **Status: GRADUATED → see [`DESIGN_recovery_v1.md`](DESIGN_recovery_v1.md).**
> The reconcile-first **v1** is built (deterministic drift audit + `diagnose` +
> human-gated `reconcile` + out-of-band dispatch). This file is retained for the
> original concept and the **Phase 0 empirical sweep** (the design's evidentiary
> basis). The full `fix` code-repair agent remains FUTURE.
>
> Originates from the bot-command discussion (2026-06-27). Builds on the
> structured-escalation foundation already in i2c (worker `escalate`/`blocked`
> devlog entries + `control.escalation` + `control.logs_transcript`).
>
> **Stale-wording note (corrected in DESIGN_recovery_v1.md §9):** quotes below
> referencing a "5-line result block" and `project.json.blocked` reflect older
> i2c versions. The current exit signal is the **2-line** `EXIT:`/`REASON:`
> block, and the schema has **no `blocked` field** — the gate is
> `state == audit_boundary`.

## Idea

Turn troubleshooting of failed/stuck iterations into **bounded worker actions**
— invoked on demand against a target iteration — rather than an open-ended
orchestrator. There are **two distinct recovery shapes**, and the first is the
more common.

## Operator note (2026-06-29): diagnose-first

The two modes below are *outcomes*, not independent entry points. The operator
can't know a failure is a `reconcile` (workflow-drift) case rather than a
code/spec case **without diagnosing it first** — so `reconcile` cannot be invoked
blind. Implication for the design: **`diagnose` is the single entry point**, and
its classification must add a **workflow-drift / reconcile** class alongside
env / code / spec, then route:

- `workflow-drift` → `reconcile` (often just the deterministic state-vs-git
  audit + a small finish/advance)
- `code` → `fix`  ·  `spec` → hand to human  ·  `env` → fix / operator

The deterministic drift audit described in Mode 1 effectively *is* the
reconcile-case detector, so it belongs **inside `diagnose`** as a cheap
prefilter: run it first; if it explains the failure, route to `reconcile`.
`reconcile` / `fix` remain the remediations, gated behind the diagnosis.

## Mode 1 — `reconcile`: workflow/state meta-view (the common case)

Often nothing is "broken" in the code; the worker did some or most of the work
but `.state/` is **inconsistent with reality**:

- code written but not committed,
- committed but the step left `pending` (no `commit` recorded),
- all steps complete but `project.state` still `execute`,
- phase closed but the boundary not advanced.

The recovery is a **meta-view**: check the artifacts (git log, files on disk,
test results) against `.state/`, determine the *true* position, and reconcile —
usually **finish the last item or two and advance**, occasionally restart the
step.

**Worked example:** toolkit phase-5 step 5.3 — the Gemini code was committed
(`5b1fb2b`) but the step stayed `pending` because the worker's `i2c state
complete` hit the PATH bug. The fix was *reconciliation, not code*: mark 5.3
complete with its commit, then resume.

**Why i2c makes this tractable:** structured `.state/` means drift is
*structurally* detectable — `steps.json.{status,commit}` vs `git log`,
`project.state` vs step completeness — far easier than e2e's prose frontmatter +
checkboxes. The state machine already reconciles **state → action**; `reconcile`
extends that to **state ↔ reality**. Much of the drift detection may be
*deterministic* (a state-vs-git audit), with the LLM reserved for the judgment
calls ("is this commit really this step's work?", "finish vs restart?").

## Mode 2 — `diagnose` / `fix`: code or logic

The work has a real blocker needing a change.

- **`diagnose <iter>`** — pre-assembled with the iteration's *failure context*
  (transcript, summary line, `.state`, the failing commit's diff, test output,
  the triggering escalation entry). Classifies the failure (**env / code /
  spec**), finds root cause, and **writes a structured diagnosis record** (e.g.
  `.state/diagnoses.json`: `{iter, class, root_cause, proposed_fix, files,
  confidence}`). Does not change code.
- **(human gate)** operator reviews (`/audit diagnosis N`).
- **`fix <iter|diagnosis>`** — reads the diagnosis, implements the fix, runs
  tests, commits, marks the diagnosis resolved. Does **not** mark the original
  failed step complete — it clears the blocker so the loop re-attempts it.
- **resume** — the normal loop re-runs the failed action.

## Why worker-actions (the framing)

- Puts the LLM reasoning where i2c already sanctions it — §7.4: nondeterminism
  lives "inside the worker (one bounded ACTION)." Reuses the entire existing
  loop (assembler → worker → `i2c state` → 2-line signal → commit) with no new
  machinery; the bot command is a thin deterministic dispatch (like `/run`).
- The **structured diagnosis file is the pivot** — both the diagnose→fix
  handoff and the human review point, auditable like all `.state/`.
- Synergy with per-action backends (`[run.backends]`): `diagnose = claude/opus`,
  `fix = codex`, `reconcile = ` (likely cheap/deterministic-leaning).

## Open design questions

- **diagnose-first (operator note, 2026-06-29).** `reconcile` is not an
  independent entry point — settle whether `diagnose` always runs first, with a
  cheap deterministic state-vs-git drift prefilter that can short-circuit to
  `reconcile`. See the "diagnose-first" note above.
- **Out-of-band dispatch.**
  machine; recovery actions are operator-targeted at an iteration. Needs an
  explicit-action dispatch path (`i2c run --action reconcile --target N`) that
  bypasses normal state-machine progression.
- **How much of `reconcile` is deterministic** vs needs the LLM — possibly a
  deterministic drift-detector (state-vs-git/disk) that only escalates the
  judgment calls to a model.
- **New state file + schema** (`diagnoses.json`) and a new assembler
  **failure-context section** (iteration logs + escalation + the state-vs-git
  audit for `reconcile`).
- **New instruction files** `reconcile.md` / `diagnose.md` / `fix.md`.
- **Resume-after-recovery semantics** — a defined transition back to the true
  position; `fix` must not mark the failed step complete.
- **Scope discipline** — recovery must not invent (same rule PLAN follows):
  `class=spec` → "needs a human decision," no fabricated fix.
- **Auto-rerun?** Default: hand back to the operator for a `/run` retry; keep the
  human gate until self-heal is proven.

## Planning TODOs (do these before designing)

1. **Mine past escalations — and categorize.** Review the escalation / `blocked`
   devlog history across real projects (toolkit, diplomat, clankercourts, …) to
   find the *common* causes, and bucket each as **reconcile-needed** (workflow
   drift) vs **code-fix** vs **spec (needs human)**. Expectation from
   experience: a large share are reconcile, not code. Ground the design
   empirically — only build for classes a bounded action can actually fix.
2. **Enumerate the drift types `reconcile` must detect**, and mark which are
   deterministically detectable (state-vs-git/disk) vs need judgment. This sizes
   how much is a deterministic audit vs an LLM action.
3. **Richer diagnose/fix prompt.** Assemble enough context to reason well —
   beyond a single transcript: the relevant ARCH/contract, failing test output,
   commit diff, prior diagnoses for the phase, project gotchas. Underspecified
   context is the main reliability risk.
4. **Worker-action vs orchestrator placement.** An orchestrator will exist
   regardless (the §7.3 loop driver), so worker-actions are **not** chosen to
   avoid orchestrator cost — they're chosen for **single-system simplicity**
   (one loop, more actions) over two separate systems. Revisit whether genuinely
   multi-step investigation belongs in the orchestrator.

## Phase 0 — escalation sweep findings (2026-06-29)

Planning TODO #1 done. Mined the failure history of **diplomat** (e2e, ~49
phases + 21 self-play runs → 33 incidents), **clankercourts** (i2c, 124 devlog
entries), and **toolkit** (i2c, phases 5–6). Each incident bucketed reconcile /
code / spec / env, and judged for i2c-relevance (prevent / detectable /
orthogonal).

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
4. **diagnose-first confirmed** (operator note above): reconcile cases were only
   identifiable by inspecting state-vs-reality — i.e. a diagnosis. The
   deterministic drift-audit *is* the diagnosis for the reconcile class.

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

**Loop-log sweep (2026-06-29, closes the earlier fidelity gap).** Parsed the
per-iteration `logs/loop/summary.log` of all three projects (one structured line
per iteration: signal/exit/backend/action/reason). Non-clean exits: **diplomat
17/220 (8%)**, **clankercourts 7/97 (7%)**, **toolkit 1/44 (2%)** — plus
diplomat had 9 `ERROR`-signal iterations (codex turn-health circuit-breaker
forced-exits). This **contradicts the devlog's near-zero reading** and confirms
its post-success bias. Findings that matter for recovery:
- **The #1 i2c failure trigger is an ambiguous/malformed exit signal** — the
  worker (esp. **codex**) finished without emitting a parseable 2-line exit
  signal (clankercourts: 5 of 7 non-clean exits). The loop then can't tell what
  state the work is in → the operator must reconcile (did the edit land? commit?
  step status?). This is precisely the reconcile/diagnose case, and it's the
  most common one in the real i2c project.
- **i2c ALREADY has a deterministic state-drift detector.** clankercourts hit a
  `post-CLOSE invariants failed: project.json.blocked must be true after CLOSE`
  (exit 2) — i2c runs post-action invariant checks and halts on violation. So
  reconcile is **not greenfield**: it extends existing detect-and-halt into
  detect-and-**reconcile**.
- **toolkit's one non-clean exit is the canonical 5.3 case at loop level:**
  `state CLI unavailable (i2c not found), so .state/ step completion could not be
  recorded` (env cause → reconcile remedy).
- **diplomat's codex turn-health forced-exits (7)** are reconcile-adjacent: work
  completed + committed but the iteration was cut at the turn guard, leaving
  bookkeeping uncertain.

Net: the reconcile/recovery trigger runs ~**7–8%** of iterations in *both* e2e
and i2c — real and recurring, just hidden from the success-biased devlog.

**Scope implication (proposed):** formalize a **narrow reconcile-first v1** — a
deterministic state-vs-(git/disk) drift audit + human-gated finish/advance,
fronted by `diagnose`, **built on i2c's existing post-action invariant checks**
(extend detect-and-halt into detect-and-reconcile) and triggered primarily by
the **ambiguous/malformed-exit-signal** case (the #1 real trigger). **Defer** a
full `fix` / code-diagnosis agent — code bugs are the majority of failures but
are orthogonal to recovery (handled by the REVIEW regime + normal dev).

## Out of scope (for now)

- Self-healing chains (`reconcile`/`fix` → rerun unattended) — only after the
  human-gated version proves reliable.
- The open-ended interactive path (operator + assistant) remains the **fallback**
  for failures a bounded recovery action can't crack.

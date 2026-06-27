# Recovery Actions — `reconcile` / `diagnose` / `fix` (FUTURE)

> **Status: concept, needs planning. Not scheduled.** Captured so it isn't
> lost. Originates from the bot-command discussion (2026-06-27). Builds on the
> structured-escalation foundation already in i2c (worker `escalate`/`blocked`
> devlog entries + `control.escalation` + `control.logs_transcript`).

## Idea

Turn troubleshooting of failed/stuck iterations into **bounded worker actions**
— invoked on demand against a target iteration — rather than an open-ended
orchestrator. There are **two distinct recovery shapes**, and the first is the
more common.

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

- **Out-of-band dispatch.** PLAN/EXECUTE/REVIEW/CLOSE come *from* the state
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

## Out of scope (for now)

- Self-healing chains (`reconcile`/`fix` → rerun unattended) — only after the
  human-gated version proves reliable.
- The open-ended interactive path (operator + assistant) remains the **fallback**
  for failures a bounded recovery action can't crack.

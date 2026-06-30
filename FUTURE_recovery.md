# Recovery — the `fix` code-repair agent (FUTURE)

> **Status: FUTURE — not scheduled.** The reconcile-first recovery **v1**
> (deterministic drift audit + `diagnose` + human-gated `reconcile` + out-of-band
> dispatch) shipped 2026-06-29 — see
> [`archive/DESIGN_recovery_v1.md`](archive/DESIGN_recovery_v1.md) for the design
> *and* the Phase-0 empirical sweep that grounds it (Appendix), plus
> [`README.md`](README.md) (Recovery) and [`DECISIONS.md`](DECISIONS.md)
> (D-recovery-*). This file tracks the one remaining recovery initiative: a
> bounded **`fix`** code-repair worker action for the `code`-class failures that
> `reconcile` deliberately does not touch.

## Why this is separate from what shipped

v1 owns **workflow-state drift** — `.state/` disagreeing with reality — which is
deterministically detectable and human-gated-reconcilable. The Phase-0 sweep (in
the archived design) found that **code / spec / env** failures are the majority
but are *orthogonal* to recovery: real bugs, platform limits, and design
ambiguity that no state format prevents. Those are handled today by the REVIEW
regime + normal dev. `fix` is the optional future step that would turn the
`code` class into a bounded worker action too.

## Shape: `diagnose` (code class) → `fix`

`diagnose` already classifies a non-drift failure as `unknown` and hands it to a
human. The `fix` extension would build on that:

- **`diagnose <iter>` (code path)** — beyond the drift audit, assemble the full
  failure context (transcript, summary line, `.state`, the failing commit's diff,
  test output, the triggering escalation entry), classify **env / code / spec**,
  find root cause, and **write a structured diagnosis record** (e.g.
  `.state/diagnoses.json`: `{iter, class, root_cause, proposed_fix, files,
  confidence}`). Does not change code.
- **(human gate)** operator reviews the diagnosis.
- **`fix <iter|diagnosis>`** — reads the diagnosis, implements the fix, runs
  tests, commits, marks the diagnosis resolved. Does **not** mark the original
  failed step complete — it clears the blocker so the loop re-attempts it.
- **resume** — the normal loop re-runs the failed action.

## Open items

- **`.state/diagnoses.json` + schema** — the persisted diagnosis record is the
  pivot for the diagnose→fix handoff and the human review point; auditable like
  all `.state/`. Only needed once `fix` lands.
- **`fix.md` instruction file** — the bounded worker procedure for code repair.
- **Richer diagnose/fix prompt** — assemble enough to reason well: the relevant
  ARCH/contract, failing test output, commit diff, prior diagnoses for the phase,
  project gotchas. Underspecified context is the main reliability risk.
- **Resume-after-recovery semantics** — a defined transition back to the true
  position; `fix` must not mark the failed step complete.
- **Scope discipline** — `fix` must not invent (same rule PLAN follows):
  `class=spec` → "needs a human decision," no fabricated fix.
- **Self-healing / auto-rerun** — default keeps the human gate (operator `/run`
  retry); only chain `fix` → rerun unattended once the gated version proves
  reliable.
- **Per-action backend synergy** — `[run.backends]` could map `fix = codex` (or a
  stronger model), mirroring the existing `diagnose` / `reconcile` mapping.
- **Worker-action vs orchestrator placement** — `fix` would ride the existing loop
  (assembler → worker → `i2c state` → 2-line signal → commit) as a bounded
  action; revisit whether genuinely multi-step investigation belongs in the
  orchestrator (the §7.3 loop driver) instead.

## Out of scope (for now)

- The open-ended interactive path (operator + assistant) remains the **fallback**
  for failures a bounded `fix` action can't crack.

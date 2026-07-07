# Recovery — code-class capture (`diagnose(code)` → `bugfix` FU) (FUTURE)

> **Status: FUTURE — not scheduled.** The reconcile-first recovery **v1**
> (deterministic drift audit + `diagnose` + human-gated `reconcile` + out-of-band
> dispatch) shipped 2026-06-29 — see
> [`archive/DESIGN_recovery_v1.md`](archive/DESIGN_recovery_v1.md) for the design
> *and* the Phase-0 empirical sweep that grounds it (Appendix), plus
> [`README.md`](README.md) (Recovery) and [`DECISIONS.md`](DECISIONS.md)
> (D-recovery-*).
>
> **Scope decided (D-recovery-7, 2026-07-07).** The old "`fix` code-repair agent"
> is **not** a standalone stack. `fix` *is* the **`bugfix` refine kind**: the
> single-shot LLM executor lives in the refine loop (`DESIGN_refine_v1.md` §12,
> Proposal B). What remains recovery-specific — and all this file now tracks — is
> the **code-class capture** (a `diagnose` extension that files a `bugfix`
> follow-up) plus the **gate/scope policy** for repairing one.

## Why this is separate from what shipped

v1 owns **workflow-state drift** — `.state/` disagreeing with reality — which is
deterministically detectable and human-gated-**reconcilable** (no worker). The
Phase-0 sweep (in the archived design) found that **code / spec / env** failures
are the majority but are *orthogonal* to state repair: real bugs, platform
limits, and design ambiguity that no state format prevents. Those are handled
today by the REVIEW regime + normal dev. The `code` class is the one that can be
turned into a bounded worker action — and that worker is now refine's, not a
recovery-owned `fix.md`.

## The two axes (D-recovery-7)

- **Deterministic vs LLM.** `reconcile` (shipped) is deterministic state-repair —
  its own thing, **untouched**. Everything below is the LLM half.
- **Capture vs execute (LLM half).** The *executor* for any single-shot item —
  incl. code repair — is the **refine loop** (`i2c refine <fu-id>`,
  `DESIGN_refine_v1.md` §12). Recovery owns only the *capture*: turning a
  diagnosed `code`-class failure into a well-specified `bugfix` follow-up.

## Shape: `diagnose(code)` → `bugfix` FU → `i2c refine`

`diagnose` already runs the drift audit first and classifies a non-drift failure
as `unknown`. The remaining extension:

- **`diagnose <iter>` (code path)** — beyond the drift audit, assemble the full
  failure context (transcript, summary line, `.state`, the failing commit's diff,
  test output, the triggering escalation entry), classify **env / code / spec**,
  find root cause, and **file a `followups.json` item of `kind: bugfix`** with the
  diagnosis as its `context` / `refs` (root cause, proposed fix, files). Changes
  no code. Only `class=code` becomes a `bugfix` FU; `class=spec` → a human
  decision (never a fabricated fix); `class=env` → surfaced, not auto-repaired.
- **(human gate)** operator reviews the `bugfix` FU (dispatch gate — see below).
- **`i2c refine <fu-id>`** (the refine loop) implements the repair, runs tests,
  and closes the FU. Per the refine sub-phase invariant (`DESIGN_refine_v1.md`
  §12.3 Q-B2) it **must not** touch `phases.json` / `steps.json` / `project.json`
  — so the failed Build step stays pending and the loop **re-attempts** it on the
  next `i2c run`. (This is the "resume-after-recovery" semantic — now satisfied
  *structurally* by refine, not by a bespoke rule.)

## What refine subsumes (dropped from this file)

Because the executor is refine's, the following — previously "Open items" here —
are **gone**, replaced by the refine substrate:

- **`.state/diagnoses.json` + schema** → the diagnosis rides the `bugfix` FU's
  `context` / `refs` in `followups.json`. (Caveat: prose `context` drops the
  structured `class` / `confidence` fields; acceptable for a human-gated v1.
  Revisit only if confidence-gated *auto*-dispatch is ever wanted.)
- **`fix.md` instruction file + separate dispatch** → `instructions/refine.md`
  (+ optional per-kind guidance, Proposal C) and `i2c refine` / `/refine`.
- **Resume-after-recovery semantics** → the refine sub-phase invariant (Q-B2).
- **Per-action backend synergy (`[run.backends].fix = codex`)** → there is no
  `fix` action; tier is chosen per call (`i2c refine … --backend/--model`) or by
  the orchestrator per FU `kind` (`DESIGN_refine_v1.md` §12.3 Q-B7).

## Open items (what genuinely remains)

- **The `diagnose(code)` capture worker** — the real recovery-specific IP:
  assembling enough context to reason well (relevant ARCH/contract, failing test
  output, commit diff, prior `bugfix` FUs for the phase, project gotchas) and
  emitting a well-specified `bugfix` FU. *Underspecified context is the main
  reliability risk* — this is where the effort concentrates.
- **Scope discipline** — `diagnose` must not invent (same rule PLAN follows):
  `class=spec` → "needs a human decision," never a fabricated `bugfix`.
- **Dispatch gate for `bugfix`** — unlike a prose pass, a `bugfix` keeps a
  **mandatory human gate** at dispatch (a low-confidence diagnosis can be wrong):
  it is the one refine kind not freely routed to unattended `/refine`. Only chain
  `diagnose(code)` → `refine` → rerun unattended once the gated version proves
  reliable (self-healing).
- **Worker-action vs orchestrator placement** — a `bugfix` refine run is a
  bounded single-shot action; revisit whether genuinely multi-step investigation
  belongs in the orchestrator (the §7.3 loop driver) instead.

## Out of scope (for now)

- The open-ended interactive path (operator + assistant) remains the **fallback**
  for failures a bounded `bugfix` refine run can't crack.

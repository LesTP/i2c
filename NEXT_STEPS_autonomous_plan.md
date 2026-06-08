# Next Steps — Autonomous-PLAN Readiness (v1)

> Forward-looking plan. Captures the five framework deltas + per-module
> ARCH discipline needed to make i2c's PLAN action safely autonomous, so
> the loop can drive entire phases without supervision and a human reviews
> only at phase boundaries (e2e-parity workflow).
>
> Status: planned, not started. Tracked as **FU-32** in `FOLLOWUPS.md`.
> Authors: operator + assistant, 2026-06-08.
> Builds on: `DESIGN_state_lifecycle_v1.md` (state lifecycle v1, landed),
> and the autonomous-loop foundation shipped in i2c Phase 3.A.

---

## 1. Cold-start: where we are now

### i2c framework state
- **State lifecycle v1 complete.** Schema, state machine, assembler, all
  instructions, fixtures, tests in the 7-state model (`plan`, `execute`,
  `review`, `close`, `audit_boundary`, `audit_escalation`, `done`).
  Commits 224aaf5 → e2a71ec → 9e53e62 → a4d88b5 → 7693330.
- **Autonomous loop runner shipped** in Phase 3.A (FU-22 closure +
  `tools/run_iteration.py` + invariants check). Single-iteration dispatch
  works; multi-iteration via shell wrapping.
- **No known blockers** to running autonomous loops from pirozhok per
  FU-28: `ssh pirozhok "incus exec claude-code -- su - claude -c 'cd
  /home/claude/workspace/<project> && python3 ../i2c/tools/run_iteration.py
  --backend claude --model sonnet --max-budget-usd 5.00'"`.

### clankercourts state
- **Phase 4 EXECUTE complete** at commit `97e9ea4`. validator module covers
  v9 §6 fully; 332 tests pass. State transitioned to `review`.
- **REVIEW + CLOSE dispatched autonomously** (first autonomous run of those
  actions for i2c). Outcome pending at time of writing. Expected end-state
  after both iterations: `state=audit_boundary, phase=4`, all `.state/`
  writes from EXECUTE swept into the CLOSE commit.
- **No further work planned** until the FU-32 deltas land OR you author
  Phase-5+ module ARCH files (whichever you sequence first — see §5).

### What this doc captures
A coherent set of five framework deltas + an ARCH-file discipline. They
shift the cost of phase work from "many tactical interactions per phase"
to "one collaborative ARCH session per module + one phase-boundary review
per phase." Matches the e2e two-step workflow (pre-arch design + loosely-
supervised autonomous batch).

---

## 2. Goal

Make `instructions/plan.md` autonomous-capable. Today the procedure can
run autonomously *in principle*, but in practice it requires human
supervision because:

1. PLAN's step-breakdown step is creative (humans pick the slice; ARCH
   files don't currently constrain the shape sufficiently).
2. PLAN's escalation triggers are vague (only "unclear spec" enumerated;
   other conditions like source-vs-ARCH drift get caught at EXECUTE time
   or — worse — silently produce wrong code).
3. Decisions made during autonomous PLAN/EXECUTE aren't easily reviewable
   in batch at the phase boundary (no per-phase filter on `decisions.json`).

The end-state we want, validated by running CC Phase 5+ this way:

| Stage | Today | Target |
|-------|-------|--------|
| Pre-phase ARCH design | implicit, in chat | **one collaborative session** producing a complete `ARCH_<module>.md` |
| PLAN | requires human approval at scope, regime, breakdown | **autonomous** unless an escalation trigger fires |
| EXECUTE | autonomous (proven Phases 2, 3, 4) | unchanged |
| REVIEW | autonomous in v1 lifecycle; first run on CC Phase 4 | unchanged |
| CLOSE | autonomous in v1 lifecycle; first run on CC Phase 4 | unchanged |
| `audit_boundary` clearance | manual | **manual + reviewer's batch decision audit** |

Per-phase human touch points drop from ~15 (Phase 4 supervised) to ~3-4
(pre-phase ARCH session + phase-boundary review + clearance).

---

## 3. Context: what the e2e workflow does

e2e is explicitly a two-step process:

1. **Architecture stage.** Work out PROJECT.md + ARCHITECTURE.md +
   per-module ARCH files in detail. Specs and guide live at
   `p:\shared\e2e\ref\SPEC_architecture.md` and
   `p:\shared\e2e\ref\GUIDE_architecture.md`. The exit criterion is
   stability — could a developer build the module from this file alone?
2. **Implementation stage.** Run autonomous loops in loosely supervised
   batch mode against the pre-designed ARCH files.

i2c currently has no equivalent ARCH-authoring discipline. ARCH files
get written in the same chats where work begins; their shape is
ad hoc. CC's `ARCH_resolver.md` and `ARCH_validator.md` happen to be
detailed enough to drive EXECUTE autonomously (and they did, for Phases
2-3), but their shape is convention, not spec.

The deltas below close that gap by promoting the e2e ARCH templates into
i2c with two new required sections that make autonomous PLAN safe.

---

## 4. The five framework deltas

### Δ1. `decisions.json` phase field

**File:** `schemas/decisions.schema.json`
**Change:** add optional `phase: integer` field. Existing records without
it stay valid; new records can carry it.
**Why:** lets a phase-boundary reviewer (you + me) filter decisions added
this phase. Today the only way is timestamp filtering (fragile) or
inferring from ID ranges (works only when decisions land in order).
**Effort:** ~5 LOC schema + test fixture update.

### Δ2. `plan.md` escalation triggers enumeration

**File:** `instructions/plan.md` (step 2 or new step 2.5)
**Change:** explicit enumeration of conditions under which PLAN halts to
`state=audit_escalation` and emits `EXIT 2`. Minimum list:
- **Source-vs-ARCH contract drift** — ARCH says X, the canonical source
  (game rules doc, sibling project's ARCH, etc.) says Y. The case caught
  by hand in CC Phase 4.3.
- **Multi-regime scope** — the phase mixes Build + Refine (e.g., shipping
  a feature *and* polishing its perception). Split required, human call.
- **Cross-module breakage at plan time** — the planned phase will change
  a contract another built module reads. Per close.md's existing
  "Cross-module breakage" handling, but flagged early at PLAN.
- **Step-shape ambiguity** — ARCH doesn't decompose into clear steps;
  multiple equally-good breakdowns exist with no objective tiebreaker.
- **Missing precondition** — the dep-probe surfaces a mismatch between
  the dependency's actual surface and what ARCH assumes.

**Why:** today, PLAN's only enumerated escalation is "unclear spec." The
absence of explicit triggers means autonomous PLAN might paper over real
problems. Explicit triggers shift the failure mode from "wrong code
silently produced" to "halted with reason."

**Effort:** ~30 lines of doc; no code change.

### Δ3. `assemble_context.py --section decisions --phase N`

**File:** `tools/assemble_context.py`
**Change:** new section for filtered-by-phase decisions. Renders the
subset of `decisions.json` records whose `phase == N` (assumes Δ1
landed). Falls back to all-decisions if Δ1 hasn't shipped.
**Why:** phase-boundary review needs a clean "what decisions did the
autonomous worker make this phase?" view. Today the assembler ships
`Decisions` filtered by status, not phase.
**Effort:** ~20 LOC + 2 tests.

### Δ4. ARCH template promoted from e2e + augmented

**Files (new):**
- `i2c/ref/SPEC_architecture.md` (port of `e2e/ref/SPEC_architecture.md`)
- `i2c/ref/GUIDE_architecture.md` (port of `e2e/ref/GUIDE_architecture.md`)

**Changes vs. e2e originals:**
Add two **required** sections to the per-module `ARCH_<module>.md`
template:
- **`## Phasing in this pilot`** — explicit step-by-step decomposition.
  Format: per-step bullet list with title + scope + test count, ordered.
  This is what PLAN reads to author step records autonomously. Both
  CC `ARCH_resolver.md` and `ARCH_validator.md` already have this
  section — promote the pattern, name it canonically.
- **`## Escalation triggers`** — module-specific conditions under which
  PLAN or EXECUTE halts to `audit_escalation`. Examples for validator:
  "v9 §6 source contradicts the rule mapping below"; "the dep probe
  surfaces a type mismatch with `clankercourts.resolver.types`." Project-
  general triggers (cross-module breakage, three strikes) come from
  WORKER_SPEC; module-specific ones come from ARCH.

Add two **recommended** sections:
- **`## Testing Strategy`** — informs PLAN's per-step test count and
  shape. Resolver/validator already have analogs.
- **`## Inputs the Module Does Not Handle`** — explicit non-scope.
  Reduces ambiguity about what falls to the harness vs. the module.

**Why:** autonomous PLAN can transcribe a step breakdown but can't
invent one safely. Mandating Phasing makes the transcription possible.
Mandating Escalation triggers makes safe-failure explicit instead of
implicit.

**Effort:** ~250 lines of new doc (mostly ported content + 2-section
augmentation). No code.

### Δ5. PLAN precondition check on ARCH completeness

**File:** `instructions/plan.md` (step 1 or step 2)
**Change:** PLAN reads the assembled `Module Contract` section; if it
lacks `## Phasing in this pilot` or `## Escalation triggers`, worker
writes a devlog entry and sets `state=audit_escalation` with reason
"ARCH lacks autonomous-PLAN-ready sections — needs collaborative
authoring session per `ref/SPEC_architecture.md`."

Could optionally be enforced at the assembler level instead (assembler
validates ARCH file shape before assembling the PLAN prompt), but
keeping it in plan.md is simpler — worker checks, escalates, no new
assembler complexity.

**Why:** without this, the ARCH discipline is aspirational. With it,
the framework refuses to autonomously plan against an under-specified
ARCH — clean failure mode.

**Effort:** ~15 lines of doc + ~10 LOC for assembler heading detection
if we go the assembler route. Doc-only is enough for v1.

---

## 5. Sequencing — CC-first or i2c-first?

Both are valid; the choice depends on what you want to validate first.

### Path A: CC ARCH files first, then framework deltas

1. Author `ARCH_<module>.md` for CC's remaining Phase-5+ modules using
   the augmented template (Phasing + Escalation triggers + recommended
   sections). Done in collaborative sessions before any PLAN runs.
2. Run CC Phase 5+ autonomously, supervised at phase boundaries.
3. Apply the framework deltas (Δ1-Δ5) to i2c afterward, distilling
   lessons from the CC ARCH-authoring sessions into the templates.

**Pros:** validates the ARCH-discipline assumption against real work
before codifying. Less risk of templates that look right but don't
serve real modules.
**Cons:** longer time before the framework benefits land. Templates
might miss things that show up later in different module shapes.

### Path B: Framework deltas first, then CC ARCH files

1. Land Δ1-Δ5 in i2c. ARCH templates are the spec; PLAN check enforces
   them; decisions.json filter is available; plan.md escalation triggers
   are documented.
2. Author CC Phase-5+ ARCH files using the new templates.
3. Run CC Phase 5+ autonomously.

**Pros:** templates available for the CC authoring sessions. Framework
ready before consumer needs it. Standard "framework precedes consumer"
order.
**Cons:** Δ4's templates might need iteration after first real use
revealing gaps — a second pass after CC validates them.

### My read

**Path B with a fast-feedback loop.** Land Δ1-Δ3 (mechanical / schema /
small) and Δ4 (template port + augmentation) in one session. Defer Δ5
(PLAN precondition check) until after CC has at least one phase
authored under the new templates — Δ5 needs the template's required
sections to be settled. Then CC Phase 5+ exercises the full stack.

If Path A is your preference (validate-then-codify), the ordering
inverts but the work is the same. Either way the templates need
authoring; the question is whether i2c codifies first or CC instances
first.

---

## 6. Open questions

- **Q1: ARCH template placement.** `i2c/ref/` (e2e parity, treats as
  reference) or `i2c/templates/` (consistent with existing
  `templates/.claude/commands/`)? Recommend `i2c/ref/` to match e2e's
  conventions.
- **Q2: Should Δ5 escalation include WHICH section is missing?** Yes —
  the devlog message should name `## Phasing` or `## Escalation
  triggers` so the operator/wrapper knows what to fix without parsing
  the ARCH. ~5 extra LOC.
- **Q3: Decisions phase field — required or optional?** Optional in v1
  (existing records lack it). Could promote to required in a v2 after
  back-filling. Don't break existing records.
- **Q4: Phase-end review checklist.** Should we ship a documented
  protocol for the phase-boundary review (what reviewer reads in what
  order)? Probably yes — covered in `ref/GUIDE_phase_boundary_review.md`
  or similar. Not in the v1 delta scope; track as a Δ6 if it grows.
- **Q5: What constitutes a "module" for ARCH purposes?** CC has
  resolver, validator, planner, etc. — each gets its own ARCH. But for
  smaller projects a single combined spec might suffice (e2e's
  Combined Spec Template handles this). Worth keeping the option
  available in the i2c template port.

---

## 7. References

- `DESIGN_state_lifecycle_v1.md` — the lifecycle redesign this builds on.
- `FOLLOWUPS.md` — FU-22 (invariants), FU-28 (autonomous loop env),
  FU-30 (state lifecycle, closed), FU-31 (ARCHITECTURE.md update step),
  FU-32 (this work, open).
- `e2e/ref/SPEC_architecture.md` and `e2e/ref/GUIDE_architecture.md` —
  source for the template port (Δ4).
- `clankercourts/ARCH_resolver.md` and `clankercourts/ARCH_validator.md`
  — examples of the Phasing pattern (without yet-named Escalation
  triggers).
- `clankercourts/.state/` post-Phase-4 — exemplar for what a phase's
  autonomous artifacts look like in practice (commits, devlog,
  decisions).

---

## 8. Implementation order (if Path B picked)

| # | Change | LOC est. | Risk |
|---|--------|---------|------|
| 1 | Δ1: decisions.json phase field (schema + test) | ~10 | low |
| 2 | Δ3: assembler `--section decisions --phase N` (+ tests) | ~30 | low |
| 3 | Δ4: port + augment ARCH templates from e2e | ~250 doc | low |
| 4 | Δ2: plan.md escalation triggers enumeration | ~30 doc | low |
| 5 | Sync deltas to CC (`schemas/`, `tools/`, `instructions/`) | bulk copy | low |
| 6 | (After first CC phase under new templates) Δ5: PLAN precondition check | ~15 doc | medium — wait for template validation |

Total: one session for steps 1-5, follow-up session for step 6 after
first CC ARCH authored under the new template.

---

## 9. Cold-start summary (the TL;DR if picking this up fresh)

- **Where we are**: state-lifecycle v1 done; CC Phase 4 EXECUTE done at
  `97e9ea4`; REVIEW + CLOSE dispatched autonomously (first time).
  Expected end-state: CC at `state=audit_boundary, phase=4`.
- **What's next**: make i2c's PLAN action autonomous-capable so future
  CC phases (and other consumer projects) run end-to-end loops with
  phase-boundary review only.
- **What blocks it**: five small framework deltas (Δ1-Δ5 above) +
  ARCH-file discipline (Phasing + Escalation triggers as required
  sections).
- **Suggested next action**: pick Path A (CC-first) or Path B
  (framework-first), then either author CC's remaining module ARCH
  files OR implement Δ1-Δ4 in i2c.
- **Open decisions**: §6 Q1-Q5 above. None block starting.
- **What to read first**: `DESIGN_state_lifecycle_v1.md` for the
  lifecycle context this builds on; `e2e/ref/SPEC_architecture.md` for
  the ARCH template starting point; `clankercourts/ARCH_validator.md`
  for an example of the Phasing pattern already in use.

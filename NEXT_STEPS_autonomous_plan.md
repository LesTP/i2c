# Next Steps — Autonomous-PLAN Readiness (v1)

> Working doc. Shrinks as items close; deleted when FU-32 fully ships.
> Closed-item history lives in `FOLLOWUPS.md` FU-32 progress log.

---

## Status (2026-06-09)

**Goal:** make i2c's PLAN action autonomous-capable so future CC phases
(and other consumer projects) run end-to-end loops with phase-boundary
review only.

**Done:**
- Δ1 — optional `phase: integer` field on `decisions.schema.json` (shipped + tests + back-filled on CC D-18/D-19/D-20).
- Δ2 — explicit escalation-trigger enumeration in `instructions/plan.md` step 2.5 (5 project-general triggers + module-specific reference + how-to-escalate).
- Δ3 — obviated by the new `--section phase-summary --phase N` (broader: same filter + steps + devlog + open items + header).
- Δ4 — ARCH template authored at `i2c/ref/SPEC_architecture.md` + `i2c/ref/GUIDE_architecture.md` with Required / Recommended / Optional taxonomy. Awaiting first-real-use validation on CC's next module.
- `--section phase-summary` — operator's audit_boundary view; ARCH_assembler.md §8b.

**Pending:**
- Δ5 — PLAN precondition check that escalates if ARCH lacks Required sections. Deferred until template stabilizes (≥1 real CC ARCH authored + 1 autonomous phase run under it).
- CC Phase 5+ ARCH authoring — first real use of the template. Validates Δ4 and surfaces template-iteration needs.

**Sequence from here:** CC Phase 5+ ARCH (validates Δ4) → second CC ARCH if useful → Δ5 (cheap, requires template settled).

---

## Δ5. PLAN precondition check on ARCH completeness — DEFERRED

**File:** `instructions/plan.md` (step 1 or step 2)

**Change:** PLAN reads the assembled `Module Contract` section; if it
lacks `## Phasing in This Pilot` or `## Escalation Triggers`, worker
writes a devlog entry and sets `state=audit_escalation` with reason
"ARCH lacks autonomous-PLAN-ready sections — needs collaborative
authoring session per `ref/SPEC_architecture.md`."

Per §6 Q2 (closed in FOLLOWUPS FU-32 progress log): the devlog message
should name which Required section is missing so the operator knows
what to fix without parsing the ARCH.

**Deferral reason:** enforces the Required section list from Δ4. Must
wait for Δ4's section list to be settled by at least one real CC
authoring session + one autonomous phase under that ARCH. Cheap to add
later once we know what worked. ~15 lines of doc + ~10 LOC if we
escalate at the assembler level instead.

---

## Implementation order

| # | Change | LOC est. | Status |
|---|--------|---------|--------|
| 1 | CC Phase 5+ ARCH authored against new template | ~1 ARCH file | upcoming (next session) |
| 2 | Refine template based on (1) friction | ~50 doc edits | upcoming |
| 3 | (After ≥1 CC phase autonomous under new template) Δ5: PLAN precondition check | ~15 doc | deferred |
| 4 | (When Δ5 lands) Delete this file; final FU-32 closure note in FOLLOWUPS | n/a | terminal |

---

## References

- `i2c/ref/SPEC_architecture.md` — template spec
- `i2c/ref/GUIDE_architecture.md` — process guide
- `FOLLOWUPS.md` FU-32 — progress log + closed-item notes
- `ARCH_assembler.md` §8b — `--section phase-summary` contract
- `DESIGN_state_lifecycle_v1.md` — 7-state lifecycle this work builds on

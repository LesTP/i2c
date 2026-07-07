# archive/ — historical design records

Design memos that shaped i2c but are **no longer the current spec**. Kept for
the *why*; don't read them for the *what*.

| File | What it was | Where the current truth lives |
|------|-------------|-------------------------------|
| `DESIGN_governance_v3.md` | The foundational state-vs-content design | `../README.md` (the system), `../ARCH_assembler.md` (assembler), `../DESIGN_packaging_v1.md` §7 (control surface), `../DECISIONS.md` (D1–D21 status) |
| `DESIGN_state_lifecycle_v1.md` | The `(state, blocked)` → 7-state redesign (shipped 2026-06-08) | `../README.md` (Lifecycle states), `../DECISIONS.md` (D-state-1..7) |
| `DESIGN_surface_backends_v1.md` | Telegram surface refactor + per-action multi-backend (shipped 2026-06-27, Part A + Part B) | `../README.md` + `../CHANGELOG.md` (per-action backends, Telegram command set), `../DESIGN_packaging_v1.md` §6/§7 (backend abstraction + control surface) |
| `DESIGN_recovery_v1.md` | The reconcile-first recovery design + Phase-0 empirical sweep (shipped 2026-06-29) | `../README.md` (Recovery section), `../DECISIONS.md` (D-recovery-*), `../CHANGELOG.md`, `../FUTURE_recovery.md` (the still-open `fix` agent) |
| `DESIGN_telemetry_v1.md` | The runner-authored telemetry sidecar (`.state/telemetry.jsonl`) — schema, cost/tier, opt-in `tests_pass` oracle (shipped: increments 1+2) | `../STATUS.md` §7 (telemetry track), `i2c/telemetry.py` + `i2c/data/schemas/telemetry_entry.schema.json` (live impl/schema) |
| `DESIGN_tests_action_v1.md` | The Build-only `tests` action (test/impl separation) — freeze a contract-derived acceptance suite before EXECUTE so `tests_pass` is a real oracle (implemented 2026-07-06) | `../README.md` (the five worker actions), `../DECISIONS.md` (D-tests-1..7), `../WORKFLOW.md` (lifecycle), `i2c/data/instructions/tests.md` (the procedure) |

These are internal records and are not part of the shippable/public doc set.
The live design memo (`../DESIGN_packaging_v1.md`) and the rolling backlog
(`../STATUS.md`) stay at the repo root.

## How to archive a design doc

Do this when a root `DESIGN_*.md` (or `FUTURE_*.md`) is **shipped/implemented**
(its status flips to *Implemented*) and its current *what* now lives in
`../README.md` / `../DECISIONS.md` / code — leaving the memo as a *why* record.
(Exceptions that stay at root: the live design memo `../DESIGN_packaging_v1.md`
and the tracker `../STATUS.md`.)

1. **Move it:** `git mv DESIGN_X.md archive/` (git repo — `git mv`, not `sl mv`).
2. **Add a table row** above: file · what it was (+ "shipped/implemented \<date\>")
   · where the current truth lives.
3. **Prepend an `ARCHIVED (date)` banner** to the moved doc pointing to that
   current truth, using `../` relative links (see `DESIGN_recovery_v1.md`).
4. **Reconcile references** — the load-bearing convention:
   - **Doc → doc** pointers (README, DECISIONS, WORKFLOW, STATUS, other DESIGN
     memos) get the **`archive/`** prefix.
   - **Code comments / schema descriptions** that name the doc are left **bare**
     (they reference by name, not a resolved path; nobody path-resolves them —
     matches every existing entry here).
   - Links *inside* archived docs use `../` back to the root.
5. Remaining follow-on work stays in the `i2c fu` backlog, not in the memo.

# archive/ — historical design records

Design memos that shaped i2c but are **no longer the current spec**. Kept for
the *why*; don't read them for the *what*.

| File | What it was | Where the current truth lives |
|------|-------------|-------------------------------|
| `DESIGN_governance_v3.md` | The foundational state-vs-content design | `../README.md` (the system), `../ARCH_assembler.md` (assembler), `../DESIGN_packaging_v1.md` §7 (control surface), `../DECISIONS.md` (D1–D21 status) |
| `DESIGN_state_lifecycle_v1.md` | The `(state, blocked)` → 7-state redesign (shipped 2026-06-08) | `../README.md` (Lifecycle states), `../DECISIONS.md` (D-state-1..7) |
| `DESIGN_surface_backends_v1.md` | Telegram surface refactor + per-action multi-backend (shipped 2026-06-27, Part A + Part B) | `../README.md` + `../CHANGELOG.md` (per-action backends, Telegram command set), `../DESIGN_packaging_v1.md` §6/§7 (backend abstraction + control surface) |
| `DESIGN_recovery_v1.md` | The reconcile-first recovery design + Phase-0 empirical sweep (shipped 2026-06-29) | `../README.md` (Recovery section), `../DECISIONS.md` (D-recovery-*), `../CHANGELOG.md`, `../FUTURE_recovery.md` (the still-open `fix` agent) |

These are internal records and are not part of the shippable/public doc set.
The live design memo (`../DESIGN_packaging_v1.md`) and the rolling backlog
(`../FOLLOWUPS.md`) stay at the repo root.

---
name: i2c-phase-plan
description: Plan the next i2c phase (supervised) — steps, then freeze the acceptance suite for Build
---
Plan the next phase in **supervised** mode: pause for approval before writing
state, surface the proposed step breakdown for review, ask before ambiguous
decisions.

Run (replace `$PHASE` with `project.json.phase`, or the lowest pending phase id):

```bash
i2c assemble --action plan --phase $PHASE --mode supervised
```

The assembled prompt carries `instructions/plan.md` (supervised framing — the
autonomous-only paragraphs are stripped), the dependency-probe section for
non-leaf modules, project state / scope, architecture, the module contract, the
prior-phase summary, decisions, and gotchas.

Follow the assembled `Instructions`. State writes go through `i2c state`
(`set` / `complete` / `append` / `append-record` / `append-gotcha` /
`update-record`) — pause for confirmation before each `.state/` mutation.

**Final transition depends on the regime (D-tests-1):**

- **Build** → `i2c state set project.json state=tests`, then author the
  phase-level **acceptance suite** as a distinct step (supervised folds test
  authoring into planning, D-tests §9 — the suite is still frozen before
  EXECUTE):
  ```bash
  i2c assemble --action tests --phase $PHASE --mode supervised
  ```
  Write the contract-derived suite under `tests/acceptance/phase_$PHASE/`
  (expected red / partial-red), commit it, then
  `i2c state set project.json state=execute`.
- **Refine / Explore** → `i2c state set project.json state=execute` (no TESTS).

Do not write implementation code during planning. Full assembler contract:
`ARCH_assembler.md`.

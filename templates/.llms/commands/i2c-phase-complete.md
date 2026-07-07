---
name: i2c-phase-complete
description: Close the current i2c phase (supervised) — tests, gotchas, contracts, human gate
---
Close the current phase in **supervised** mode: run phase-level tests (incl. the
Build acceptance suite), integration-check if non-leaf, promote gotchas,
propagate contract changes, close decisions, mark the phase complete, and set the
human gate. Confirm before each write.

Run (replace `$PHASE` with `project.json.phase`):

```bash
i2c assemble --action close --phase $PHASE --mode supervised
```

The assembled prompt carries `instructions/close.md` (supervised framing), the
integration-check section for non-leaf modules, project state, the module
contract, the full phase devlog, and decisions.

Follow the procedure. Notable writes (confirm each first):

- Promote a gotcha:
  ```bash
  i2c state append-gotcha project.json "<one-line lesson>"
  ```
- Close an open decision resolved by this phase:
  ```bash
  i2c state update-record decisions.json --match id=D-N status=closed decision="..."
  ```
- Mark the phase complete:
  ```bash
  i2c state complete phases.json --phase $PHASE
  ```
- Set the gate (halts the loop for human audit):
  ```bash
  i2c state set project.json state=audit_boundary
  ```

Phase-level tests must pass — for a Build phase this **includes the frozen
acceptance suite** under `tests/acceptance/phase_$PHASE/` (must be green).

Leave `state` at `audit_boundary`. Advance to the next phase when ready with
`i2c clear-boundary` (or `i2c state set project.json phase=<N+1> state=plan`).

Full assembler contract: `ARCH_assembler.md`.

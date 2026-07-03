---
name: phase-complete
description: Close the current phase in supervised mode
---

Wrap up the current phase: run phase-level tests, integration-check if
non-leaf, promote gotchas, propagate contracts, close decisions, mark
phase complete, set the human gate. Supervised mode framing: confirm
before each write.

Run:

```bash
i2c assemble --action close --phase $PHASE --mode supervised
```

Replace `$PHASE` with the current phase number from
`.state/project.json.phase`.

The assembled output contains:

- The action procedure from `instructions/close.md` (with supervised
  framing)
- The conditional integration-check section if the module is non-leaf
- Project state, the module contract, the full phase devlog, decisions

Follow the procedure. Notable write operations the procedure references
(confirm each with the user before running):

- Gotcha promotion:
  ```bash
  i2c state append-gotcha project.json "<one-line lesson>"
  ```
- Close an open decision:
  ```bash
  i2c state update-record decisions.json --match id=D-N status=closed decision="..."
  ```
- Contract scan over devlog (read-only):
  ```bash
  jq -c --argjson p $PHASE 'select(.phase == $p and (.contracts // [] | length) > 0)' .state/devlog.jsonl
  ```
- Mark phase complete + set the gate:
  ```bash
  i2c state complete phases.json --phase $PHASE
  i2c state set project.json state=audit_boundary
  ```

After close, project sits at `state=audit_boundary`. The next gate-clear
(by you, or via the i2c bot's `/endphase`) writes one of:

- `set project.json phase=N+1 state=plan` — advance to the next phase
- `set project.json state=done` — declare project terminal

See `archive/DESIGN_state_lifecycle_v1.md` §3 for the full lifecycle model.

Full assembler contract: see `ARCH_assembler.md`.

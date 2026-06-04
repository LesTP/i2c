---
name: phase-complete
description: Close the current phase in supervised mode - replaces e2e's phase-complete
---

Wrap up the current phase: run phase-level tests, integration-check if
non-leaf, promote gotchas, propagate contracts, close decisions, mark
phase complete, set the human gate. Supervised mode framing: confirm
before each write.

Run:

```bash
python3 tools/assemble_context.py --action close --phase $PHASE --mode supervised
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
  python3 tools/state.py append-gotcha project.json "<one-line lesson>"
  ```
- Close an open decision:
  ```bash
  python3 tools/state.py update-record decisions.json --match id=D-N status=closed decision="..."
  ```
- Contract scan over devlog (read-only):
  ```bash
  jq -c --argjson p $PHASE 'select(.phase == $p and (.contracts // [] | length) > 0)' .state/devlog.jsonl
  ```
- Mark phase complete + set the gate:
  ```bash
  python3 tools/state.py complete phases.json --phase $PHASE
  python3 tools/state.py set project.json blocked=true
  ```

Leave `state` as `close`. The next gate-clear (by you, or via codexbot
`/close` once it ships) flips `blocked=false state=plan` for the next
phase.

Full assembler contract: see `ARCH_assembler.md`.

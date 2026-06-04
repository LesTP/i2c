---
name: phase-review
description: Run the end-of-phase review in supervised mode - replaces e2e's phase-review
---

Review all code from the current phase. Supervised mode framing: pause
for direction before applying fixes, surface findings for human triage.

Run:

```bash
python3 tools/assemble_context.py --action review --phase $PHASE --mode supervised
```

Replace `$PHASE` with the current phase number from
`.state/project.json.phase`.

The assembled output contains:

- The action procedure from `instructions/review.md` (with supervised
  framing)
- Project state, the module contract, full architecture, the phase
  devlog (every entry from this phase), decisions

Follow the procedure: read all phase code, categorize findings as Must /
Should / Optional, present them for human triage. Do **not** apply fixes
without explicit direction. If contract drift surfaces (code diverged
from `ARCH_<module>.md`), halt and report — do not silently reconcile.

Skipped Optional items get logged as decisions via:

```bash
python3 tools/state.py append-record decisions.json '{...}'
```

When review is done and fixes have been applied:

```bash
python3 tools/state.py append devlog.jsonl '{...}'     # review entry
python3 tools/state.py set project.json state=close
```

Full assembler contract: see `ARCH_assembler.md`.

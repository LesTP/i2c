---
name: phase-plan
description: Plan the next phase in supervised mode - replaces e2e's phase-plan
---

Plan the next phase. Supervised mode framing: pause for human approval
before writing state, surface the proposed step breakdown for review,
ask before ambiguous decisions.

Run:

```bash
python3 tools/assemble_context.py --action plan --phase $PHASE --mode supervised
```

Replace `$PHASE` with the phase being planned (typically
`project.json.phase` if no record exists yet in `phases.json`, or the
lowest pending phase id otherwise).

The assembled output contains:

- The action procedure from `instructions/plan.md` (with supervised-mode
  framing — the autonomous-only paragraphs are stripped)
- The conditional dependency-probe section if the module is non-leaf
- Project state, project scope, architecture, the module contract, the
  prior phase summary, decisions, gotchas

Follow the procedure in the assembled `Instructions` section. State
writes go through `python3 tools/state.py` (subcommands `set`,
`complete`, `append`, `append-record`, `append-gotcha`, `update-record`).
Pause before each `.state/` mutation for confirmation.

Full assembler contract: see `ARCH_assembler.md`.

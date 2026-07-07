---
name: i2c-phase-review
description: End-of-phase review (supervised) — findings, acceptance-suite integrity, transition to close
---
Review all code from the current phase in **supervised** mode: surface findings
for human triage; pause before applying fixes.

Run (replace `$PHASE` with `project.json.phase`):

```bash
i2c assemble --action review --phase $PHASE --mode supervised
```

The assembled prompt carries `instructions/review.md` (supervised framing),
project state, the module contract, architecture, the full phase devlog, and
decisions.

Follow the procedure:

1. Read the phase's code; categorize findings as **Must / Should / Optional**
   and present them for triage. Apply fixes only with direction.
2. **Acceptance-suite integrity (Build).** If a `tests/acceptance/phase_$PHASE/`
   suite exists, confirm EXECUTE didn't weaken it since the TESTS commit:
   ```bash
   git diff $(git log --pretty=%H --grep="^$PHASE\.tests:" -n 1) HEAD -- tests/acceptance/phase_$PHASE/
   ```
   Any unjustified change (loosened/removed/`xfail`ed assertions) is a Must-fix.
3. If code diverged from `ARCH_<module>.md`, **halt and report** — do not
   silently reconcile (that's a contract change).

Log skipped Optionals as decisions:

```bash
i2c state append-record decisions.json '{...}'
```

When the review is done and fixes are applied:

```bash
i2c state append devlog.jsonl '{...}'          # review entry, action:"review"
i2c state set project.json state=close
```

Full assembler contract: `ARCH_assembler.md`.

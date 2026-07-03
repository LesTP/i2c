---
name: step-done
description: Mark the current step complete - log to devlog, transition if last
---

The user finished a step and wants it recorded. Capture what landed and
write to `.state/` via `i2c state`. Pure write-side; no assembler call.
Supervised mode: you commit the step yourself in your IDE; this captures
that commit and records it. (Autonomous runs commit via the runner — FU-40
— so the hash-capture below is supervised-only.)

1. **Identify** the (phase, step) just completed: the lowest-numbered
   `pending` step for the current phase in `.state/steps.json` is the one you
   just finished (step status is binary `pending`/`complete`; no `in_progress`).

2. **Capture** the commit hash:
   ```bash
   git log -1 --pretty=%h
   ```

3. **Ask the user** for a 1-3 sentence summary of what was done, and
   whether any `ARCH_*.md` files were modified (the `contracts` array).

4. **Mark complete:**
   ```bash
   i2c state complete steps.json --phase N --step M --commit <hash>
   ```

5. **Append devlog entry** (build the JSON from the captured summary +
   contracts + commit + ISO 8601 timestamp):
   ```bash
   i2c state append devlog.jsonl '{"phase":N,"step":M,"action":"execute","outcome":"complete","summary":"...","contracts":[...],"commit":"<hash>","timestamp":"YYYY-MM-DDTHH:MM:SSZ"}'
   ```

6. **If this was the last pending step in the phase**, transition:
   ```bash
   i2c state set project.json state=review
   ```

7. **Pause.** Do not start the next step until told.

Full procedure: `instructions/execute.md`.
Devlog schema: `schemas/devlog_entry.schema.json`.

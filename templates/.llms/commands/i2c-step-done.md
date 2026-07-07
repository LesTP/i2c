---
name: i2c-step-done
description: Record a completed step (supervised) — mark complete, devlog, transition if last
---
Record a step you just finished on an i2c project. Write-side only (no assembler
call); every write goes through `i2c state`.

1. **Identify** the (phase, step): the lowest-numbered `pending` step for the
   current phase in `.state/steps.json` (`i2c status` shows it).
2. **Capture the commit hash** — after you have committed the step's code:
   ```bash
   git log -1 --pretty=%h
   ```
3. **Confirm** with me a 1–3 sentence summary of what landed and any `ARCH_*.md`
   files touched (the `contracts` array).
4. **Mark the step complete:**
   ```bash
   i2c state complete steps.json --phase N --step M --commit <hash>
   ```
5. **Append the devlog entry:**
   ```bash
   i2c state append devlog.jsonl '{"phase":N,"step":M,"action":"execute","outcome":"complete","summary":"...","contracts":[...],"commit":"<hash>","timestamp":"YYYY-MM-DDTHH:MM:SSZ"}'
   ```
6. **If that was the last pending step in the phase**, transition:
   ```bash
   i2c state set project.json state=review
   ```
7. **Pause.** Do not start the next step until told.

Full procedure: `instructions/execute.md`; devlog schema:
`schemas/devlog_entry.schema.json`.

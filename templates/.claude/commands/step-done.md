---
name: step-done
description: Mark the current step complete - log to devlog, transition if last
---

The user finished a step and wants it recorded. Capture what landed and
write to `.state/` via `state.py`. Pure write-side; no assembler call.

1. **Identify** the (phase, step) just completed. Read
   `.state/steps.json` to find the lowest-numbered step with
   `status: "in_progress"` for the current phase (if no in-progress
   marker exists, the lowest `pending` is the just-completed one — the
   worker may not have marked in_progress per FU-1).

2. **Capture** the commit hash:
   ```bash
   git log -1 --pretty=%h
   ```

3. **Ask the user** for a 1-3 sentence summary of what was done, and
   whether any `ARCH_*.md` files were modified (the `contracts` array).

4. **Mark complete:**
   ```bash
   python3 tools/state.py complete steps.json --phase N --step M --commit <hash>
   ```

5. **Append devlog entry** (build the JSON from the captured summary +
   contracts + commit + ISO 8601 timestamp):
   ```bash
   python3 tools/state.py append devlog.jsonl '{"phase":N,"step":M,"action":"execute","outcome":"complete","summary":"...","contracts":[...],"commit":"<hash>","timestamp":"YYYY-MM-DDTHH:MM:SSZ"}'
   ```

6. **If this was the last pending step in the phase**, transition:
   ```bash
   python3 tools/state.py set project.json state=review
   ```

7. **Pause.** Do not start the next step until told.

Full procedure: `instructions/execute.md`.
Devlog schema: `schemas/devlog_entry.schema.json`.

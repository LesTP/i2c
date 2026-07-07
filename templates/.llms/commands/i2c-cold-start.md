---
name: i2c-cold-start
description: Orient on an i2c project — phase, state, pending steps, gotchas, decisions
---
Re-establish context for an i2c project from `.state/` (never guess from memory).

Run:

```bash
i2c status
```

(`--json` for structured output; `i2c next-action` shows the ACTION / NEXT the
state machine would dispatch next.)

Read the output and report:

1. **Where the project is:** phase number and current `state`
   (`plan | tests | execute | review | close | audit_boundary |
   audit_escalation | done`).
2. **What's next:** the lowest-numbered pending step (when in `execute`), or the
   action `i2c next-action` would dispatch.
3. **Recent activity:** the last few devlog entries (`i2c devlog --phase <N>`).
4. **Open questions:** decisions with `status: "open"`
   (`i2c decisions --phase <N>`).
5. **Gotchas** to keep in mind (shown in `i2c status`).

If `state` is `audit_boundary` or `audit_escalation`, do **not** propose work —
surface the gate / escalation and wait for direction.

Full surface: `README.md` (lifecycle states), `WORKFLOW.md`.

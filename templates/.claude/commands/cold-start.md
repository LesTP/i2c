---
name: cold-start
description: Orient on the current project state - replaces e2e's cold-start procedure
---

Show the current project status: phase, lifecycle state, pending steps,
gotchas, recent activity, and open decisions.

Run:

```bash
python3 tools/assemble_context.py --section status
```

Read the output carefully and report what you see. Specifically:

1. **Where the project is right now:** phase number and current state
   (one of `plan`, `execute`, `review`, `close`, `audit_boundary`,
   `audit_escalation`, `done`).
2. **What's next:** lowest-numbered pending step (if in execute), or the
   action the state machine would dispatch.
3. **Recent activity:** what landed in the last 3 devlog entries.
4. **Open questions:** any decisions with `status: "open"`.
5. **Gotchas to keep in mind.**

If the state is a halt state (`audit_boundary`, `audit_escalation`, or
`done`), do not propose work — surface the gate and wait for direction.
See `DESIGN_state_lifecycle_v1.md` §3 for what each halt state means and
the expected human/wrapper recovery write.

Full assembler contract: see `ARCH_assembler.md` §8.

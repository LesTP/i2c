---
name: cold-start
description: Orient on the current project state - replaces e2e's cold-start procedure
---

Show the current project status: phase, action state, blocked flag, pending
steps, gotchas, recent activity, and open decisions.

Run:

```bash
python3 tools/assemble_context.py --section status
```

Read the output carefully and report what you see. Specifically:

1. **Where the project is right now:** phase number, current state
   (plan/execute/review/close), blocked flag.
2. **What's next:** lowest-numbered pending step (if in execute), or the
   action the state machine would dispatch.
3. **Recent activity:** what landed in the last 3 devlog entries.
4. **Open questions:** any decisions with `status: "open"`.
5. **Gotchas to keep in mind.**

If `blocked: true`, do not propose work — surface the gate and wait for
direction.

Full assembler contract: see `ARCH_assembler.md` §8.

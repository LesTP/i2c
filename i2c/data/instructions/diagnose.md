# Diagnose — Classify a Failed or Stuck Iteration

Out-of-band recovery action. The operator dispatches it with
`i2c run --action diagnose --target N` against a specific iteration (default:
the latest). It is the **single entry point** for recovery: you cannot know a
failure is a workflow-drift (`reconcile`) case rather than a code/spec/env case
without diagnosing it first.

This action is **read-only**: it does not change code or `.state/`. It produces
a diagnosis the operator reviews before deciding whether to `reconcile`, fix
code, or hand off.

The assembler has already run the deterministic drift audit and rendered it in
the `## Failure Context` section of your prompt. Start there — do not re-derive
it by hand.

---

## Procedure

### 1. Read the Failure Context

The `## Failure Context` section gives you, for the target iteration:

- the **classification** the deterministic prefilter reached
  (`workflow-drift` / `unknown` / `none`),
- whether any drift is **reconcilable**,
- the target iteration's **exit code + reason** (including whether the exit
  signal was missing/malformed — the #1 real i2c trigger),
- the **Drift Audit**: each state-vs-reality finding, tagged `reconcilable`
  (a deterministic fix exists) or `judgment` (needs a human call).

### 2. If the audit explains the failure → it's workflow-drift

If the Drift Audit lists findings:

- **All findings reconcilable** → the remedy is `reconcile`, not code. Do **not**
  apply the fix here (diagnose never mutates). Recommend the exact command:

  ```
  i2c run --action reconcile --target N
  ```

  and summarize each proposed reconcile in your output.

- **Some findings are `judgment`** (e.g. a recorded commit absent from git, or
  a dirty working tree) → describe what a human must decide before reconciling
  (is the dirty tree real work or leftover instrumentation? is the missing
  commit a rebase artifact?). Do not guess.

### 3. If there's no drift but the iteration failed → classify the cause

When classification is `unknown` (the target iteration failed but the audit
found no drift), read the iteration transcript (`i2c logs --iter N`), the
triggering escalation entry (shown under the Project Context), the failing
commit's diff, and any test output. Bucket the root cause:

- **`env`** — platform/tooling limit (PATH, missing binary, network). Note the
  operator fix; this is usually not a code change.
- **`code`** — a real bug needing a change. State the root cause and the
  smallest proposed fix and the files involved. Hand to the human / a future
  `fix` action; **do not** implement it here (v1 defers code repair to the
  REVIEW regime + normal dev).
- **`spec`** — the work is underspecified / needs a design decision. Say so
  plainly; **never fabricate a fix** (same scope discipline PLAN follows).

A **malformed/missing exit signal** also lands here as `unknown` (the runner
records it as `exit=2`). When the audit is otherwise clean, the work likely
landed fine and only the loop's *read* of the result was lost — say so, and
recommend the operator simply resume (`i2c run`).

### 4. If classification is `none`

No drift and no failed iteration: report that the project looks consistent and
the operator can simply resume (`i2c run`).

### 5. Output the diagnosis

Write your diagnosis as prose: the class, the root cause, and the recommended
next step (`reconcile` / fix / hand-off / resume). Then emit the exit signal.

Exit code is `0` — diagnose is an analysis action; producing a diagnosis is
success even when the underlying failure is severe. Emit `EXIT 2` only if the
failure context was genuinely unavailable (e.g. no target iteration and no
state to read).

---

## What this action does NOT do

- Change code (that's a future `fix` / the REVIEW regime)
- Mutate `.state/` (that's `reconcile`)
- Mark the failed step complete
- Fabricate a fix for an underspecified (`spec`) failure

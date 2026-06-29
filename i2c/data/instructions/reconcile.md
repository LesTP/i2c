# Reconcile — Clear Workflow-State Drift

Out-of-band recovery action. The operator dispatches it with
`i2c run --action reconcile --target N` **after** reviewing a `diagnose` that
found reconcilable workflow-drift. Running this command is itself the human gate:
do not invent fixes the diagnosis did not propose.

Reconcile resolves the common case where the loop did the work but `.state/`
disagrees with reality — a commit landed but its step stayed `pending`, the last
step completed but `project.state` never advanced, a phase was marked complete
but the boundary gate was never set. It **finishes the bookkeeping** so the loop
can resume; it does not write code.

The assembler has already run the deterministic drift audit and rendered it in
the `## Failure Context` section, with an exact proposed reconcile for each
reconcilable finding. Apply those — do not improvise.

---

## Procedure

### 1. Read the Drift Audit

In the `## Failure Context` section, each finding is tagged `reconcilable` (a
deterministic fix exists, with a `proposed reconcile` line) or `judgment` (needs
a human call). Act only on the `reconcilable` ones.

### 2. Apply each reconcilable proposal — verbatim, via `i2c state`

Every mutation goes through the sanctioned, schema-validated `i2c state` path.
Never hand-edit `.state/` files. Apply exactly what the proposal says:

- **Commit exists but the step is still `pending`** (the canonical toolkit-5.3
  case) — mark the step complete with the discovered commit:

  ```
  i2c state complete steps.json --phase 5 --step 3 --commit 5b1fb2b
  ```

- **All steps complete but `project.state` is still `execute`** — advance:

  ```
  i2c state set project.json state=review
  ```

- **Phase marked complete but the gate was never set** — set the boundary:

  ```
  i2c state set project.json state=audit_boundary
  ```

Verify each proposed commit really is that step's work before recording it
(`git show <hash> --stat`). If a proposed commit does not match the step's
intent, treat it as a judgment call (step 4) instead of applying it.

### 3. Do NOT over-reach

- Do **not** mark a step complete when the step's work is genuinely unfinished
  (a code blocker). Reconcile clears *workflow* drift so the loop can re-attempt
  the action; it does not paper over missing work.
- Do **not** act on `judgment`-tagged findings (a recorded commit absent from
  git, an unexplained dirty tree). Leave those for the operator.

### 4. Commit the reconciliation

The `.state/` writes above need a commit so the corrected position is durable:

```
git add .state/
git commit -m "reconcile: mark step 5.3 complete (commit 5b1fb2b); advance to review"
```

Always pass `-m`. The full prohibitions on interactive git commands apply (see
the Shell command discipline section in your Worker Contract).

### 5. Emit the exit signal

- If you applied every reconcilable finding and no judgment-class drift remains:
  `EXIT 0`, reason summarizing what you reconciled. The operator resumes with
  `i2c run`.
- If drift remains that needs a human decision (judgment-class findings, or a
  proposed commit you could not confirm): `EXIT 2`, reason naming what still
  needs the operator.

---

## What this action does NOT do

- Write or fix code (that's the REVIEW regime / a future `fix`)
- Mark an unfinished step complete to silence a code blocker
- Apply judgment-class findings without operator review
- Advance `project.json.phase` (that stays the operator's call at the boundary)

# Refine — Single-Shot Backlog Item

Out-of-band refine-tier action (DESIGN_refine_v1.md §12). The operator dispatches
it with `i2c refine <fu-id>` to resolve **one** backlog item from
`.state/followups.json` in a single invocation. There is no phase lifecycle:
refine is **sub-phase** — no PLAN/EXECUTE/REVIEW/CLOSE, no `phases.json` record,
no `project.state` change.

This is distinct from the EXECUTE **Refine *regime*** (a planned, time-budgeted
phase inside the lifecycle). Here there is no phase at all — you make one focused
change toward the follow-up's goal and exit.

The item you are working on is rendered in the `## Refine Target` section (its
kind, title, context, trigger, and any declared files). Work only that item; do
not expand it into a phase-sized effort — if it needs that, exit 2 and say so.

---

## Procedure

### 1. Read the Refine Target

The `## Refine Target` section holds the follow-up's `kind`, `title`, `context`,
`trigger`, and declared `files` (read-hints). Open the declared files and any
others you need to understand the change. Let the `kind` shape the work (a
`prose` pass rewrites worker-facing text; a `dead-surface` removal cuts an
unused surface; a `doc-reconciliation` corrects docs against ground truth).

### 2. Make the minimal change

Prefer the simplest change that satisfies the follow-up. Stay within its scope;
reuse existing code; preserve existing structure unless the item is explicitly a
refactor. Do not fold in unrelated cleanups — capture those as new follow-ups if
worth tracking, don't do them here.

### 3. Run tests if the project has them

If there is a test suite relevant to your change, run it and confirm green before
exiting. Refine has no frozen acceptance suite (that is the Build-only `tests`
action), so this is a sanity check, not a graded oracle.

### 4. Do NOT commit — the runner does

Leave your edits in the working tree; do **not** run `git`. After you exit, the
deterministic runner commits the files you changed (fenced off from any unrelated
working-tree changes) as `refine(<kind>): <fu-id> <your devlog summary>`. This
removes the interactive-hang / wrong-scope / forgotten-commit hazards.

### 5. Do NOT touch phase state

Never write `phases.json`, `steps.json`, or `project.json`. Refine is sub-phase;
the runner asserts these files are byte-unchanged after your run and halts if they
are not. The only `.state/` write you make is the devlog entry in step 6.

### 6. Append a devlog entry

One `refine` entry, JSON envelope matching `schemas/devlog_entry.schema.json`.
Required fields: `phase` (**null** — refine is phase-less), `step` (**null**),
`action` (`"refine"`), `outcome`, `summary`, `timestamp`. Set `kind` to the
Refine Target's kind. Optionally set `iteration`.

```bash
i2c state append devlog.jsonl '{
  "phase": null,
  "step": null,
  "action": "refine",
  "kind": "prose",
  "outcome": "complete",
  "summary": "Rewrote the threat-framed passages in WORKER_SPEC to reason-first.",
  "timestamp": "2026-07-13T08:30:00Z"
}'
```

`timestamp` is ISO 8601 UTC — use the current time. For a `summary` containing
`$` or newlines, write the JSON to a file and use `--from-file <path>` to avoid
shell-quoting hazards.

### 7. Emit the exit signal

- Change made and (if applicable) tests green: `EXIT 0`, reason summarizing what
  you changed. The runner uses your reason as the follow-up's resolution and
  closes it with `i2c fu close`.
- Blocked, or the item turned out to need phase-scale work / human judgment:
  `EXIT 2`, reason naming what is needed. The runner leaves the follow-up open.

---

## What this action does NOT do

- Touch phase state — never write `phases.json` / `steps.json` / `project.json`
  (refine is sub-phase; the runner enforces this).
- Run `git` / commit (the runner owns the `refine(<kind>): <fu-id>` commit).
- Close the follow-up (the runner closes it on `EXIT 0` using your reason).
- Run PLAN / REVIEW / CLOSE gates — a change big enough to want those is a
  Refine-*regime* phase (DESIGN_refine_v1.md §3), not an ad-hoc refine.

---

## Examples

<!-- assembler:omit_in_prompt -->

**A prose pass (`kind: prose`).** Refine Target points at `WORKER_SPEC.md` with
trigger "threat-framing drift". You rewrite the flagged passages in place, run no
tests (docs only), append a `refine`/`prose` devlog row, and `EXIT 0` with reason
"reason-first rewrite of WORKER_SPEC threat passages". The runner commits
`refine(prose): FU-36 reason-first rewrite …` and closes FU-36.

**A blocked item.** Refine Target is a `dead-surface` removal, but tracing usage
shows a live consumer you cannot safely cut without a broader change. You make no
edit, append no devlog row, and `EXIT 2` with reason "surface still consumed by
X; needs a phase to migrate callers first". The runner leaves the FU open.

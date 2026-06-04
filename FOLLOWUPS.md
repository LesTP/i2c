# i2c Followups — Design Notes and Tooling Gaps

Running list of items deferred or noted during build sessions. Lower-priority
than the rollout plan phases; revisited when triggers surface (real friction,
Phase 2 pilot feedback, or downstream work that needs the gap closed).

Distinct from `FUTURE_waymark.md` (a roadmap for one specific deferred
initiative) — this is the catch-all log of "noticed during the build, doesn't
block the current deliverable, worth tracking."

ID scheme: `FU-N` (Follow-Up). Status: `open` / `accepted` (will do, scheduled
to a phase) / `closed` / `wontfix`.

---

## Tooling — state.py CLI gaps

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-1 | No subcommand to mark a step `in_progress` | open | `steps.schema.json` allows `status: "in_progress"`. `state.py complete` jumps straight to `complete`. `state.py set` only works on JSON object files, not arrays. Workaround documented in `instructions/execute.md`: append a devlog `outcome: "partial"` entry as a "started" signal. | Phase 2 supervised-mode pilot surfaces a real "where am I" question, OR a concurrent observer (waymark, codexbot StateReader) wants live in-progress visibility. Add `state.py mark steps.json --phase N --step M --status in_progress`. |
| FU-2 | No subcommand to append a new step mid-phase | open | `state.py` has no `append-step` op. Adjacent work discovered during EXECUTE has to be deferred to next PLAN action via a `Deferred:` prefix in the devlog `summary`. This is intentional — step authoring is PLAN's responsibility — but it's an honest constraint worth tracking. | Phase 2 pilot shows the `Deferred:` flag pattern is too lossy or PLAN doesn't reliably pick deferred items up. Then add `state.py append-step steps.json --phase N --title "..."`. |
| FU-3 | `state.py set` only handles JSON object files, not arrays | open | Reasonable for project.json (the only "set"-style consumer today), but means there's no atomic way to mutate an arbitrary field on a single record in steps.json or phases.json or decisions.json. Workers can only call `complete` (which is record-shaped) on those files. | A real worker scenario needs to update a non-status field on a record (e.g., adding a `notes` field, fixing a typo'd title). Then either generalize `set` with a `--match` selector or add purpose-built subcommands. |
| FU-4 | No subcommand to mark a phase blocked/closed without `set` syntax | open | `state.py set project.json blocked=true` works but is dense. A named op like `state.py block project.json --reason "..."` would be more readable and could log a structured reason field. | Low priority — current syntax is fine for autonomous use. Revisit if supervised UI wants a richer block flow. |

## Tooling — assembler (Phase 1.3, not yet started)

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-5 | Conditional-section detection needs `dependencies` lookup | open | DESIGN D19 / D-prose-1: assembler includes dep-probe section in plan + integration-check section in close when the current phase's `dependencies` array is non-empty. Fixture already demonstrates this (Phase 4 `orchestrator` depends on `event_store`). Assembler needs to read `phases.json`, find the current phase, evaluate `len(dependencies) > 0`. | Phase 1.3 (assembler build). |
| FU-6 | Smoke test doesn't exercise the non-leaf Phase 4 path | open | Phase 4 was added to the fixture to show the non-leaf shape, but `smoke_test.py` only walks Phase 2. The assembler conditional-section logic in Phase 1.3 should add a smoke-test scenario that asks the assembler for the plan action against Phase 4 and confirms the dep-probe section appears. | Phase 1.3. |
| FU-7 | `exit_signal.schema.json` is permissive (additionalProperties: true) | open | Schema is deliberately loose for v1 because the full exit signal contract hasn't been validated against real runner needs. Plan calls for tightening once Phase 3 (autonomous mode tooling) defines the actual fields. | Phase 3 (autonomous mode). At that point: set additionalProperties:false, lock the exact field set, validate the runner emits what it claims. |

## Prose — instructions, WORKER_SPEC, adapters

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-8 | execute.md commit-format suggestion (`phase.step: title`) is not enforced anywhere | open | The prose says "default commit message format `phase.step: short title`" but nothing validates it. A pre-commit hook or a `state.py complete --validate-commit-msg` check could enforce. | Phase 2 pilot reveals workers drift from the format and downstream tooling (codexbot `/diff <phase>`, waymark commit-by-phase view) needs consistency. |
| FU-9 | Refine regime in execute.md uses `step: null` for devlog entries | open | The schema allows `step: null` and the prose recommends it for Refine iterations. But there's no constraint that ties a Refine entry to *which* iteration (no iteration counter field). The commit message carries it (`14.iter3:`) but the structured data doesn't. | Phase 2 pilot does enough Refine work that iteration-by-iteration analytics matter. Add `iteration: int` optional field to `devlog_entry.schema.json`. |
| FU-10 | Production-incident anecdotes in (future) WORKER_SPEC §3 are e2e-vintage | open | Per D-prose-8 the Codex 105k-char and Claude 5-3 incidents stay verbatim — they have pedagogical value. But once i2c has its own incidents, those should be added or substituted to keep the pedagogy current. | i2c accumulates 2+ documented loop-discipline failures of its own. Add a refresh pass to WORKER_SPEC §3. |
| FU-11 | Per-file JSON-example validation isn't automated | open | Today the only check that `instructions/*.md` examples validate against the schemas is a manual one-off Python snippet during authoring. A test that lifts every fenced JSON block in `instructions/**.md` and validates against the registered schema would catch drift. | Either after all 4 instruction files are written (worth-it threshold), or when a schema change breaks an instruction example silently. Pattern: parse markdown code fences, route by surrounding prose hint or filename hint. |

## Cross-platform

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-12 | Multi-line JSON in `state.py append` assumes bash-style heredoc / single-quote quoting | open | The examples in `instructions/execute.md` use `'{ "key": value }'` with embedded newlines. PowerShell quoting rules differ — backtick-vs-backslash, $-interpolation. Workers running on Windows shells will need an adapter-side note or a `state.py append --from-file <path>` alternative. | Phase 2 pilot on Windows (clankercourts is being developed in the user's Windows workspace). Add `--from-file` flag or document PowerShell-safe quoting in `CODEX.md` / `CLAUDE.md` adapter Tool Rules. |

## Closed / decided

(Items here once resolved, with the resolution noted. Keeps the historical
context without crowding the open list.)

| ID | Resolution |
|----|------------|
| — | — |

---

## How to use this file

- When you notice a gap or design note during a build session, add it as a new `FU-N` row in the right section. One-line title, brief context, explicit trigger.
- When you act on one, move it to **Closed / decided** with a one-line resolution note. Don't delete — historical context is cheap and useful.
- Reference these IDs from instructions/, plans, or commit messages when relevant.
- This file does not gate any phase. It is a backlog, not a blocker list.

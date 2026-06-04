# i2c Followups — Design Notes and Tooling Gaps

Running list of items deferred or noted during build sessions. Lower-priority
than the rollout plan phases; revisited when triggers surface (real friction,
Phase 2 pilot feedback, or downstream work that needs the gap closed).

Distinct from `FUTURE_waymark.md` (a roadmap for one specific deferred
initiative) — this is the catch-all log of "noticed during the build, doesn't
block the current deliverable, worth tracking."

ID scheme: `FU-N` (Follow-Up). Status: `open` / `accepted` (will do, scheduled
to a phase) / `partially closed` / `closed` / `wontfix`.

---

## Cold-start summary (next session entry point)

**Where we are (2026-06-04):** Phase 1 complete end-to-end. Schemas, write
CLI (`state.py`), validation, instruction files for all four actions,
worker spec, adapter templates, slash-command wrappers, and the **context
assembler** (`tools/assemble_context.py`) all ship and are covered by 160
passing tests. Smoke test (`examples/smoke_test.py`) walks
`state.py` + the assembler end-to-end against the fixture in
`examples/initial_state/`.

**What's next (priority order):**

1. **Phase 2 — supervised pilot on clankercourts.**
   - Bootstrap clankercourts as an i2c project: copy `WORKER_SPEC.md`,
     `instructions/`, `schemas/`, `tools/`, `CLAUDE.md` (filled in), and
     `templates/.claude/commands/` from this repo into `p:\shared\clankercourts\`.
   - Author `PROJECT.md`, `ARCHITECTURE.md`, the first `ARCH_<module>.md`.
   - Initialize `.state/` (see README "Bootstrap a new i2c project").
   - Run `/cold-start`, `/phase-plan`, `/phase-review`, `/phase-complete`
     against the real project. Anything awkward → log as a new FU here.
2. **Phase 3 — autonomous loop tooling.** `tools/state_machine.sh` first
   (ACTION/NEXT dispatch), then `run-iteration.sh` updates to pipe
   assembler output into `claude -p` / `codex exec`. Add `--next` flag
   to the assembler so the runner can pass NEXT explicitly (D-impl-3
   anticipated this). Then tighten `exit_signal.schema.json` (FU-7).
3. **Phase 4 — codexbot StateReader + dispatcher.** Telegram/Discord
   control surface over `.state/`. Mostly downstream of Phase 3.
4. **Phase 5 (optional) — diplomat / e2e migration.** Deferred.

**Quick orientation commands** (from the project root, after Phase 2
bootstraps clankercourts):

```powershell
$env:PYTHONIOENCODING="utf-8"
python tools\assemble_context.py --section status
python -m unittest discover -s tests
python examples\smoke_test.py
```

**Canonical references:**
- Build status: `README.md` table
- Assembler contract: `ARCH_assembler.md`
- Architectural rationale: `DESIGN_governance_v3.md`
- Workflow diagrams: `WORKFLOW.md`
- This file: the rolling backlog

---

## Tooling — state.py CLI gaps

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-1 | No subcommand to mark a step `in_progress` | open | `steps.schema.json` allows `status: "in_progress"`. `state.py complete` jumps straight to `complete`. `state.py set` only works on JSON object files, not arrays. Workaround documented in `instructions/execute.md`: append a devlog `outcome: "partial"` entry as a "started" signal. | Phase 2 supervised-mode pilot surfaces a real "where am I" question, OR a concurrent observer (waymark, codexbot StateReader) wants live in-progress visibility. Add `state.py mark steps.json --phase N --step M --status in_progress`. |
| FU-2 | No subcommand to append a new step mid-phase | partially closed | `state.py append-record steps.json '{...}'` exists (covers PLAN-time and in principle mid-phase). The original framing was specifically about EXECUTE-time step creation; `instructions/execute.md` still funnels deferred work through devlog `Deferred:` flags so PLAN owns step authoring. If a real workflow demands runtime step append from EXECUTE, lift the restriction in execute.md prose — the CLI now supports it. | Phase 2 pilot shows execute-time step append is wanted; loosen the prose. |
| FU-3 | `state.py set` only handles JSON object files, not arrays | partially closed | `update-record` (added for review/close authoring) covers single-record updates on array files via `--match KEY=VALUE`. Generic-set-on-array still isn't supported (e.g., updating fields on N records at once). | A real workflow needs bulk update across multiple records (rare). Until then, `update-record` covers the gap that drove FU-3 in practice. |
| FU-4 | No subcommand to mark a phase blocked/closed without `set` syntax | open | `state.py set project.json blocked=true` works but is dense. A named op like `state.py block project.json --reason "..."` would be more readable and could log a structured reason field. | Low priority — current syntax is fine for autonomous use. Revisit if supervised UI wants a richer block flow. |
| FU-14 | No read-side query helper in `state.py` (e.g., `state.py query devlog.jsonl --where 'contracts != []'`) | accepted (deferred) | The assembler (Phase 1.3) exposes pre-formatted views: `--section devlog --phase N` gives a bulleted phase tail, `--section status` an orientation snapshot. Ad-hoc queries still fall back to `jq`. Per ARCH §2 / §10, the assembler intentionally does not absorb general-purpose queries. | Phase 2 pilot reveals a repeated query pattern worth absorbing into `--section`. |

## Tooling — assembler (`assemble_context.py`)

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-7 | `exit_signal.schema.json` is permissive (`additionalProperties: true`) | open | Schema is deliberately loose for v1 because the full exit signal contract hasn't been validated against real runner needs. | Phase 3 (autonomous mode). At that point: set `additionalProperties:false`, lock the exact field set, validate the runner emits what it claims. |
| FU-15 | `Module Contract` section is hard-required when `phases.json[current].module` is set | open | Per ARCH §11.1 / §4.1, the assembler exits 1 if a phase declares a `module` field but no `ARCH_<module>.md` file exists. This is strict by spec — useful for catching missing contracts early — but can bite during the very first plan iteration of a new module (when the ARCH file is being authored as part of the plan itself). Today the workaround is to write a stub ARCH file first. | Phase 2 pilot has a session where stub-first feels like ceremony. Then either soften to "warn + placeholder" or add a `--skip-module-contract` escape. Do **not** add until the friction is real. |
| FU-16 | Available Modules ARCHITECTURE.md fallback is naive | open | When the adapter's `## Available Modules` section is placeholder-only, the assembler grabs `## Implementation Sequence` from `ARCHITECTURE.md` verbatim and surfaces its body. If projects use richer Implementation Sequence tables (extra columns, longer prose), the rendered Available Modules section will be noisy. | Phase 2 / 3 pilots show real-world Implementation Sequence tables overflow the section. Tighten the fallback to extract only module names, or document a project convention for the fallback shape. |
| FU-17 | `--phase` accepted (but ignored) with `--section status` | open | Per ARCH §8, `--section status` does not accept `--phase` — it always reports on `project.json.phase`. The implementation accepts `--phase` at the argparse layer and silently uses `project.json.phase` in `build_section_status`. Functionally correct but not strict per spec. | Either Phase 2 pilot surfaces confusion (operator passes `--phase 3` and expects status for phase 3), OR a spec-compliance pass. Fix is one branch in `_validate_args`: reject `--phase` when `--section == status`. |
| FU-18 | Assembler tests slow on Windows network share | open | `tests/test_assemble_context.py` runs in ~60s on `\\192.168.0.50\shared\...`. Primary cost: `TempProject(with_framework=True)` copies the full `instructions/` directory and WORKER_SPEC + both adapters per test invocation. | If iteration cost becomes painful, refactor `TempProject` to copy only what each test class needs (most renderer tests don't read instructions), or cache the framework copy per pytest session. Not a correctness issue. |

## Prose — instructions, WORKER_SPEC, adapters

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-8 | execute.md commit-format suggestion (`phase.step: title`) is not enforced anywhere | open | The prose says "default commit message format `phase.step: short title`" but nothing validates it. A pre-commit hook or a `state.py complete --validate-commit-msg` check could enforce. | Phase 2 pilot reveals workers drift from the format and downstream tooling (codexbot `/diff <phase>`, waymark commit-by-phase view) needs consistency. |
| FU-9 | Refine regime in execute.md uses `step: null` for devlog entries | open | The schema allows `step: null` and the prose recommends it for Refine iterations. But there's no constraint that ties a Refine entry to *which* iteration (no iteration counter field). The commit message carries it (`14.iter3:`) but the structured data doesn't. | Phase 2 pilot does enough Refine work that iteration-by-iteration analytics matter. Add `iteration: int` optional field to `devlog_entry.schema.json`. |
| FU-10 | Production-incident anecdotes in WORKER_SPEC §3 are e2e-vintage | open | Per D-prose-8 the Codex 105k-char and Claude 5-3 incidents stay verbatim — they have pedagogical value. But once i2c has its own incidents, those should be added or substituted to keep the pedagogy current. | i2c accumulates 2+ documented loop-discipline failures of its own. Add a refresh pass to WORKER_SPEC §3. |
| FU-11 | Per-file JSON-example validation isn't automated | open | The only check that `instructions/*.md` examples validate against the schemas is manual. A test that lifts every fenced JSON block in `instructions/**.md` and validates against the registered schema would catch drift. | A schema change breaks an instruction example silently. Pattern: parse markdown code fences, route by surrounding prose hint or filename hint. |

## Cross-platform

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-12 | Multi-line JSON in `state.py append` assumes bash-style heredoc / single-quote quoting | open | The examples in `instructions/execute.md` use `'{ "key": value }'` with embedded newlines. PowerShell quoting rules differ — backtick-vs-backslash, $-interpolation. Workers running on Windows shells will need an adapter-side note or a `state.py append --from-file <path>` alternative. | Phase 2 pilot on Windows (clankercourts is being developed in the user's Windows workspace). Add `--from-file` flag or document PowerShell-safe quoting in `CODEX.md` / `CLAUDE.md` adapter Tool Rules. |

---

## Closed / decided

Items resolved, with a one-line resolution note. Historical context is cheap.

| ID | Resolution |
|----|------------|
| FU-2 (CLI side) | `append-record` subcommand added when authoring `instructions/plan.md` surfaced that PLAN needs to write new records to all three JSON-array files (steps, phases, decisions). Generic over per-record-type subcommands per design discussion: same shape as the existing `append` for JSONL, schema-validated, atomic. The prose-side framing (EXECUTE defers to PLAN via `Deferred:`) is unchanged — the CLI gap that drove FU-2 is closed but the design rule it embodied stands. |
| FU-5 | Phase 1.3 `tools/assemble_context.py` implements the conditional-section marker mechanism per ARCH §7 (evaluator registry, `requires=dependencies_nonempty`, `autonomous_only`, `supervised_only`). Markers in `instructions/plan.md` and `instructions/close.md` strip deterministically. |
| FU-6 | Phase 1.3 tests cover both leaf and non-leaf paths; `examples/smoke_test.py` also exercises `--section status` end-to-end. |
| FU-13 | `update-record FILE --match KEY=VALUE field=value ...` added when authoring `instructions/review.md` + `close.md` surfaced the need to close open decisions and flip phase status mid-flight. Generic: matches one record by a single key=value (errors on no-match or multi-match), updates one-or-more fields, validates the whole array, atomic write. Sibling to `append-record` in pattern. |

---

## How to use this file

- When you notice a gap or design note during a build session, add it as a new `FU-N` row in the right section. One-line title, brief context, explicit trigger.
- When you act on one, move it to **Closed / decided** with a one-line resolution note. Don't delete — historical context is cheap and useful.
- Reference these IDs from instructions/, plans, or commit messages when relevant.
- This file does not gate any phase. It is a backlog, not a blocker list.
- When picking up after a break, read the **Cold-start summary** above first — it captures where things were left and what to do next.

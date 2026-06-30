# ARCH: Context Assembler (`assemble_context.py`)

**Status:** Contract spec. Implementation deferred to Phase 1.3.
**Lives at:** `tools/assemble_context.py`
**Supersedes:** Scattered specifications in `archive/DESIGN_governance_v3.md` §3 / §7.5 / Appendix B, `WORKFLOW.md`, instruction files, `README.md`. Where this contract and any earlier file disagree, this contract wins for assembler behavior. The earlier files remain useful as design rationale.

---

## 1. Purpose

The assembler builds the structured prompt that workers receive. It sits between the state machine (which decides what action to perform) and the worker (which performs it). It reads:

- Worker spec, action instructions, and the adapter file (markdown governance)
- `.state/*.json` and `.state/devlog.jsonl` (structured state)
- `PROJECT.md`, `ARCHITECTURE.md`, `ARCH_<module>.md` (project narrative)

…and emits a single markdown document on stdout that contains everything the worker needs for the current action. The worker reads no governance files directly.

The same binary serves two callers:

- **Autonomous mode:** the runner calls `--action $ACTION --phase $PHASE` per invocation; output is piped into `claude -p` / `codex exec`.
- **Supervised mode:** the human or their assistant calls `--section status` for orientation or `--action $ACTION --phase $PHASE --mode supervised` for per-action context. Output goes to the terminal or a Devmate slash-command wrapper.
- **Mid-step (multi-step):** the worker may call `--section X` between steps for fresh governance context (see §10).

---

## 2. Non-Goals

The assembler is intentionally narrow. It does **not**:

| Non-goal | Where this is handled instead |
|----------|-------------------------------|
| Write or mutate `.state/` | `tools/state.py` |
| Call LLMs / dispatch the worker | The runner (`run-iteration.sh`) |
| Decide the next ACTION / NEXT | `tools/state_machine.py` |
| Validate the worker's exit signal | The runner (against `schemas/exit_signal.schema.json`) |
| Advance `project.json.phase` | Human / orchestrator on gate-clear, or PLAN action's `state.py` calls |
| Re-decide whether a section *should* apply | Worker — but only for code (which step to do, what tests to run); never for governance (whether to skip the dep-probe). Governance branching is deterministic and lives in this contract. |
| Make assembly recipes data-driven | Recipe is Python code (matrix + evaluators). Data-driven config is deferred until complexity warrants it (per D19). |
| Stream output | Synchronous full-buffer write to stdout. |
| Cache or watch files | Reads happen fresh on every invocation. |
| Implement read-side queries on devlog/decisions | `jq` is the answer today; FU-14 tracks whether to absorb canned queries here later. |

---

## 3. CLI Surface

### 3.1 Subcommand summary

```
assemble_context.py --action ACTION --phase N [--mode {autonomous,supervised}] [--emit {full,system,user}]
assemble_context.py --section architecture
assemble_context.py --section module --module NAME
```

`--action` and `--section` are mutually exclusive. Exactly one must be specified.

This interface is the assembler program's own argparse surface. Operators and
workers reach it through the `i2c` console command as `i2c assemble …` (a
passthrough); the runner invokes `assemble_context.py` directly.

### 3.2 Flags

| Flag | Required when | Accepted values | Default |
|------|---------------|-----------------|---------|
| `--action` | building a full prompt | `plan`, `execute`, `review`, `close`, `diagnose`, `reconcile` | — |
| `--phase` | with `--action` (always) | positive integer | — |
| `--mode` | optional with `--action` | `autonomous`, `supervised` | `autonomous` |
| `--section` | building a single section | `architecture`, `module` | — |
| `--module` | only with `--section module` | non-empty module name | — |
| `--target` | optional with `--action` (recovery) | positive integer | — |
| `--step-budget` | optional with `--action` | positive integer | `1` |
| `--emit` | optional with `--action` | `full`, `system`, `user` | `full` |

`diagnose` / `reconcile` are the out-of-band **recovery** actions
(archive/DESIGN_recovery_v1.md): they render a `## Failure Context` Region-3 section
(the deterministic drift audit for `--target`, via a lazy `control.diagnose`
call — the one place assembly invokes the read-only `git` drift audit) and emit
no Next State. `--target` selects which iteration's failure context to render.

The mode flag is *only* meaningful with `--action`. Specifying `--mode` together with `--section` is a CLI argument error. The same holds for `--emit`: it is only meaningful with `--action`, and a non-default `--emit` with `--section` is a CLI argument error.

`--emit` selects which part of the assembled prompt to write to stdout (FU-35, prompt-cache support). `full` (default) is the whole prompt and is byte-identical to pre-FU-35 output. `system` is the cache-stable prefix — the WORKER CONTRACT and TOOL RULES regions (§6) — which the runner routes through Claude Code's `--append-system-prompt-file` so it can be prompt-cached and reused across consecutive same-phase iterations. `user` is the per-iteration body — the PROJECT CONTEXT and ACTION CONTEXT regions plus the Output Contract reminder. The split is exact: `full == system.rstrip() + "\n\n" + user`. Only the claude backend splits; codex sends `full` on stdin and relies on server-side prefix caching.

`--step-budget` controls whether the `multi_step_only`-marked subsections in `WORKER_SPEC.md` (the multi-step LOOP, "Loop discipline — multi-step only", and the production-incident anecdotes) appear in the assembled prompt. Default `1` (the v1 runner's value) strips them; values > 1 keep them, in preparation for the multi-iteration loop (Phase 3.B/C).

### 3.3 Output

- stdout — the assembled prompt, or the `--emit`-selected part of it (markdown, UTF-8, LF line endings, trailing newline).
- stderr — error messages and structured warnings on degraded sections (see §11).

### 3.4 Exit codes

| Code | Meaning |
|------|---------|
| 0 | Successful assembly (including assemblies with degraded optional sections marked inline) |
| 1 | Required input missing, unreadable, or schema-invalid (see §11.1) |
| 2 | CLI argument error (unknown flag, mutually exclusive flags both set, missing required flag) |

### 3.5 Invocation examples

```bash
# Autonomous runner, per-action
i2c assemble --action execute --phase 11
i2c assemble --action plan --phase 12

# Prompt-cache split (FU-35): cache-stable prefix vs per-iteration body
i2c assemble --action execute --phase 11 --emit system
i2c assemble --action execute --phase 11 --emit user

# Supervised assistant, per-action
i2c assemble --action plan --phase 12 --mode supervised

# Single-section context (worker mid-step or operator)
i2c assemble --section architecture
i2c assemble --section module --module event_store

# Operator views moved to the i2c CLI (Phase 3a, see §8):
#   i2c status        i2c phase-summary --phase N        i2c devlog --phase N
```

---

## 4. Section Catalog

The canonical, normative list. Every name uses **Title Case**. Every reference inside any instruction file, WORKER_SPEC, adapter, or README must match these names exactly.

### 4.1 Section table

| Canonical name | Source | Per-action inclusion | Conditional | Notes |
|----------------|--------|:--------------------:|-------------|-------|
| Identity | `WORKER_SPEC.md` §1 | all | — | Subsection of Worker Contract banner |
| Main Loop | `WORKER_SPEC.md` §2 | all | — | Subsection of Worker Contract banner. `--mode supervised` strips autonomous-only subsections (see §9). |
| Escalation Conditions | `WORKER_SPEC.md` §3 | all | — | Subsection of Worker Contract banner |
| Output Contract | `WORKER_SPEC.md` §4 | all | autonomous_only | Stripped under `--mode supervised` |
| Autonomous Behavioral Rules | `WORKER_SPEC.md` §5 | all | autonomous_only | Stripped under `--mode supervised` |
| Prohibitions | `WORKER_SPEC.md` §6 | all | — | Subsection of Worker Contract banner |
| Action: $TYPE | state-machine output | all (autonomous) | — | Templated from ACTION value. Under `--mode supervised`, replaced with "Active Action: $TYPE". |
| Next State: $STATE | state-machine output | all (autonomous) | autonomous_only | Stripped under `--mode supervised` |
| Phase: N — Title (Regime) | `project.json.phase` + `phases.json` record | all | — | Title and regime looked up by phase id |
| Step: N — Title | `steps.json` lowest-numbered pending step for current phase | EXECUTE | — | Omitted if no pending step exists (then state machine should not have emitted EXECUTE) |
| Instructions | `instructions/$ACTION.md` | all | — | Conditional subsections stripped per markers (see §7) |
| Project Scope | `PROJECT.md` | PLAN | — | Optional (missing PROJECT.md → degrade, see §11) |
| Architecture | `ARCHITECTURE.md` | PLAN, REVIEW | — | Optional |
| Module Contract | `ARCH_<module>.md` for current phase's module | all | required for current module | If `phases.json` record for the active phase has a `module` field and `ARCH_<module>.md` exists, it's required. If no `module` field, this section is omitted entirely. |
| Project State | `project.json` (phase, state, steps_remaining, budget fields) | all | — | Always required |
| Gotchas | `project.json.gotchas` | all | — | Empty array → render the heading with `<!-- empty -->` placeholder |
| Current Phase | `phases.json` record for `project.json.phase` | all | — | The single phase record (regime, dependencies, status) |
| Current Phase Steps | `steps.json` filtered to current phase | EXECUTE, REVIEW, CLOSE | — | All step records for the current phase, in step-number order |
| Phases | `phases.json` (full array, summarized) | PLAN | — | One-line summary per phase: `id, module, regime, status` |
| Recent Activity | `devlog.jsonl` last 5 entries | EXECUTE | — | Project-wide tail, not phase-filtered |
| Phase Devlog | `devlog.jsonl` filtered to current phase | REVIEW, CLOSE | — | Full phase history |
| Prior Phase Summary | `devlog.jsonl` filtered to (current phase − 1), last 3 entries | PLAN | — | Helps plan continuity. Omitted entirely for phase 1. |
| Decisions | `decisions.json` | PLAN, REVIEW, CLOSE | — | EXECUTE omits it (Phase 3.A.2): project-wide decision history is reference, not per-step load-bearing. PLAN includes so D-IDs don't collide. |
| Tool Rules | adapter file (`CLAUDE.md` or `CODEX.md`), section "Claude-Specific Tool Rules" / "Codex-Specific Tool Rules" | all | backend-specific | Backend choice comes from runner env / supervised invocation context |
| Available Modules | adapter "Available Modules" section, fallback to `ARCHITECTURE.md` Implementation Sequence | EXECUTE, CLOSE | — | Omitted on PLAN/REVIEW since `## Architecture` already includes the Component Map (§4.3, §5) |
| Action Context | banner only | all | — | See §6 for the banner format |
| Tool Rules | banner only | all | — | See §6 for the banner format |

### 4.2 Source resolution rules

- **`phases.json` record for current phase:** the record whose `id` equals `project.json.phase`. If no such record exists, that's a required-input failure (state machine should not dispatch ACTION when phases.json doesn't list the current phase).
- **`ARCH_<module>.md`:** the module name comes from the current phase record's `module` field. The assembler looks for `<project-root>/ARCH_<module>.md`. Missing file: see §11.
- **`WORKER_SPEC.md` and `instructions/<action>.md` (framework-canonical):** resolved **project-local override → packaged default** (D-pkg-11, §5.3). The assembler first checks `<project-root>/WORKER_SPEC.md` / `<project-root>/instructions/<action>.md`; if absent, it falls back to the copy shipped in the installed package (`i2c/data/…`, via `importlib.resources`). Override is per-file. Adapters (`CLAUDE.md` / `CODEX.md`), `PROJECT.md`, `ARCHITECTURE.md`, and `ARCH_<module>.md` are **not** resolved this way — they are project-root-only.
- **`devlog.jsonl` filters:** evaluated lazily; if the file doesn't exist or is empty, the corresponding section renders as `<!-- empty -->`.

### 4.3 Available Modules fallback

The Available Modules section pulls from the adapter file's "Available Modules" placeholder, parsed as the markdown between the H2 heading and the next H2 (or EOF). If that section contains only the `<!-- List tracks ... -->` placeholder comment (i.e., no real content), the assembler falls back to grepping `ARCHITECTURE.md` for an "Implementation Sequence" table and rendering its module names. If neither yields content, the section renders as `<!-- empty -->`.

---

## 5. Assembly Matrix

The per-action inclusion table, derived from §4 for fast reference.

| Section | PLAN | EXECUTE | REVIEW | CLOSE |
|---------|:----:|:-------:|:------:|:-----:|
| Worker Contract (Identity, Main Loop, Escalation, Output Contract, Behavioral Rules, Prohibitions) | ✓ | ✓ | ✓ | ✓ |
| Action: $TYPE | ✓ | ✓ | ✓ | ✓ |
| Next State: $STATE | ✓ | ✓ | ✓ | ✓ |
| Phase: N — Title (Regime) | ✓ | ✓ | ✓ | ✓ |
| Step: N — Title | – | ✓ | – | – |
| Instructions | ✓ | ✓ | ✓ | ✓ |
| Project Scope | ✓ | – | – | – |
| Architecture | ✓ | – | ✓ | – |
| Module Contract | ✓ | ✓ | ✓ | ✓ |
| Project State | ✓ | ✓ | ✓ | ✓ |
| Gotchas | ✓ | ✓ | ✓ | ✓ |
| Current Phase | ✓ | ✓ | ✓ | ✓ |
| Current Phase Steps | – | ✓ | ✓ | ✓ |
| Phases | ✓ | – | – | – |
| Recent Activity | – | ✓ | – | – |
| Phase Devlog | – | – | ✓ | ✓ |
| Prior Phase Summary | ✓ | – | – | – |
| Decisions | ✓ | – | ✓ | ✓ |
| Tool Rules | ✓ | ✓ | ✓ | ✓ |
| Available Modules | – | ✓ | – | ✓ |

`--mode supervised` strips:
- `Output Contract` and `Autonomous Behavioral Rules` from the Worker Contract banner
- `Next State: $STATE` from the Action Context banner
- Any subsection marked `<!-- assembler:autonomous_only -->` inside `WORKER_SPEC.md` or `instructions/$ACTION.md`

`--step-budget 1` (the v1 runner default) also strips:
- Any subsection marked `<!-- assembler:multi_step_only -->` (currently `WORKER_SPEC.md`'s multi-step LOOP pseudocode, the "Loop discipline — multi-step only" subsection, and the production-incident anecdotes that motivate that discipline).

The `<!-- assembler:omit_in_prompt -->` marker strips unconditionally on every assembly — used in `instructions/*.md` for operator-facing prose (Examples, Known tooling gaps, Behavior modes) that adds no signal in the assembled prompt.

---

## 6. Prompt Structure and Ordering

The assembled prompt has a fixed order of four banner-delimited regions:

```
═══════════════════════════════════════════════
WORKER CONTRACT
═══════════════════════════════════════════════

## Identity
[…]

## Main Loop
[…]

## Escalation Conditions
[…]

## Output Contract                              ← stripped under --mode supervised
[…]

## Autonomous Behavioral Rules                  ← stripped under --mode supervised
[…]

## Prohibitions
[…]

═══════════════════════════════════════════════
TOOL RULES
═══════════════════════════════════════════════

[from adapter file's tool rules section]

## Available Modules                            ← EXECUTE, CLOSE (dedup with Architecture)
[from adapter or ARCHITECTURE.md fallback]

═══════════════════════════════════════════════
PROJECT CONTEXT
═══════════════════════════════════════════════

## Module Contract: Orchestrator
[from ARCH_orchestrator.md]

## Project State
[from project.json, JSON-pretty]

## Gotchas
[from project.json.gotchas, one bullet per entry]

## Current Phase
[the phases.json record for the current phase, as a small table]

## Current Phase Steps                          ← EXECUTE, REVIEW, CLOSE
[steps.json filtered to current phase, table form]

## Phases                                       ← PLAN only
[one-line per phase summary]

## Recent Activity                              ← EXECUTE only
[devlog.jsonl last 5 entries]

## Phase Devlog                                 ← REVIEW, CLOSE
[devlog.jsonl filtered to current phase]

## Prior Phase Summary                          ← PLAN only (omit for phase 1)
[devlog.jsonl prev phase last 3 entries]

## Project Scope                                ← PLAN only
[from PROJECT.md, verbatim]

## Architecture                                 ← PLAN, REVIEW
[from ARCHITECTURE.md, verbatim]

## Decisions
[from decisions.json, all records summarized]

═══════════════════════════════════════════════
ACTION CONTEXT
═══════════════════════════════════════════════

## Action: EXECUTE
## Next State: execute                          ← stripped under --mode supervised
## Phase: 11 — Orchestrator (Build)
## Step: 3 — Slash command routing              ← EXECUTE only
## Instructions
[from instructions/$ACTION.md, conditional subsections stripped]
```

**Banner format:** exactly 47 box-drawing characters (`═`, U+2550) per band line, the banner title in all-caps centered between two band lines, blank line after.

**Section heading style:** `## Title Case With Colons As Separators` for parameterized sections (`## Phase: 11 — Orchestrator (Build)`). Use the em-dash `—` (U+2014), not a hyphen, in parameterized headers.

**Ordering invariant:** the four regions appear in the order shown above (Worker Contract → Tool Rules → Project Context → Action Context); sections within a region appear in the order shown above; this order is fixed and not configurable. Rationale: identity framing first, environment rules early (worker knows what tools it can and can't use before reading the procedure that names those tools), reference material in the middle, action procedure last so the model's recency bias works in our favor.

**Prompt-cache split (FU-35).** The same fixed ordering doubles as the cache boundary. `--emit system` returns the first two regions (Worker Contract + Tool Rules) — content that is byte-identical across consecutive same-phase, same-action iterations, so it can ride in Claude Code's prompt-cached system prompt. `--emit user` returns the rest (Project Context + Action Context + the Output Contract reminder) — everything that changes per phase / step / iteration, kept out of the cached prefix. The split preserves the ordering invariant exactly: `--emit full` is byte-identical to `system.rstrip() + "\n\n" + user`. The Output Contract reminder stays at the absolute tail of the `user` part, so its recency anchoring is unaffected.

**Trailing newline:** the file ends with `\n`.

---

## 7. Conditional Section Mechanism

Some sections inside `instructions/$ACTION.md` or `WORKER_SPEC.md` apply only under specific conditions. The assembler strips inapplicable sections deterministically.

### 7.1 Marker syntax

After a heading line, on its own line, place an HTML comment of the form:

```
<!-- assembler:KEY=VALUE -->
```

or, for boolean conditions:

```
<!-- assembler:KEY -->
```

The marker applies to the section that begins at the preceding heading and ends at the next heading of the same or shallower level (or EOF).

### 7.2 Supported keys

| Key | Evaluation | Strips when | Where applied |
|-----|------------|-------------|---------------|
| `requires=dependencies_nonempty` | True iff `phases.json[id=$PHASE].dependencies` has length > 0 | condition is false | `plan.md` Pre-plan Dependency Probe, `close.md` Pre-close Integration Check |
| `autonomous_only` | True iff `--mode` is `autonomous` (the default) | condition is false (i.e., `--mode supervised`) | `WORKER_SPEC.md` Output Contract & Autonomous Behavioral Rules, any autonomous-only paragraphs in instruction files |
| `supervised_only` | True iff `--mode supervised` | condition is false (i.e., autonomous mode) | Reserved for future use; no current consumers |
| `multi_step_only` | True iff `--step-budget > 1` (default is `1`) | condition is false (the single-step common case) | `WORKER_SPEC.md` Multi-step LOOP, Loop discipline subsection, and production-incident anecdotes. Forward-compatible with the multi-iteration loop (Phase 3.B/C). |
| `omit_in_prompt` | Always false | always | Operator-facing sections in `instructions/*.md` (Examples, Known tooling gaps, Behavior modes) that read well in the source file but add no signal in the assembled prompt. |

### 7.3 Evaluation order

1. Apply per-action inclusion (§5) to determine candidate sections.
2. For each candidate, evaluate any `<!-- assembler:... -->` markers.
3. Strip sections whose conditions evaluate to false. A section with no marker is always included if it passes step 1.
4. Conditional stripping happens before banner assembly so the output is exactly what the worker sees.

### 7.4 Extensibility

New conditions are added by:
1. Tagging the relevant heading in the markdown file with the new marker key.
2. Adding a small evaluator function in the assembler that maps the key to a predicate over `.state/` and CLI args.

No registry file, no DSL — just a Python function per key. Keeps the surface tight and inspectable.

### 7.5 Example

```markdown
## Pre-plan: Dependency Probe — non-leaf modules only
<!-- assembler:requires=dependencies_nonempty -->

Procedure:
1. Inventory external dependencies for the current module's `dependencies` list.
2. …
```

When the assembler builds the PLAN prompt for a phase whose `dependencies` array is empty, the entire "Pre-plan: Dependency Probe" section is stripped. The worker receives the remaining `plan.md` content with no awareness that the conditional section ever existed.

---

## 8. Operator views — moved to `i2c.control` (removed Phase 3a)

The assembler previously rendered three operator-facing prose sections —
`--section status`, `--section phase-summary`, and `--section devlog`. They were
**removed in Phase 3a (FU-39, D-arch-13)** because they duplicated the
projections already in `i2c.control` — the prose-vs-structure dual maintenance
i2c exists to eliminate (D-pkg-7/14). Operator and surface views are now
structured dataclasses from `control`, formatted at the surface:

| Removed assembler section | Replacement |
|---------------------------|-------------|
| `--section status` | `control.status()` → `i2c status` (text) / `i2c status --json` |
| `--section phase-summary --phase N` | `control.phase_summary(phase=N)` → `i2c phase-summary --phase N [--json]` |
| `--section devlog --phase N` | `control.devlog(phase=N)` → `i2c devlog --phase N [--json]` |

The assembler now exposes only worker-prompt assembly (`--action`) and the two
verbatim file-passthrough sections (`--section architecture`, `--section
module`). Per D-pkg-15 the byte-locked worker-prompt path stays isolated from
`control`; the shared leaf renderers (Current Phase Steps, Gotchas, Recent
Activity — §4.1) remain in the assembler for the prompt's use only, and the
worker prompt is byte-identical across this change (golden snapshots in
`tests/test_prompt_golden.py`).

---

## 9. Mode Framing

`--mode {autonomous,supervised}` controls which subsections of the Worker Contract and the instructions are included.

### 9.1 Autonomous (default)

- All sections in §5 included per the matrix.
- `Output Contract`, `Autonomous Behavioral Rules`, and `Next State` are present.
- Any subsection tagged `<!-- assembler:autonomous_only -->` is included.
- Any subsection tagged `<!-- assembler:supervised_only -->` is stripped.

### 9.2 Supervised

- All sections in §5 included per the matrix, **except** those tagged `autonomous_only` (stripped).
- `Output Contract`, `Autonomous Behavioral Rules`, `Next State: $STATE` are stripped.
- `Action: $TYPE` becomes `Active Action: $TYPE` (no exit-signal framing).
- Subsections tagged `<!-- assembler:supervised_only -->` are included (none today; reserved).

The assembler does **not** add any prose beyond the strip-or-include mechanism. The instruction files themselves carry both autonomous and supervised paragraphs, distinguished by markers. Adding a new supervised-mode-specific instruction means adding a tagged subsection to `instructions/$ACTION.md`, not editing assembler code.

### 9.3 Mode framing is set by the caller, not the worker

The runner (autonomous mode) passes `--mode autonomous` explicitly. Devmate slash-command wrappers (supervised mode) pass `--mode supervised`. Human direct CLI use chooses one. The worker reads the assembled prompt and follows whatever framing arrived; it does not choose.

---

## 10. Multi-Step Mid-Step Usage

In multi-step mode (`STEP_BUDGET > 1`), the worker loops the state machine itself between steps (per `WORKER_SPEC.md` §2). Between steps, the worker may call the assembler for fresh single-section context. The following `--section` invocations are callable mid-step:

| Section | Use case |
|---------|----------|
| `--section architecture` | Need the full ARCHITECTURE.md to reason about cross-module wiring during a refactor step |
| `--section module --module $NAME` | Need a different module's contract than the active phase's module (e.g., reviewing how a consumer uses this module's API) |

`--action` is **not** callable mid-step. Re-assembling the full prompt mid-step would duplicate context already in the worker's window and burn tokens. The runner is the only caller authorized to issue full-prompt assemblies.

Mid-step assembler calls do not decrement the step budget (`state.py` budget decrement is the state machine's job, not the assembler's).

Note that the `multi_step_only` marker mechanism used to strip `WORKER_SPEC.md`'s multi-step LOOP and discipline subsections in single-step mode (Phase 3.A.1) is the same `--step-budget`-driven evaluator described in §7.2. When the multi-iteration loop ships and the runner starts passing `--step-budget > 1`, those subsections automatically reappear in the assembled prompt.

---

## 11. Error and Edge Cases

### 11.1 Required-input failures (exit 1, abort)

| Failure | Source |
|---------|--------|
| `project.json` missing | always required |
| `project.json` schema-invalid | always required |
| `phases.json` missing or schema-invalid | always required (state machine needs it too) |
| `steps.json` missing or schema-invalid | always required |
| No record in `phases.json` with `id == project.json.phase` | required for every action EXCEPT `--action plan` (which creates the record per `instructions/plan.md` step 4 — see archive/DESIGN_state_lifecycle_v1.md §6.4); always required for `--section` requests |
| `instructions/$ACTION.md` missing from **both** project-root and the installed package | required for `--action` (resolved override→packaged, §4.2; failure only if absent from both) |
| `WORKER_SPEC.md` missing from **both** project-root and the installed package | required (resolved override→packaged, §4.2) |
| Adapter file (`CLAUDE.md` or `CODEX.md`) missing | required (backend chosen by runner / caller; assembler reads whichever is named) |
| `ARCH_<module>.md` missing when `phases.json[current].module` is set | required for current module |
| Schema validation failure on any `.state/` file the assembler is reading for this invocation | abort; failure path includes the file path and `jsonschema` error message |

On any required-input failure, the assembler writes a structured error to stderr and exits 1. Format:

```
ERROR: <kind>
File: <absolute path>
Detail: <jsonschema error message or file-system error>
```

Nothing is written to stdout on exit 1.

### 11.2 Optional-input degradations (exit 0, marker in output)

| Optional case | What the section renders |
|---------------|--------------------------|
| `PROJECT.md` missing | `<!-- not present: PROJECT.md not found -->` under the `## Project Scope` heading |
| `ARCHITECTURE.md` missing | `<!-- not present: ARCHITECTURE.md not found -->` under `## Architecture` |
| `decisions.json` empty array | `<!-- empty -->` under `## Decisions` |
| `devlog.jsonl` empty or missing | `<!-- empty -->` under `## Recent Activity` / `## Phase Devlog` / `## Prior Phase Summary` |
| `project.json.gotchas` empty | `<!-- empty -->` under `## Gotchas` |
| Adapter Available Modules section empty and ARCHITECTURE fallback also empty | `<!-- empty -->` under `## Available Modules` |
| Current phase is phase 1 (no prior phase) | `## Prior Phase Summary` heading omitted entirely (not even an empty marker — phase 1 has nothing to summarize) |
| Current phase record has no `module` field | `## Module Contract` heading omitted entirely |
| `--action plan` AND no phases.json record for current phase | `## Phase: N — (record to be created by PLAN)` heading; `## Current Phase` body renders a placeholder comment pointing to `instructions/plan.md` step 4; dep-probe conditional section strips (`dependencies_nonempty` evaluator returns False). See archive/DESIGN_state_lifecycle_v1.md §6.4. |

Optional degradations all exit 0. The worker sees the placeholders and decides whether the absence matters. None of these block assembly.

### 11.3 CLI argument errors (exit 2)

| Failure | Behavior |
|---------|----------|
| Both `--action` and `--section` specified | exit 2 with "specify exactly one of --action or --section" |
| Neither `--action` nor `--section` | exit 2 |
| `--action` with unknown value | exit 2 (argparse `choices` enforces) |
| `--section` with unknown value | exit 2 (argparse `choices` enforces) |
| `--phase` missing when `--action` is set | exit 2 |
| `--module` missing when `--section module` | exit 2 |
| `--mode` specified with `--section` | exit 2 |
| `--phase` specified with `--section {architecture,module}` | exit 2 (these sections don't consume `--phase`; FU-17) |
| non-default `--emit` specified with `--section` | exit 2 |
| `--phase` is not a positive integer | exit 2 |

### 11.4 Schema validation policy

Every `.state/*.json` file read is validated against the registered schema via `tools/validate.py` (`SCHEMA_BY_FILENAME`, `validate_state_file`). `devlog.jsonl` is validated line-by-line via `validate_devlog_jsonl`. Validation failure on a required file → exit 1 per §11.1. Validation failure on `devlog.jsonl` is treated as required (the file format is small and explicit; corrupt devlogs are bugs).

---

## 12. Output Encoding and Invariants

- **Encoding:** UTF-8. Box-drawing characters (`═` U+2550, `—` U+2014) are emitted as their UTF-8 byte sequences.
- **Line endings:** LF (`\n`) only. The assembler does not respect platform line-ending conventions; downstream consumers handle conversion if needed.
- **Trailing newline:** the file ends with a single `\n`.
- **Section ordering:** fixed per §6. Reordering is a contract break.
- **Section content reproducibility:** the same `.state/` + `instructions/` + same CLI args produces byte-identical output. No timestamps, no randomness, no environment-dependent content (file paths in `<!-- not present -->` markers use repo-relative paths).
- **Source markdown verbatim:** content lifted from `WORKER_SPEC.md`, `PROJECT.md`, `ARCHITECTURE.md`, `ARCH_<module>.md`, and `instructions/$ACTION.md` is included verbatim except for conditional-section stripping. The assembler does not edit prose, normalize whitespace, or re-flow paragraphs.
- **State-file rendering:** `project.json` is rendered as pretty-printed JSON inside a fenced ```json``` block. `phases.json`, `steps.json`, `decisions.json` are rendered as markdown tables (one row per record, columns in schema-declaration order). `devlog.jsonl` filters render as bulleted summaries: `phase.step action → outcome (commit if present) — summary`.

---

## 13. Implementation Notes

- **Language:** Python 3.10+ (matches existing tools).
- **Dependencies:** stdlib only, plus `jsonschema` (already required by `validate.py`).
- **Code reuse:** `tools/validate.py` provides `SCHEMA_BY_FILENAME`, `validate_state_file`, `validate_devlog_jsonl`, `load_schema`, `validate_json_schema`. The assembler reuses these directly — no duplicate validation logic.
- **Module structure:** single file `tools/assemble_context.py` for v1. Internal organization: argparse setup, per-action recipes, per-section renderers, conditional-marker extractor, output writer. If sections proliferate, split per-section renderers into a sub-package.
- **No file caching:** every invocation re-reads every source file. Files are small (largest is `devlog.jsonl`, kilobytes); caching adds invalidation complexity without measurable benefit. (Distinct from the *LLM prompt cache*, which the `--emit system/user` split enables downstream — see §6 and D-arch-12.)
- **No streaming:** synchronous full-buffer write. Prompts are tens of KB at most.
- **Testing:** unit tests against the example fixture in `examples/initial_state/.state/`. Each per-section renderer testable in isolation; full-prompt assembly testable for each (action, mode) pair against golden outputs.
- **Determinism:** no `dict`-order dependency (use the schema's declared field order for table columns); no environment-dependent content; no walltime in output.

---

## 14. Decisions

Locked decisions, captured inline so the contract's rationale travels with it.

| # | Decision | Rationale |
|---|----------|-----------|
| **D-arch-1** | Fail-fast for required inputs, degrade for optional ones | Required-input failure (e.g., schema-invalid `project.json`) would mislead the worker. Optional-input degradation (empty `decisions.json`, leaf module with no probe) is normal early-project state. Two error paths are worth the complexity. |
| **D-arch-2** | `--section module --module NAME` (flag form) | Consistent with `--section devlog --phase N`. Avoids mixed positional/flag arg shapes across sections. |
| **D-arch-3** | Heading metadata markers for conditional sections | Single mechanism handles dep-probe, integration check, supervised-mode stripping. Extensible by tagging + adding an evaluator. Markers are HTML comments — invisible in rendered markdown. |
| **D-arch-4** | Title Case section names throughout | Matches the DESIGN sketch, the most-quoted reference. Standardizing eliminates the drift surfaced during cross-reference. |
| **D-arch-5** | `Decisions` section included for all four actions | PLAN authors D-IDs and would collide without seeing existing decisions. Size cost is negligible. |
| **D-arch-6** | `Current Phase` and `Current Phase Steps` are two distinct sections | Different sources (`phases.json` record vs `steps.json` filter) and different consumers. Conflating them was a documentation oversight. |
| **D-arch-7** | Mid-step `--section X` is documented and bounded | Per D16. Bounded to two section types (§10) so it doesn't sprawl into a runtime tool. `--action` is not callable mid-step. (Originally four; `status`/`devlog` removed in Phase 3a — see §8.) |
| **D-arch-8** | Available Modules: adapter primary, ARCHITECTURE.md fallback | Adapter has the `Available Modules` placeholder explicitly for this purpose. Architecture is a sensible fallback when the placeholder is unfilled. |
| **D-arch-9** | This contract is authoritative; archive/DESIGN_governance_v3.md gets a forward-pointer | Avoids rewriting the design doc while making the authority explicit. |
| **D-arch-10** | Output is markdown / UTF-8 / LF / trailing newline | No source specified; pick the existing toolchain convention. |
| **D-arch-11** | `--mode {autonomous,supervised}`, autonomous default | Default-only would surprise readers when they grep for `--mode autonomous` and find no examples; explicit values keep the surface obvious. |
| **D-arch-12** | `--emit {full,system,user}` exposes the cache split in the assembler, not the runner (FU-35) | The stable/volatile boundary is a property of the prompt's region structure (§6), which the assembler owns. Exposing it as a flag keeps the runner a thin caller and makes the split independently testable (`full == system.rstrip() + "\n\n" + user`). The runner can't inject Anthropic `cache_control` markers anyway — it pipes plaintext to the `claude -p` / `codex exec` CLIs — so the reachable lever is routing the stable prefix through Claude Code's `--append-system-prompt-file`. |
| **D-arch-13** | Operator-facing `--section` views (`status`, `phase-summary`, `devlog`) removed; the assembler is worker-prompt assembly + verbatim file passthrough (`architecture`, `module`) only (Phase 3a / FU-39) | They duplicated `i2c.control`'s projections — the prose-vs-structure dual maintenance i2c exists to kill (D-pkg-7/14). Views now come from `control` formatted at the `i2c` CLI; the byte-locked prompt path stays isolated from `control` (D-pkg-15), proven by golden snapshots in `tests/test_prompt_golden.py`. |

---

## 15. Change History

| Date | What changed | Why |
|------|--------------|-----|
| 2026-06-04 | Initial contract spec | Phase 1 build-order step 7.5 — author the contract before Phase 1.3 implementation and step 8 slash wrappers. Resolves section-name drift and undocumented edge cases surfaced by a cross-reference of all framework files. |
| 2026-06-21 | Added `--emit {full,system,user}` (FU-35) | Prompt-cache support: expose the region structure's stable/volatile boundary so the runner can route the cache-stable prefix through Claude Code's system prompt. `full` output unchanged. See §3.2, §6, D-arch-12. |
| 2026-06-25 | Removed `--section {status,phase-summary,devlog}` (FU-39 / Phase 3a) | De-duplicate operator views against `i2c.control`; views now come from the `i2c` CLI. Worker prompts byte-identical (golden test). See §8, D-arch-13. |

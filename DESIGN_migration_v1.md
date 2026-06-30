# e2e → i2c Migration & the `import` Converter — v1

> Design memo. Scopes a tool (`i2c import`) that migrates existing
> e2e-lineage projects onto installed i2c. Motivated by a fleet of
> consumers built on the framework's ancestor ("e2e"), most done but some
> still in development. The operator wants a repeatable converter rather
> than N hand-ports — **if** the per-project drift is small enough to
> automate.
>
> Status: **audit in progress.** This memo records the e2e prose-state
> starting point and the converter design that follows from it; the
> per-project audit table (§4) is being filled in as real implementations
> are inspected. Three consumers audited (toolkit, diplomat, phosphene);
> **toolkit** is migrated onto the package.
>
> Authors: operator + assistant, 2026-06-26.

---

## 1. Problem

The operator runs several projects descended from the same governance
lineage. They want to move them onto installed i2c (`pip install` + `.state/`
+ the `i2c` console tool surface, per `DESIGN_packaging_v1.md`). A one-off
hand-port is feasible per project but doesn't scale across the fleet, and
the projects **differ** — they were set up at different times, off different
framework versions, by the copy-and-sync model the packaging memo (§1)
describes as silently rotting.

The deliverable is therefore two-part:
1. **An audit** of what is *exactly the same* vs *different* across the
   fleet — the variance surface that any converter must absorb.
2. **A converter** (`i2c import`) whose scope is set by that audit.

The central question is the same one `DESIGN_packaging_v1.md` raises as
Q-pkg-5 (transition plan for existing internal consumers), generalized from
one project to the fleet.

## 2. The migration starting point — e2e (prose state)

Every project in scope shares one starting form: **stock e2e with prose
state.** State lives in `DEVPLAN.md` YAML frontmatter + markdown `- [ ]`
checkboxes; the state machine is `tools/state_machine.sh` (bash/grep/sed
mutating the frontmatter); the worker **reads governance files directly**;
history is prose (`DEVLOG.md`, `DECISIONS.md`); `GOVERNANCE.md` /
`WORKER_SPEC.md` are symlinked or copied from an `e2e/` checkout. Migrating
means **serializing prose state into JSON `.state/`** *and* repackaging.
→ **toolkit, diplomat, phosphene.**

**The framework name is not a reliable signal** — diplomat's adapter already
calls its framework "From Idea to Code" while running the e2e prose
machinery; toolkit's calls it "e2e." Detect by *machinery*, not by label.

### Detection signals — is this an e2e prose-state project? (cheap, file-existence level)

| Signal | e2e prose-state |
|---|---|
| `DEVPLAN.md` with `- [ ]` steps | ✓ |
| `tools/state_machine.sh` (bash) | ✓ |
| State machine | bash, mutates frontmatter |
| Worker reads governance files | yes (tiered @-reads) |
| `.state/*.json` already present | ✗ (already on i2c — nothing to import) |
| Exit signal | 5-line (toolkit) / TBD |

## 3. What's invariant vs what varies

### Invariant — true for every project (the converter can hard-code these)

Because every project converges on the *same* i2c end state, these never
change and need no per-project logic:

1. **Conceptual model** — 4 actions (PLAN/EXECUTE/REVIEW/CLOSE), 7 lifecycle
   states, 3 regimes (Build/Refine/Explore). e2e and i2c are semantically
   identical here; i2c is the direct descendant.
2. **Narrative docs pass through untouched** — `PROJECT.md`,
   `ARCHITECTURE.md`, `ARCH_*.md`, `API.md`. Same filenames, same role.
3. **Target schema** — the five `.state/` files + `schema_version` stamp.
4. **The end tool surface** — `i2c` console command; in-repo framework
   (tools/schemas/instructions/WORKER_SPEC/GOVERNANCE/runner) is deleted and
   supplied by the package.

### Variant — must be audited per project

| # | Axis | e2e baseline | How it drifts | Converter impact |
|---|---|---|---|---|
| 1 | **Layer** | 0 (supervised) or 0+1 (autonomous) | Layer-0-only lacks `state`/runner/worker | **Detect** → skip Layer-1 transforms |
| 2 | **Framework version** | latest e2e | copied/symlinked trees drift across projects set up at different times | **Detect** → version-aware parse / `i2c migrate` |
| 3 | **Frontmatter** | `phase/blocked/state/steps_remaining` | `blocked` ∈ {null,false,true,`awaiting-human-audit`}; `state` absent on L0 | **Normalize** → project.json |
| 4 | **Phase ID type** | int (toolkit, diplomat) | alphanumeric — GOVERNANCE `3b`; **phosphene `MVP.4d`** | **Blocker** — i2c schema requires *integer* `phase`/`id` (confirmed by prototype); converter refuses non-integer phase. Needs a schema decision or renumbering (Q-mig-7) |
| 5 | **Step representation** | `- [ ]` under `## Phase N` | separate plan files (toolkit `CLANKMATES_CLIENT_PLAN.md`); heading variants | **Parse/manual** → highest extraction risk |
| 6 | **Sub-tracks** | one DEVPLAN/DEVLOG | `DEVPLAN_<TOPIC>.md` / `DEVLOG_<TOPIC>.md` pairs | **Detect** → merge or per-track |
| 7 | **DEVLOG format/order** | `### Step N` + Mode/Outcome, newest-last | toolkit uses `## date —`, newest-**first** (admitted drift, CLAUDE.md:51) | **Lenient parse** or snapshot-don't-port |
| 8 | **Regime → budget** | Build=steps; Refine=time; Explore open | single-pass projects commonly end in a Refine (time-budget) phase | **Auto** → `budget_type` follows the current phase's regime (refine→time, build→steps; else omitted — i2c infers) |
| 9 | **Exit-signal format** | 5-line (toolkit) | stale variants | **N/A to data** — only the deleted runner |
| 10 | **Adapters** | CLAUDE/CODEX tiered-read | project-specific module tables/rules | **Manual** → re-scaffold + paste project sections |

## 4. Per-project audit (in progress)

Verified by inspection 2026-06-26. Cells marked *TBD* await confirmation
against the pointed implementations.

| Project | Layer | Phase / State | Scale | Status | Notes |
|---|---|---|---|---|---|
| **toolkit** | 1 | 4 / plan, unblocked | 9 modules, all complete | Done | 5-line exit; DEVLOG newest-first (drift); GOVERNANCE symlink `../e2e/`; names fw "e2e". `CLANKMATES_CLIENT_PLAN.md` sub-plan holds some steps. |
| **diplomat** | 1 | 48 / close, blocked | ~17 ARCH modules; heavy research tooling | In dev — at a phase boundary | Names fw "From Idea to Code" but runs e2e machinery. `DEVLOG_archive.md` → large history. Exit-signal format *TBD*. Biggest data-conversion job. |
| **phosphene** | 1 | `MVP.4d` / execute, blocked | 9 ARCH modules; depends on toolkit | In dev — MVP | **Alphanumeric phase ID** (`MVP.4d`) — confirms axis 4. GOVERNANCE.md + DEVLOG+archive; many `DESIGN_*.md`. Same machinery as toolkit/diplomat. |
| *(others)* | | | | | codexbot + any further consumers the operator points to. |

> **Historical footnote — clankercourts.** The single predecessor copy-model
> project; hand-migrated onto the package 2026-06-29 — see git history. Not a
> converter target (N=1, no successor).

**Reading so far:** the in-scope fleet is uniformly prose-e2e (toolkit,
diplomat, phosphene). diplomat is the worst case (48 phases, archived
history); phosphene is the fidelity bar — still in development, with an
alphanumeric phase id (axis 4), so its live `.state/` must convert
losslessly.

## 5. Converter design

`i2c import` is a single-purpose, **two-stage** tool — transform → report —
for e2e prose-state projects. Its one precondition: the root must be an e2e
prose-state project (a `DEVPLAN.md`); it refuses anything else (nothing to
import) and won't clobber an existing `.state/`.

```
i2c import [PATH]            # e2e (prose state) only
  ├─ (precondition: DEVPLAN.md present, else refuse)
  ├─ transform → serialize prose state → .state/ JSON  + de-vendor + adapters
  └─ report()  → manual-review list + `i2c migrate --check` + verify
```

### 5.1 Prose e2e → i2c (`toolkit`, `diplomat`)

Deterministic, parameterized by the detected profile:

- **Auto:** frontmatter → `project.json` (normalize axis 3); "Current
  Status" table + phase sections → `phases.json` (regime, status, axis 4/8);
  `DECISIONS.md` D-# entries → `decisions.json`.
- **Parse / profile-dependent:** `- [ ]` checkboxes → `steps.json`,
  including separate plan files (axis 5) and sub-tracks (axis 6). For
  *closed* phases, collapse to summaries — full per-step fidelity is not
  required.
- **Skippable history:** `DEVLOG.md`(+archive) → `devlog.jsonl`. For done
  projects, **snapshot the prose as an archive doc and start the JSONL
  fresh** rather than parsing newest-first/format-drifted prose (axis 7).
  Port history only where an in-dev project needs it.
- **Manual (Stage 1 cut-over, §5.3):** adapter rewrite — re-scaffold the i2c
  `CLAUDE/CODEX` and paste the project-specific *Available Modules* +
  *Project-Specific Notes* verbatim (axis 10).
- **Delete (at finalize, §5.3):** `tools/state_machine.sh`, `run-iteration.sh`,
  `tools/*.py` log parsers, `WORKER_SPEC.md`, `GOVERNANCE.md`,
  `.claude/commands`, the `e2e/` symlinks.

### 5.1.1 Prototype status — toolkit (shipped 2026-06-26)

The converter is implemented and proven on toolkit:

- **Code:** `i2c/import_e2e.py` (transform → report) + `i2c import`
  CLI subcommand (dry-run default; `--apply`, `--force`, `--port-history`,
  `--json`); `tests/test_import_e2e.py` (10 tests). Full suite green.
- **Run on toolkit:** emitted a schema-valid `.state/` — `project.json`
  (phase 4, state plan, 3 gotchas, `budget_type=steps`, `schema_version=1`,
  `blocked`/`steps_remaining` dropped), `decisions.json` (9 records, D-1..D-9,
  status/priority mapped), `phases.json` (loop-era ids 1, 2, 4), empty
  `steps.json` / `devlog.jsonl`. Verified by `i2c status` + `i2c migrate
  --check` (“up to date: schema v1”). **Non-destructive** — git showed only
  `?? .state/`; refusal on a non-e2e project (no DEVPLAN.md) confirmed
  (exit 2, no write).
- **Findings confirmed:**
  - **Collided numbering handled by report-not-guess.** toolkit's duplicate
    `## Phase 3` (Structured LLM vs RAPTOR) was *not* guessed — id 3 was
    excluded from `phases.json` and surfaced in `manual_review`. The pre-loop
    modules (Embedding/Clustering/…, never phase-numbered) were likewise
    reported, not invented. This validates the snapshot-don't-port + report
    architecture over an over-ambitious parser.
  - **Integer-id schema constraint is a real fleet blocker.** project/phases/
    steps schemas require integer ids; the converter refuses a non-integer
    `phase` (e.g. phosphene `MVP.4d`) with a clear error rather than emit
    invalid state. Resolve before phosphene (Q-mig-7).
  - **toolkit is the pessimistic outlier.** It was built piecemeal (modules
    added across unrelated work) — which is precisely what produced the
    collided/mixed phase numbering. Most projects are single-pass (master-plan
    → prototype) with a polish/refine tail, so typical conversions have (a)
    clean sequential numbering (the collision path rarely fires) and (b) a
    **Refine** current phase more often than toolkit suggested. The converter
    derives `budget_type` from the current phase's regime (refine→time,
    build→steps; else omit and let i2c infer) rather than assuming steps.

### 5.2 End-state validator

Every migration finishes with the same checks so "migrated" means one thing:
`i2c status` reads the new `.state/`; `i2c migrate --check` is clean;
`i2c assemble --action <state> --mode supervised` produces a sane prompt;
the project's own tests still pass.

### 5.3 Cut-over: from `.state/` to the i2c runtime (manual, staged)

`i2c import` does the **state-serialization half** — it produces `.state/` but
leaves the project still running the e2e loop. The **runtime half** (making the
project actually run on installed i2c) is separate and, for this fleet,
**manual** — one operator, a handful of projects; automating it isn't worth it.

**Why a project can't run both loops.** After import a project holds two
*unsynchronized* representations: the e2e loop reads/writes `DEVPLAN.md`
frontmatter + checkboxes; `.state/` is a frozen snapshot. Run the e2e loop and
`.state/` goes stale; run i2c and `DEVPLAN.md` goes stale. **Pick one runtime
per project from cut-over onward.**

**Why the runtime switch must precede any i2c trial** (override resolution,
packaging memo §5.3 — project-local files win over packaged defaults):
- Adapters (`CLAUDE.md`/`CODEX.md`) are read from the project root, always. The
  e2e adapters tell the worker to read governance files and call
  `bash tools/state_machine.sh` — wrong for an i2c worker. **Must rewrite.**
- A project-local `WORKER_SPEC.md` **shadows** the package's correct one; the
  e2e copy is wrong for i2c. **Must remove/rename.**
- The bash `run-iteration.sh` / `tools/state_machine.sh` / `tools/*.py` do
  **not** interfere with `i2c run` (merely unused) → keep as a fallback during
  the trial; delete at finalize.

**Staged plan (toolkit):**

- **Stage 0 — serialize (done):** `.state/` generated by `i2c import --apply`.
- **Stage 1 — cut-over (minimal; precedes trial):**
  1. Rewrite `CLAUDE.md` + `CODEX.md` to i2c form (source: packaged
     `i2c/data/adapters/{claude,codex}.md`; clankercourts' `CLAUDE.md` is a
     filled example). Framework = "i2c"; paste toolkit's current *Available
     Modules* + *Project-Specific Notes*; drop the e2e "Required Reading"
     tiers and the bash/WORKER_SPEC/.claude references; 2-line `EXIT: 0|2`
     output contract. (Gotchas now arrive via `project.json.gotchas` — don't
     duplicate them in the adapter.)
  2. Neutralize shadowing markdown: rename `WORKER_SPEC.md` →
     `WORKER_SPEC.e2e.md` (the assembler stops resolving it) and remove the
     `.claude/commands` e2e symlink. (toolkit has no local `instructions/`, so
     the packaged procedures apply automatically.)
  3. Add `i2c.toml` (source: packaged template): `[run]` backend/model/budget;
     `[telegram]` admin allowlist + root for the bot.
  4. `pip install -e p:\shared\i2c` (and `i2c[telegram]`) so `i2c` is on PATH
     and toolkit resolves the package from its own directory.
  5. Add a one-line "on i2c now — do NOT run run-iteration.sh" note atop
     `DEVPLAN.md` to prevent a habitual double-loop.
  - **Acceptance:** from `p:\shared\toolkit`, `i2c status` reads `.state/`;
    `i2c assemble --action plan --phase 5 --mode supervised` emits a prompt
    with **no** DEVPLAN/bash/e2e references, using the i2c adapter + packaged
    WORKER_SPEC + packaged `instructions/plan.md`.
  - *Optional:* patch `phases.json` (resolve the id-3 collision; add pre-loop
    module records). Not required — toolkit is at phase 4/plan; the next action
    is PLAN phase 5, which doesn't depend on historical records.
- **Stage 2 — trial:** `i2c serve telegram`; run the queued phase(s) through
  the i2c loop only. Rollback if needed (`rm -r .state`, `git checkout`
  adapters; the e2e files are still present).
- **Stage 3 — finalize (after it works):** delete `run-iteration.sh`,
  `tools/state_machine.sh`, `tools/parse_*.py` + `digest_logs.py`,
  `GOVERNANCE.md`, `WORKER_SPEC.e2e.md`, any `.claude` leftovers; optionally
  archive/rename the snapshot `DEVPLAN.md`/`DEVLOG.md`/`DECISIONS.md`; commit.

## 6. Effort & automation verdict

- **Done projects (toolkit)** are moderate but mostly *skippable* history:
  the non-negotiable core (project.json + phases.json + adapters +
  delete-set) is small; steps/devlog fidelity is optional.
- **diplomat** is the real test of the parser: 48 phases and archived
  history. If the converter handles diplomat's step/devlog extraction
  acceptably (or we accept snapshot-don't-port for its closed phases), it
  handles anything in the fleet.

Automating the e2e prose-state migration looks worthwhile: it has multiple
instances (toolkit, diplomat, phosphene, + any of the operator's other
prose-e2e projects), the invariant core is large, and the form is regular.
The risk concentrates in axis 5/6/7 (step + history extraction) — exactly
where snapshot-don't-port de-risks done projects.

## 7. Open questions

- **Q-mig-2:** for done projects, default to **snapshot-don't-port**
  DEVLOG history (archive the prose, fresh JSONL) vs best-effort parse?
  Leaning snapshot-by-default, `--port-history` opt-in.
- **Q-mig-3:** how much step fidelity do in-dev projects need —
  current phase only, or all open phases? (Closed phases collapse to
  summaries regardless.)
- **Q-mig-5:** *(updated)* phosphene confirmed (e2e prose-state, in-dev).
  Still open: codexbot + any further consumers, and whether any differs in
  layer (e.g. Layer-0-only). All audited so far are Layer 1.
- **Q-mig-6:** *(resolved)* migration tooling (`i2c import` + the manual
  cut-over, §5.3) is **fleet-internal and one-time** — the operator is the sole
  e2e user; any public i2c adoption is greenfield. It is **excluded from the
  public package surface at release** (kept in-repo for fleet work; relocate it
  out of the shipped CLI as a pre-release cleanup). The cut-over is **not**
  automated into the tool — not worth it for one operator.
- **Q-mig-7:** *(blocker)* alphanumeric phase ids. i2c's schema requires
  integer `phase`/`id`, but e2e prose projects allow `3b` / `MVP.4d`
  (phosphene). Two options: (a) relax the schemas to accept string ids
  (ripples into state.py/state_machine/control), or (b) a renumbering
  transform in the converter (map `MVP.4d`… → a clean integer sequence,
  recorded in the import report). Must be decided before converting phosphene.

## 8. Next steps

1. Operator points to the remaining implementations (codexbot, any others).
2. Fill in the §4 audit cells marked *TBD* — diplomat's exit-signal/step-source
   details, and any further consumers.
3. Confirm whether any further consumer differs in layer (Q-mig-5).
4. *(done 2026-06-26)* converter prototyped and proven on toolkit
   (§5.1.1).
5. Next: run it against **diplomat** (the 48-phase stress test for the phase/
   collision parser), and decide **Q-mig-7** (alphanumeric phases) before
   **phosphene**.
6. *(done)* toolkit taken fully onto the package via the staged cut-over (§5.3) — Stage 1 cut-over → Telegram bot → queued phase(s) → finalize. toolkit now runs on installed i2c, driven by the i2c bot.

## 9. Decisions (ratified)

| # | Decision | Status |
|---|---|---|
| D-mig-2 | `i2c import` requires an e2e prose-state project (a `DEVPLAN.md`); it refuses anything else (nothing to import) and won't clobber an existing `.state/`. Single path, no variant matrix. | ratified |
| D-mig-3 | Done-project history is snapshot-not-ported by default (`--port-history` to opt in). | ratified |
| D-mig-5 | Narrative docs (PROJECT/ARCHITECTURE/ARCH_*/API) are pass-through; the converter never rewrites them. | ratified |
| D-mig-6 | Migration is staged: serialize (`i2c import`) → cut-over (manual, §5.3) → trial → finalize. The runtime switch (adapters + neutralize shadowing markdown + i2c.toml + install) precedes any i2c trial; a project never runs both loops at once. | ratified |
| D-mig-7 | Migration tooling is fleet-internal and one-time: excluded from the public i2c surface at release (public adoption is greenfield-only); the cut-over stays manual rather than a tool feature. | ratified |

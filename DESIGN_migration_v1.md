# e2e → i2c Migration & the `import` Converter — v1

> Design memo. Scopes a tool (`i2c import`) that migrates existing
> e2e-lineage projects onto installed i2c. Motivated by a fleet of
> consumers built on the framework's ancestor ("e2e") or on a copy-model
> i2c, most done but some still in development. The operator wants a
> repeatable converter rather than N hand-ports — **if** the per-project
> drift is small enough to automate.
>
> Status: **audit in progress.** This memo records the dialect taxonomy and
> the converter design that follows from it; the per-project audit table
> (§4) is being filled in as real implementations are inspected. Three
Four consumers audited (toolkit, diplomat, phosphene, clankercourts);
> **toolkit** (Dialect A) and **clankercourts** (Dialect B) are now migrated
> onto the package.
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

## 2. Key finding — there are two migration *classes*, not one

The fleet does not vary along a single axis. Inspection shows (at least)
**two fundamentally different starting points**, which imply two different
migrations that happen to converge on the same end state:

- **Dialect A — stock e2e (prose state).** State lives in `DEVPLAN.md` YAML
  frontmatter + markdown `- [ ]` checkboxes; the state machine is
  `tools/state_machine.sh` (bash/grep/sed mutating the frontmatter); the
  worker **reads governance files directly**; history is prose
  (`DEVLOG.md`, `DECISIONS.md`); `GOVERNANCE.md`/`WORKER_SPEC.md` are
  symlinked or copied from an `e2e/` checkout. Migrating means **serializing
  prose state into JSON `.state/`** *and* repackaging.
  → **toolkit, diplomat.**

- **Dialect B — copy-model i2c (JSON state, vendored tools); the operator's
  "first-gen i2c."** State is
  already real i2c
  already real i2c `.state/{project,phases,steps,decisions}.json` +
  `devlog.jsonl`; the i2c Python toolchain (`tools/state.py`,
  `assemble_context.py`, `validate.py`), `schemas/`, and per-action
  `instructions/` are **copied in-repo**; the worker gets a pre-assembled
  prompt and reads zero governance files; 2-line exit signal. Migrating
  means **de-vendoring** (delete the copied framework, depend on the
  package) and **retargeting the tool surface** (`python3 tools/state.py`
  → `i2c state`, per D-pkg-4) — **no data conversion at all**.
  → **clankercourts only** — the *sole* first-gen i2c project; first-gen was
  a transitional form and no further Dialect B projects will be created, so
  this is a one-off hand migration, **not** a converter target.

This split is the most important design input. A converter that assumed a
single "e2e → i2c" data transform would do the wrong thing for Dialect B
(whose data is already correct) and under-serve Dialect A (whose data needs
real parsing). **The name is not a reliable signal** — diplomat's adapter
already calls its framework "From Idea to Code" while running the e2e prose
machinery; toolkit's calls it "e2e." Detect by *machinery*, not by label.

### Detection signals (cheap, file-existence level)

| Signal | Dialect A (prose e2e) | Dialect B (copy i2c) |
|---|---|---|
| `.state/*.json` present | ✗ | ✓ |
| `tools/state.py` / `schemas/*.schema.json` | ✗ | ✓ |
| `DEVPLAN.md` with `- [ ]` steps | ✓ | ✗ |
| `tools/state_machine.sh` (bash) | ✓ | ✗ |
| State machine | bash, mutates frontmatter | python, reads `.state/` |
| Worker reads governance files | yes (tiered @-reads) | no (pre-assembled) |
| Exit signal | 5-line (toolkit) / TBD | 2-line |

## 3. What's invariant vs what varies

### Invariant — true for every dialect (the converter can hard-code these)

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

| # | Axis | Dialect A baseline | How it drifts | Converter impact |
|---|---|---|---|---|
| 1 | **Dialect** | prose e2e | vs copy-model i2c (B) | **Detect** → choose migration class |
| 2 | **Layer** | 0 (supervised) or 0+1 (autonomous) | Layer-0-only lacks `state`/runner/worker | **Detect** → skip Layer-1 transforms |
| 3 | **Framework version** | latest e2e | copied trees drift (memo: clankercourts behind a full FU) | **Detect** → version-aware parse / `i2c migrate` |
| 4 | **Frontmatter** | `phase/blocked/state/steps_remaining` | `blocked` ∈ {null,false,true,`awaiting-human-audit`}; `state` absent on L0 | **Normalize** → project.json |
| 5 | **Phase ID type** | int (toolkit, diplomat, clankercourts) | alphanumeric — GOVERNANCE `3b`; **phosphene `MVP.4d`** | **Blocker** — i2c schema requires *integer* `phase`/`id` (confirmed by prototype); converter refuses non-integer phase. Needs a schema decision or renumbering (Q-mig-7) |
| 6 | **Step representation** | `- [ ]` under `## Phase N` | separate plan files (toolkit `CLANKMATES_CLIENT_PLAN.md`); heading variants | **Parse/manual** → highest extraction risk |
| 7 | **Sub-tracks** | one DEVPLAN/DEVLOG | `DEVPLAN_<TOPIC>.md` / `DEVLOG_<TOPIC>.md` pairs | **Detect** → merge or per-track |
| 8 | **DEVLOG format/order** | `### Step N` + Mode/Outcome, newest-last | toolkit uses `## date —`, newest-**first** (admitted drift, CLAUDE.md:51) | **Lenient parse** or snapshot-don't-port |
| 9 | **Regime → budget** | Build=steps; Refine=time; Explore open | single-pass projects commonly end in a Refine (time-budget) phase | **Auto** → `budget_type` follows the current phase's regime (refine→time, build→steps; else omitted — i2c infers) |
| 10 | **Exit-signal format** | 5-line (toolkit) | 2-line (B); stale variants | **N/A to data** — only the deleted runner |
| 11 | **Adapters** | CLAUDE/CODEX tiered-read | project-specific module tables/rules | **Manual** → re-scaffold + paste project sections |

## 4. Per-project audit (in progress)

Verified by inspection 2026-06-26. Cells marked *TBD* await confirmation
against the pointed implementations.

| Project | Dialect | Layer | Phase / State | Scale | Status | Notes |
|---|---|---|---|---|---|---|
| **toolkit** | A (prose e2e) | 1 | 4 / plan, unblocked | 9 modules, all complete | Done | 5-line exit; DEVLOG newest-first (drift); GOVERNANCE symlink `../e2e/`; names fw "e2e". `CLANKMATES_CLIENT_PLAN.md` sub-plan holds some steps. |
| **diplomat** | A (prose e2e) | 1 | 48 / close, blocked | ~17 ARCH modules; heavy research tooling | In dev — at a phase boundary | Names fw "From Idea to Code" but runs e2e machinery. `DEVLOG_archive.md` → large history. Exit-signal format *TBD*. Biggest data-conversion job. |
| **phosphene** | A (prose e2e) | 1 | `MVP.4d` / execute, blocked | 9 ARCH modules; depends on toolkit | In dev — MVP | **Alphanumeric phase ID** (`MVP.4d`) — confirms axis 5. GOVERNANCE.md + DEVLOG+archive; many `DESIGN_*.md`. Same machinery as toolkit/diplomat. |
| **clankercourts** | B (first-gen i2c) | 1 | 15 / plan | ~13 modules; phases 1–14 done, 15 pending | **Migrated 2026-06-29** | Clean i2c `.state/`; **schema v0** (no `schema_version`; `budget_type:"steps"`; **no `blocked` field** → 0→1 drop-blocked is a no-op, just stamp version). Vendored `tools/state.py`+`schemas/`+`instructions/`; 2-line exit. Mojibake from the copied writer — normalized at byte level (5 fixes across `decisions.json` + `phases.json`). Migration was packaging-only; **done 2026-06-29** (commits 904d32b, c3aacfd) — see §5.2. |
| *(others)* | *TBD* | | | | | codexbot + any further consumers the operator points to. |

**Reading so far:** the fleet spans the *entire* evolution of the framework
— from pure prose state (toolkit, diplomat, phosphene) to a vendored
first-gen copy of i2c itself (clankercourts). diplomat is the worst case for
Class A (48 phases, archived history); clankercourts is the cleanest case
(already i2c, just packaged wrong) — but note it is **in-dev**, so its live
`.state/` must convert losslessly. Two of the four (phosphene, clankercourts)
are still in development, which raises the fidelity bar for those two.

## 5. Converter design

**The reusable converter targets Dialect A only.** Dialect B has exactly one
instance (clankercourts) and no successor will ever be created — first-gen
i2c was a transitional form. Automating an N=1 migration is over-engineering;
clankercourts is a **one-off hand migration** (recipe in §5.2), not a code
path in `i2c import`.

So `i2c import` is a single-dialect, **three-stage** tool — detect →
transform → report — for prose-e2e projects:

```
i2c import [PATH]            # Dialect A (prose e2e) only
  ├─ detect()  → layer, version, phase-id form, step-source, sub-tracks  (§2/§3)
  │              (if it finds Dialect B → refuse, point to the §5.2 recipe)
  ├─ transform → serialize prose state → .state/ JSON  + de-vendor + adapters
  └─ report()  → manual-review list + `i2c migrate --check` + verify
```

### 5.1 Class A — prose e2e → i2c (`toolkit`, `diplomat`)

Deterministic, parameterized by the detected profile:

- **Auto:** frontmatter → `project.json` (normalize axis 4); "Current
  Status" table + phase sections → `phases.json` (regime, status, axis 5/9);
  `DECISIONS.md` D-# entries → `decisions.json`.
- **Parse / profile-dependent:** `- [ ]` checkboxes → `steps.json`,
  including separate plan files (axis 6) and sub-tracks (axis 7). For
  *closed* phases, collapse to summaries — full per-step fidelity is not
  required.
- **Skippable history:** `DEVLOG.md`(+archive) → `devlog.jsonl`. For done
  projects, **snapshot the prose as an archive doc and start the JSONL
  fresh** rather than parsing newest-first/format-drifted prose (axis 8).
  Port history only where an in-dev project needs it.
- **Manual (Stage 1 cut-over, §5.4):** adapter rewrite — re-scaffold the i2c
  `CLAUDE/CODEX` and paste the project-specific *Available Modules* +
  *Project-Specific Notes* verbatim (axis 11).
- **Delete (at finalize, §5.4):** `tools/state_machine.sh`, `run-iteration.sh`,
  `tools/*.py` log parsers, `WORKER_SPEC.md`, `GOVERNANCE.md`,
  `.claude/commands`, the `e2e/` symlinks.

### 5.1.1 Prototype status — toolkit (shipped 2026-06-26)

The Dialect-A converter is implemented and proven on toolkit:

- **Code:** `i2c/import_e2e.py` (detect → transform → report) + `i2c import`
  CLI subcommand (dry-run default; `--apply`, `--force`, `--port-history`,
  `--json`); `tests/test_import_e2e.py` (12 tests). Full suite green (453).
- **Run on toolkit:** emitted a schema-valid `.state/` — `project.json`
  (phase 4, state plan, 3 gotchas, `budget_type=steps`, `schema_version=1`,
  `blocked`/`steps_remaining` dropped), `decisions.json` (9 records, D-1..D-9,
  status/priority mapped), `phases.json` (loop-era ids 1, 2, 4), empty
  `steps.json` / `devlog.jsonl`. Verified by `i2c status` + `i2c migrate
  --check` (“up to date: schema v1”). **Non-destructive** — git showed only
  `?? .state/`; Dialect-B refusal on clankercourts confirmed (exit 2, no write).
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

### 5.2 One-off hand migration — clankercourts (not converter scope)

N=1; never recurs. Do it by hand once, no automation. No data conversion —
almost entirely deletion + find-replace + an existing command:

- **De-vendor:** delete in-repo `tools/{state,assemble_context,validate}.py`,
  `schemas/`, `instructions/`, copied `WORKER_SPEC.md`; add the package as a
  dependency.
- **Retarget tool surface (D-pkg-4):** `python3 tools/state.py …` →
  `i2c state …` and `assemble_context.py` → `i2c assemble …` across
  adapters, `instructions/` overrides (if kept), and the runner.
- **Schema reconcile:** run `i2c migrate` to stamp `schema_version` and
  apply the 0→1 transform (drop retired `blocked`, etc.). `i2c migrate
  --check` is the acceptance gate. **This is the one place the existing
  migration command already does the work** — Class B is largely "de-vendor,
  then `i2c migrate`."
- **Keep overrides if drifted intentionally:** any genuinely customized
  `instructions/*.md` can stay as project-local overrides (§5.3 of the
  packaging memo's resolution order); everything unmodified is dropped.

  **Executed 2026-06-29** (commits 904d32b encoding, c3aacfd cut-over):
  de-vendored onto the package; `i2c migrate` stamped schema v1; 5 mojibake
  fixes across `decisions.json` + `phases.json`. Verified `i2c doctor` /
  `status` / `assemble` clean, no `src`/`tests` breakage. Two deviations from
  this recipe: (a) `schemas/map.schema.json` is project-specific (used by
  `src/` + two tests), so it was **kept in place** — only the 6 i2c schemas
  were deleted; (b) `run-iteration.sh` (a codexbot shim already pointing at a
  missing path) was **deleted**, not retargeted — clankercourts is driven via
  `i2c run` / the Telegram bot, and repointing codexbot is a separate
  follow-up. Also: the copied-writer mojibake spanned `phases.json` too, not
  just `decisions.json`.

### 5.3 Shared end-state validator

Both classes finish with the same checks so "migrated" means one thing:
`i2c status` reads the new `.state/`; `i2c migrate --check` is clean;
`i2c assemble --action <state> --mode supervised` produces a sane prompt;
the project's own tests still pass.

### 5.4 Cut-over: from `.state/` to the i2c runtime (manual, staged)

`i2c import` does the **state-serialization half** — it produces `.state/` but
leaves the project still running the e2e loop. The **runtime half** (making the
project actually run on installed i2c) is separate and, for this fleet,
**manual** — one operator, a handful of projects; automating it isn't worth it.

**Why a project can't run both loops.** After import a Dialect-A project holds
two *unsynchronized* representations: the e2e loop reads/writes `DEVPLAN.md`
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

- **clankercourts (Dialect B)** is a **one-off hand migration**, not part of
  the converter — N=1 with no successor. Cheap regardless: de-vendor + the
  existing `i2c migrate`. Caveat: it's **in-dev** with live `.state/`, so do
  it carefully — lossless conversion + encoding normalization — but by hand.
- **Class A (toolkit)** is moderate but mostly *skippable* history for done
  projects: the non-negotiable core (project.json + phases.json + adapters +
  delete-set) is small; steps/devlog fidelity is optional.
- **Class A (diplomat)** is the real test of the parser: 48 phases and
  archived history. If the converter handles diplomat's step/devlog
  extraction acceptably (or we accept snapshot-don't-port for its closed
  phases), it handles anything in the fleet.

Automating **Dialect A** looks worthwhile: it has multiple instances
(toolkit, diplomat, phosphene, + any of the operator's other prose-e2e
projects), the invariant core is large, and the dialect is regular. The risk
concentrates in axis 6/7/8 (step + history extraction) — exactly where
snapshot-don't-port de-risks done projects.

## 7. Open questions

- **Q-mig-1:** *(resolved by scoping)* `i2c import` handles Dialect A only;
  clankercourts is a hand migration (§5.2). No dialect front-end / `--dialect`
  matrix — just detect-and-refuse on Dialect B.
- **Q-mig-2:** for Class A done projects, default to **snapshot-don't-port**
  DEVLOG history (archive the prose, fresh JSONL) vs best-effort parse?
  Leaning snapshot-by-default, `--port-history` opt-in.
- **Q-mig-3:** how much step fidelity do in-dev Class A projects need —
  current phase only, or all open phases? (Closed phases collapse to
  summaries regardless.)
- **Q-mig-4:** *(resolved 2026-06-29)* clankercourts' `.state/` is clean i2c
  shape at schema **v0** (no `schema_version`; no `blocked` field → the 0→1
  drop-blocked migration is a no-op, so it needs only a version stamp). Confirmed 2026-06-29 (clankercourts migrated, §5.2): `i2c migrate --check` against it; whether `budget_type` matches
  the current schema's budget field name; and encoding normalization (mojibake
  `Â§`).
- **Q-mig-5:** *(updated)* phosphene confirmed (Dialect A, in-dev). Still
  open: codexbot + any further consumers, and whether any is on a *third*
  dialect (Layer-0-only, or an intermediate between e2e-prose and first-gen
  i2c). All four audited so far are Layer 1.
- **Q-mig-6:** *(resolved)* migration tooling (`i2c import` + the manual
  cut-over, §5.4) is **fleet-internal and one-time** — the operator is the sole
  e2e user; any public i2c adoption is greenfield. It is **excluded from the
  public package surface at release** (kept in-repo for fleet work; relocate it
  out of the shipped CLI as a pre-release cleanup). The cut-over is **not**
  automated into the tool — not worth it for one operator.
- **Q-mig-7:** *(new, blocker)* alphanumeric phase ids. i2c's schema requires
  integer `phase`/`id`, but Dialect A allows `3b` / `MVP.4d` (phosphene). Two
  options: (a) relax the schemas to accept string ids (ripples into
  state.py/state_machine/control), or (b) a renumbering transform in the
  converter (map `MVP.4d`… → a clean integer sequence, recorded in the import
  report). Must be decided before converting phosphene.

## 8. Next steps

1. Operator points to the remaining implementations (codexbot, any others).
2. Fill in the §4 audit cells marked *TBD* — especially clankercourts'
   `.state/project.json` and `i2c migrate --check` result (Q-mig-4), and
   diplomat's exit-signal/step-source details.
3. Confirm whether a third dialect exists in the fleet (Q-mig-5).
4. *(done 2026-06-26)* Dialect-A converter prototyped and proven on toolkit
   (§5.1.1).
5. Next: run it against **diplomat** (the 48-phase stress test for the phase/
   collision parser), and decide **Q-mig-7** (alphanumeric phases) before
   **phosphene**. *(clankercourts hand-migrated 2026-06-29, §5.2.)*
6. Take toolkit fully onto the package via the staged cut-over (§5.4): Stage 1
   cut-over → spin up the Telegram bot → run the queued phase(s) → finalize.

## 9. Decisions (proposed — not yet ratified)

| # | Decision | Status |
|---|---|---|
| D-mig-1 | Two migration classes exist, but only Dialect A (prose-e2e→i2c) is automated; Dialect B (first-gen i2c) is N=1 (clankercourts) and handled as a one-off hand migration. Detect by machinery, not by framework name. | proposed |
| D-mig-2 | `i2c import` is single-dialect (Dialect A only); on detecting Dialect B it refuses and points to the §5.2 hand-migration recipe. No `--dialect` matrix. | proposed |
| D-mig-3 | Class A done-project history is snapshot-not-ported by default (`--port-history` to opt in). | proposed |
| D-mig-4 | clankercourts' one-off migration reuses the existing `i2c migrate` for schema reconciliation (stamp version; drop-blocked is a no-op) plus de-vendor + tool-surface retarget + encoding normalization. Done by hand, once. | proposed; **executed 2026-06-29** |
| D-mig-5 | Narrative docs (PROJECT/ARCHITECTURE/ARCH_*/API) are pass-through in every class; the converter never rewrites them. | proposed |
| D-mig-6 | Migration is staged: serialize (`i2c import`) → cut-over (manual, §5.4) → trial → finalize. The runtime switch (adapters + neutralize shadowing markdown + i2c.toml + install) precedes any i2c trial; a project never runs both loops at once. | proposed |
| D-mig-7 | Migration tooling is fleet-internal and one-time: excluded from the public i2c surface at release (public adoption is greenfield-only); the cut-over stays manual rather than a tool feature. | proposed |

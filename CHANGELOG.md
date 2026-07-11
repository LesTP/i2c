# Changelog

All notable changes to i2c are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and from this release
forward the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is the public counterpart to `STATUS.md` (internal tracking).

## [Unreleased]

### Added

- **`project.json.pattern` — explicit architecture pattern (A/B).** New optional
  field recording whether a project uses per-module `ARCH_<module>.md` contracts
  (`"A"`) or a single-document `ARCHITECTURE.md` (`"B"`); **absent ⇒ `"A"`**
  (back-compat). `i2c init` stamps it (new `--pattern A|B` flag, default `A`);
  change it later with `i2c state set project.json pattern=A|B`. It is the
  authoritative signal the assembler uses to decide whether a per-module
  contract is required. See `ref/SPEC_architecture.md`.

- **`i2c dashboard` — self-contained HTML snapshot (v0, tables-only).** A new
  subcommand emits a single offline `dashboard.html` (no server, no auth, no
  network) that opens in any browser and syncs over the shared disk. It follows
  the design's frozen-shell / bound-data split (D-dash-6..10): a human-owned,
  frozen Pico.css shell (`i2c/data/dashboard/{shell.html,style.css,bind.js}`)
  into which the generator only binds an allowlisted `control.DashboardModel`
  (JSON → DOM). Modes: **single-project** when run inside a project (or given a
  project dir), **portfolio** over a parent folder; `--out` chooses the path
  (default `dashboard.html`, gitignored), `--json` prints the model. The model
  is the single allowlist seam — it reads only `.state/` (portfolio/status), the
  non-secret `[run]` config, and `doctor`, and **never** `[telegram]` or the
  environment (D-dash-3). Panels: portfolio table, per-project drill, health.
  The telemetry aggregator + Chart.js charts are the tracked v0.1 follow-up.

- **Backend rate-limit / infra-error exit code (`exit 3`).** The runner now
  detects when the *backend itself* refused — claude's `--output-format json`
  carries `is_error: true` + `api_error_status` (429 = usage/rate limit) — and
  surfaces it distinctly: a clear summary reason (`backend rate-limited
  (HTTP 429): …`) plus runner **exit code 3** ("backend unavailable, retryable"),
  instead of the generic `exit=2 "exit signal missing or malformed"` that made a
  quota hit indistinguishable from a real worker error. Nothing lands on a
  rate-limit (invariants + commits skipped). The worker exit-signal schema (0|2)
  is unchanged — this is a runner-level infra outcome. codex detection and
  bot-side `/batch` handling of exit 3 are follow-ups.

### Changed

- **Runner owns all git commits (FU-40 complete).** The deterministic runner
  now commits REVIEW fix-ups (`<phase>: <review summary>`) and CLOSE docs
  (`<phase>: <close summary>`, alongside the separate `.state/` + telemetry
  commit), joining the EXECUTE code commit shipped earlier. The worker no
  longer runs `git` for any action — it edits files and writes `.state/` via
  `i2c state`, and the runner commits deterministically after it exits, fencing
  off operator working-tree changes. This removes the interactive-hang /
  wrong-scope / forgotten-commit hazards and guarantees the
  `<phase>.<step>:` / `<phase>:` commit-message format recovery's
  `commit_exists_step_pending` check relies on (also closes FU-8). The
  `execute` / `review` / `close` instruction files, `WORKER_SPEC.md`, and the
  Claude/Codex adapters were updated to match; worker-prompt goldens
  regenerated.

### Removed

- **Multi-step / `--step-budget` machinery (D-run-1/D-run-2).** Declared
  single-iteration-per-invocation the design and removed the unused multi-step
  surface: the assembler's `--step-budget` flag and `multi_step_only` marker,
  the runner's hardcoded pass-through, and the multi-step LOOP / loop-discipline
  subsections of `WORKER_SPEC.md`. Rationale: one invocation is one backend, so
  cross-action multi-step is incompatible with per-action routing
  (`[run.backends]`); the only coherent unit — a single invocation running
  several EXECUTE steps for continuous context — is deferred pending
  model-benchmark evidence, since it would forfeit the per-step commit and
  per-(action, step) telemetry granularity FU-40 established. Contract docs
  (`ARCH_assembler.md`, `WORKFLOW.md`) simplified to match.

### Fixed

- **PLAN no longer wedges single-document (Pattern B) projects (FU-48).**
  Previously PLAN could write a `module` onto a phase record even for a project
  with no `ARCH_<module>.md`, after which the assembler hard-required that file
  and every later action (TESTS/EXECUTE/CLOSE) failed at prompt assembly with an
  opaque `exit=2`. Now: (1) the assembler omits the Module Contract section
  whenever `project.json.pattern == "B"`, ignoring any stray `module` (so a
  corrupted `.state/` no longer crashes the loop); (2) `instructions/plan.md`
  step 4 tells the worker to set `module` only under Pattern A; and (3) the
  Pattern A "contract missing" error now hints at setting `pattern=B` for
  single-document projects.
- **Bootstrap docs: seed `phases.json` + choose the pattern explicitly (FU-46).**
  The README bootstrap now includes an explicit "seed the phase list" step and
  documents choosing Pattern A/B at `i2c init` time, so following the steps
  verbatim no longer errors out at first `i2c assemble`.

### Migrations

- **`schema_version` 2 → 3 (no-op stamp).** Guards the additive optional
  `project.json.pattern` field: an older i2c (whose `project.json` schema is
  `additionalProperties: false`) rejects a newer, `pattern`-stamped project via
  the newer-than-current guard ("upgrade i2c") rather than an opaque validation
  error. Existing `.state/` files keep validating unchanged; run `i2c migrate`
  to re-stamp (no data transform).

## [0.2.0] - 2026-06-30

The Phase-3 control-surface arc: a single structured command/projection layer
(`i2c.control`) with an operator CLI and a Telegram surface over it.

### Added

- **Recovery (reconcile-first v1).** A deterministic workflow-drift audit
  (`i2c/recovery.py`: state-vs-`.state` plus state-vs-git/disk signals, with
  CRLF/whitespace false-positive guards) fronted by a `diagnose` entry point.
  `i2c diagnose [--target N]` runs the audit first and classifies the failure
  (`workflow-drift` / `unknown` / `none`), read-only. `i2c reconcile [--apply]`
  applies the deterministic fixes (dry-run by default; mutations go through the
  sanctioned `state.py` path) and surfaces judgment calls without touching them.
  Out-of-band dispatch (`i2c run --action diagnose|reconcile --target N`)
  bypasses the state machine; the runner also surfaces a non-fatal drift
  advisory after each lifecycle action. The Telegram surface exposes the same
  pair as `/diagnose [N]` (read-only) and `/reconcile [apply]` (admin-gated,
  dry-run unless `apply`). Extends i2c's detect-and-halt
  post-action invariants into detect-and-reconcile. See
  `archive/DESIGN_recovery_v1.md`. The full `fix` code-repair agent is deferred.
- **Per-action backend selection.** An optional `[run.backends]` table in
  `i2c.toml` maps each worker action (plan/execute/review/close) to a backend,
  resolved once the dispatched action is known. Lets a run spread load across
  backends (e.g. heavy execute on codex; plan/review/close on claude) or use an
  independent reviewer. Precedence: `i2c run --backend` (force single) >
  `[run.backends][action]` > `[run].backend` > `claude`. Backward-compatible:
  with no map, behavior is unchanged.
- **`i2c.control` command API + operator CLI.** Structured, typed read views and
  actions exposed as `i2c` subcommands: `status`, `next-action`, `phase-summary`,
  `decisions`, `devlog`, `escalation`, `logs`, `portfolio`, and `clear-boundary`,
  each with a `--json` mode for structured consumers.
- **Cross-project portfolio view.** `i2c portfolio [--root PATH]` discovers every
  project under a folder and reports each one's phase, state, next action, and
  escalation — escalations and boundaries first — to answer "which project needs
  me?".
- **Telegram surface (optional extra).** `pip install i2c[telegram]` then
  `i2c serve telegram` runs a deterministic chat bot over `i2c.control`; mutating
  commands are gated to an admin allowlist (`[telegram]` in `i2c.toml`) and the
  bot token is read only from the environment.

### Changed

- **Telegram surface refactored to a tighter, self-documenting command set.**
  Reads consolidated under `/audit [facet]` (summary by default, plus
  `phase N` / `decisions` / `devlog` / `escalation` / `logs`); `/run` gains
  `[N] [backend]` (single-backend series); `/batch` runs a full phase to a halt
  using the per-action backend map; `/use`→`/setdir`, `/help`→`/commands`,
  `/clearboundary`→`/endphase [last]`; `/status`, `/next`, `/projects` removed
  (folded into `/audit` / `/portfolio`).
- **Operator views moved to the CLI / `control`.** The assembler's operator-facing
  `--section status`, `--section phase-summary`, and `--section devlog` modes were
  removed; use the corresponding `i2c` commands (with `--json`) instead. The
  worker-prompt assembly and the `--section architecture` / `--section module`
  passthroughs are unchanged, and worker prompts are byte-for-byte identical
  (verified by committed golden snapshots).

## [0.1.0]

The Phase-2 packaging arc: i2c becomes an installable library with a stable
`i2c` command, packaged framework assets, project scaffolding, file-based
configuration, and versioned `.state/` migrations.

### Added

- **Installable package + console entry point (§5.2).** i2c ships as an
  importable `i2c` package; `pip install` puts the `i2c` command on `PATH` and
  bundles the JSON Schemas as package data. Consumers no longer copy `tools/`.
- **Override-resolved framework assets (§5.3).** `WORKER_SPEC.md` and the
  per-action `instructions/` ship inside the package and resolve
  project-local-override → packaged default, so a project customizes a procedure
  by ejecting and editing only the file it needs.
- **`i2c init` and `i2c eject` (§5.4).** `init` scaffolds a new project
  (`.state/` seed, `PROJECT.md` / `ARCHITECTURE.md`, adapter(s), `i2c.toml`,
  `.gitignore`); `eject` materializes a packaged asset into the project for local
  override.
- **`i2c.toml` run configuration (§5.5).** A `[run]` table supplies defaults for
  `i2c run` with precedence CLI flag > `i2c.toml` > built-in default.
- **Schema versioning + `i2c migrate` (§8).** `project.json` carries an optional
  `schema_version`; an absent value means legacy (version 0). `i2c migrate`
  performs versioned in-place `.state/` migrations, with `--check` (CI-friendly
  drift check; exit 1 when a migration is needed) and `--dry-run` (preview
  without writing). `i2c init` stamps the current schema version on new projects.

### Migrations

- **0 → 1:** drops the legacy `blocked` field (retired by the 7-state lifecycle
  redesign) from `project.json` and stamps `schema_version`.

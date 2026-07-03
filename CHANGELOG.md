# Changelog

All notable changes to i2c are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and from this release
forward the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is the public counterpart to `STATUS.md` (internal tracking).

## [Unreleased]

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

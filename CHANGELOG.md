# Changelog

All notable changes to i2c are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and from this release
forward the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

This is the public counterpart to `FOLLOWUPS.md` (internal tracking).

## [Unreleased]

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

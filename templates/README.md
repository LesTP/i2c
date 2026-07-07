# i2c Project Templates

Starter material the framework ships for projects to copy on bootstrap.
Nothing here is consumed by the framework itself — these files only matter once
they live where the agent harness loads them.

## What's here

| Path | Purpose |
|------|---------|
| `.llms/commands/i2c-*.md` | Supervised-mode slash-command wrappers (`/i2c-cold-start`, `/i2c-phase-plan`, `/i2c-phase-complete`, `/i2c-phase-review`, `/i2c-step-done`). Each is a thin shell around an `i2c assemble …` / `i2c state …` / `i2c status` invocation. The `i2c-` prefix keeps them alongside any existing e2e-flavored global commands. |

## Where these load — read this first (FU-20)

**Devmate loads slash commands only from the operator-global `~/.llms/commands/`.**
Project-level command directories — **both** `.llms/commands/` *and*
`.claude/commands/` — are **not** picked up by Devmate today (FU-20). (Project
`.llms/rules/` *does* load; commands do not.) Claude Code, which did auto-load
`.claude/commands/`, is retired.

So there is one working surface: **global**. Copy this set there once:

```powershell
# Windows / PowerShell
xcopy /E /I p:\shared\i2c\templates\.llms\commands  $env:USERPROFILE\.llms\commands
```

```bash
# POSIX
cp p:/shared/i2c/templates/.llms/commands/i2c-*.md ~/.llms/commands/
```

After copying, `/i2c-phase-plan`, `/i2c-step-done`, etc. appear in the Devmate
command picker (type `/` to confirm) for **every** i2c project — they read the
current project's `.state/` at invocation time, so one global copy serves the
whole fleet.

## Bootstrap pattern

`i2c init` scaffolds the rest of a new project (`.state/`, `PROJECT.md`,
`ARCHITECTURE.md`, adapters, `i2c.toml`, `.gitignore`) — it does **not** copy
these commands. They are operator-global (above), authored once, not re-seeded
per project. (Historically some projects carried per-project copies; those are
inert in Devmate and have been removed.)

## Why global, not per-project (supersedes D-prose-4 in practice)

`D-prose-4` originally chose *per-project* command copies (framework owns the
canonical template; each project keeps its own; Claude Code / Codex auto-load
`.claude/commands/`). Two facts changed that in practice:

- **Devmate is the active harness** and does not load project-level commands
  (FU-20); Claude Code (which did) is retired.
- Per-project copies drift — divergent stale copies (pre-packaging `tools/*.py`
  invocations) are exactly what FU-20 surfaced.

So the canonical set now lives here (framework-owned, one source of truth) and
is copied to the **operator-global** surface. The `i2c-` prefix lets it coexist
with e2e-flavored global commands, so no cross-framework detection is needed —
the property D-prose-4 was protecting.

## What each command does

| Command | Purpose | Underlying CLI |
|---------|---------|----------------|
| `/i2c-cold-start` | Orient on current project state | `i2c status` / `i2c next-action` |
| `/i2c-phase-plan` | Plan the next phase (supervised); Build forks to the `tests` action | `i2c assemble --action plan …` (+ `--action tests` for Build) |
| `/i2c-step-done` | Mark a step complete, log to devlog, transition if last | `i2c state complete` / `append` / `set` |
| `/i2c-phase-review` | End-of-phase review (supervised) + acceptance-suite integrity check | `i2c assemble --action review …` |
| `/i2c-phase-complete` | Close the phase, set the human gate (supervised) | `i2c assemble --action close …` |

`/i2c-step-done` is the only pure write-side command; the others assemble
context the agent reads to perform the action. All five reflect the current
lifecycle (`plan → tests → execute → review → close → audit_boundary`) and the
Build-only `tests` action.

## Where the contracts live

- Assembler CLI surface: `ARCH_assembler.md` §3
- State CLI surface: `i2c state --help` (or `i2c state SUBCOMMAND --help`)
- Per-action procedures: `instructions/{plan,tests,execute,review,close}.md`
  (ship in the `i2c` package; a project may override per-file with a local copy)
- Schemas for every write: `i2c/data/schemas/*.schema.json`

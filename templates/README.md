# i2c Project Templates

Starter material the framework ships for projects to copy on bootstrap.
Nothing here is consumed by the framework itself — these files only
matter once they live inside a real project directory.

## What's here

| Path | Purpose |
|------|---------|
| `.claude/commands/` | Per-project Devmate slash-command wrappers for supervised mode. Each wrapper is a thin shell around an `i2c assemble …` or `i2c state …` invocation. |

## Bootstrap pattern

`i2c init` scaffolds the rest of a new project (`.state/`, `PROJECT.md`,
`ARCHITECTURE.md`, adapters, `.gitignore`); this directory only adds the
supervised-mode slash-command wrappers. Copy it into the project root:

```powershell
# Windows / PowerShell
xcopy /E /I p:\shared\i2c\templates\.claude\commands  <project>\.claude\commands
```

```bash
# POSIX
cp -r p:/shared/i2c/templates/.claude/commands <project>/.claude/commands
```

After copying, the slash commands are available inside Devmate / Claude
Code when working in the project directory. Personal-level commands at
`~/.llms/commands/` are left untouched (they still point at e2e).

## Why per-project, not personal-level

Per `D-prose-4` (in the i2c rollout plan):

- The framework owns the canonical template; future CLI changes can
  evolve the template once and projects copy when they want the change.
- Each project keeps its own copy; e2e projects keep their e2e-flavored
  slash commands. No cross-framework detection logic in personal config.
- Devmate / Claude Code picks up the project's `.claude/commands/`
  automatically — no extra configuration step.

## What each slash command does

| Command | Purpose | Underlying CLI |
|---------|---------|----------------|
| `/cold-start` | Orient on current project state | `i2c assemble --section status` |
| `/phase-plan` | Plan the next phase (supervised) | `i2c assemble --action plan --phase N --mode supervised` |
| `/step-done` | Mark a step complete, log to devlog, transition if last | `i2c state complete`, `i2c state append devlog.jsonl`, `i2c state set` |
| `/phase-review` | Run end-of-phase review (supervised) | `i2c assemble --action review --phase N --mode supervised` |
| `/phase-complete` | Close the phase, gate to human (supervised) | `i2c assemble --action close --phase N --mode supervised` |

`/step-done` is the only pure write-side command — the others are
read-side (they assemble context the agent reads to perform the action).

## Status note

The `--mode` flag and per-section subcommands are implemented and ship in the
`i2c` package; all five wrappers run today once the framework is installed
(`pip install`). Each wrapper invokes the `i2c` console command.

## Where the contracts live

- Assembler CLI surface: `ARCH_assembler.md` §3
- State CLI surface: `i2c state --help` (or `i2c state SUBCOMMAND --help`)
- Per-action procedures: `instructions/{plan,execute,review,close}.md` (ship in
  the `i2c` package; a project may override per-file with a local copy)
- Schemas for every write: `i2c/data/schemas/*.schema.json`

# i2c Project Templates

Starter material the framework ships for projects to copy on bootstrap.
Nothing here is consumed by the framework itself — these files only
matter once they live inside a real project directory.

## What's here

| Path | Purpose |
|------|---------|
| `.claude/commands/` | Per-project Devmate slash-command wrappers for supervised mode. Each wrapper is a thin shell around a `python3 tools/assemble_context.py …` or `python3 tools/state.py …` invocation. |

## Bootstrap pattern

When creating a new i2c project, copy this directory into the project root:

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
| `/cold-start` | Orient on current project state | `assemble_context.py --section status` |
| `/phase-plan` | Plan the next phase (supervised) | `assemble_context.py --action plan --phase N --mode supervised` |
| `/step-done` | Mark a step complete, log to devlog, transition if last | `state.py complete`, `state.py append devlog.jsonl`, `state.py set` |
| `/phase-review` | Run end-of-phase review (supervised) | `assemble_context.py --action review --phase N --mode supervised` |
| `/phase-complete` | Close the phase, gate to human (supervised) | `assemble_context.py --action close --phase N --mode supervised` |

`/step-done` is the only pure write-side command — the others are
read-side (they assemble context the agent reads to perform the action).

## Status note

The `--mode` flag and per-section subcommands rely on
`tools/assemble_context.py`, which is **Phase 1.3 — not yet
implemented**. The wrappers are deliberately authored against the locked
ARCH contract so they ship ready to run once Phase 1.3 lands. Until
then, `/cold-start`, `/phase-plan`, `/phase-review`, and
`/phase-complete` will exit non-zero because the assembler doesn't exist;
`/step-done` works today (pure `state.py` calls, no assembler).

## Where the contracts live

- Assembler CLI surface: `ARCH_assembler.md` §3
- State CLI surface: `tools/state.py --help` (or `tools/state.py SUBCOMMAND --help`)
- Per-action procedures: `instructions/{plan,execute,review,close}.md`
- Schemas for every write: `schemas/*.schema.json`

# DESIGN — Telegram surface refactor + per-action multi-backend (v1)

> **Status: ✅ implemented & shipped, then archived (2026-06-29).** Both halves
> landed: Part A (per-action `[run.backends]` resolution in config → runner →
> CLI) and Part B (the Telegram surface refactor + slash-command menu). All
> decisions D-sb-1..6 are accepted. Kept for the *why*, not the *what* —
> current truth lives in `../README.md` + `../CHANGELOG.md` (per-action
> backends, the Telegram command set) and `../DESIGN_packaging_v1.md` §6/§7
> (backend abstraction + control surface). Original scope was the **bot
> surface** (`i2c.surfaces.telegram*`) plus the shared **backend resolution**
> (config → runner → CLI) that both `i2c run` and the bot inherit; the CLI's own
> command set (`i2c status`, `i2c logs`, …) was out of scope. Originated from the
> bot-command + multi-backend discussion (2026-06-27).

## 1. Motivation

Two unrelated problems, fixed in one pass because they meet in the bot's
`/run`/`/batch`:

1. **One backend per project is too rigid.** The operator can't run everything
   on a single backend (token/quota limits), and wants **independent review**
   (a different backend reviews than builds). The runner already takes
   `--backend` per invocation; the gap is (a) no *per-action* selection and
   (b) the bot hard-codes the project's single `[run].backend`. This is the §6
   "backend abstraction" / axis-2 work the packaging memo anticipates.
2. **The bot command surface grew organically** and drifted from the operator's
   codexbot muscle memory: a `/next` that means something different than
   codexbot's old `/next` (a trap), redundant `/portfolio` vs `/projects`, a
   verbose `/clearboundary`, and read-command sprawl.

## 2. Scope & non-goals

**In scope:** per-action backend map (`config`, `run_iteration`, `cli.cmd_run`);
Telegram surface refactor (`telegram_core`, `telegram`); `i2c.toml` template;
docs; tests.

**Non-goals:** per-action *model* selection (backend-only for now); recovery
actions (`reconcile`/`diagnose`/`fix` — see `FUTURE_recovery.md`); the CLI's own
subcommands; any orchestrator/loop-driver work.

## 3. Part A — per-action multi-backend

### 3.1 Config (`config.py`)
Add an optional action→backend map under `[run]`:
```toml
[run]
backend = "claude"        # default / fallback (unchanged)
[run.backends]            # optional per-action override
plan = "claude"
execute = "codex"
review = "claude"
close = "claude"
```
- `RunConfig` gains `backends: dict[str, str] = {}`.
- `load_run_config` parses `run["backends"]` and validates **keys ∈
  {plan, execute, review, close}** and **values ∈ {claude, codex}**, raising
  `ConfigError` otherwise. Unknown keys still ignored elsewhere (forward-compat).

### 3.2 Runner (`run_iteration.py`)
The runner computes the next ACTION (state machine) *before* invoking a backend,
so backend selection moves to just after that point:
- Signature: `run_iteration(*, backend=None, backend_map=None,
  default_backend="claude", model, max_budget_usd, claude_invoker=…,
  codex_invoker=…)`.
- Resolution (only reached for plan/execute/review/close; EXIT short-circuits
  earlier): `resolved = backend or backend_map.get(action.lower(),
  default_backend)`.
- `backend` = explicit single-backend override; `backend_map` = the
  `[run.backends]` dict; `default_backend` = `[run].backend`.
- `control.run_iteration` is the same re-exported function, so this flows
  through unchanged.

### 3.3 CLI (`cli.cmd_run`)
Resolution precedence:

| Priority | Source |
|---|---|
| 1 | `--backend` flag (forces a single backend) |
| 2 | `[run.backends][action]` |
| 3 | `[run].backend` |
| 4 | built-in `claude` |

`cmd_run` passes `backend=args.backend, backend_map=cfg.backends,
default_backend=(cfg.backend or "claude")`. **Behavior change:** `i2c run` with
no `--backend` resolves per-action *when a map is configured*; with no map it
behaves exactly as today. `--backend X` still pins one backend. Model/budget
resolution is unchanged.

### 3.4 The elegant part
Because the bot's `/run`/`/batch` shell `i2c run`, **the bot inherits per-action
backends for free** once the runner/CLI support them — no bot-side backend logic.

## 4. Part B — Telegram surface refactor

### 4.1 Final command set (~7)

| Command | Maps to |
|---|---|
| `/audit [proj] [facet] [N]` | facet ∈ {∅→summary, `phase N`, `decisions [N]`, `devlog [N]`, `escalation`, `logs [N]` / `logs iter N`} → existing `control` projections + `render._render_*` |
| `/portfolio` | `control.portfolio` (render the project **path**, so fixtures like `examples/initial_state` are obvious) |
| `/setdir <proj>` | set the chat's current project |
| `/commands` (+ `/start`) | help listing |
| `/run [proj] [N] [backend]` | up to N iterations (default 1) on a **single** backend (arg, else `[run].backend`); stop at a halt (`next_action == EXIT`) or non-zero |
| `/batch [proj]` | loop to a halt using the **per-action map** (no N) |
| `/endphase [proj] [last]` | `control.clear_boundary(advance = "last" not in args)` |

**Dropped from the bot** (folded into `/audit` or removed): `/status`, `/next`,
`/projects`, `/use`, `/clearboundary`, `/phasesummary`, `/devlog`,
`/decisions`, `/escalation`, `/logs`.

### 4.2 codexbot → i2c alignment (resolved)
- `/next` (codexbot = old name for run) **dropped** — i2c's `/next` was a
  different (preview) thing; the collision was the trap.
- `/setdir` kept (over i2c's `/use`).
- `/commands` kept (over i2c's `/help`).
- `/endphase [last]` replaces `/clearboundary` — and stays **distinct from the
  CLOSE worker action** (run via `/run`), which e2e conflated under `/close`.
- `/projects` dropped (redundant with `/portfolio`).
- `/audit` repurposed as the deterministic read-hub (codexbot's `/audit` was
  LLM-mediated; the LLM path is the future `/ask`/recovery work).

### 4.3 Code changes
**`telegram_core.py`:**
- `READ_COMMANDS = {audit, portfolio, setdir, commands, start}`;
  `MUTATING_COMMANDS = {run, batch, endphase}`.
- `run_iteration_fn` type → `Callable[[Path, str | None], int]` (proj, backend
  override).
- `/run` parses project (leading, via `_resolve_project`), then `int → N`,
  `token ∈ {claude,codex} → backend`; loops N calling `run_iteration_fn(proj,
  backend)`, breaking on `next_action == EXIT` or non-zero (today's `/batch`
  halt logic).
- `/batch` loops to halt calling `run_iteration_fn(proj, None)` (None → CLI uses
  the map; degrades to `[run].backend` for all actions if no map).
- `/audit` routes by facet to the existing `status`/`phase_summary`/`decisions`/
  `devlog`/`escalation`/`logs[_transcript]` projections + renderers.

**`telegram.py`:**
- `_make_runner` → `fn(proj, backend=None)` that shells `i2c run` (cwd=proj)
  with `--backend` **only when overridden**; drops the explicit
  `--model`/`--max-budget` passthrough (`cmd_run` reads them from the project's
  `i2c.toml`).
- `build_application` auto-registers the new `ALL_COMMANDS`.

## 5. Files & tests

| File | Change |
|---|---|
| `i2c/config.py` | `[run.backends]` parse + validate; `RunConfig.backends` |
| `i2c/run_iteration.py` | per-action backend resolution |
| `i2c/cli.py` | `cmd_run` precedence (backend/map/default) |
| `i2c/surfaces/telegram_core.py` | command set, grammar, `run_iteration_fn` arg |
| `i2c/surfaces/telegram.py` | `_make_runner` backend override; registration |
| `i2c/data/templates/i2c.toml` | commented `[run.backends]` example |
| `README.md`, `DESIGN_packaging_v1.md` §6, `CHANGELOG.md` | docs |

**Tests:**
- `test_config` — `[run.backends]` parse + key/value validation.
- `test_run_iteration` — resolution: override > map[action] > default; EXIT
  skips selection.
- `test_telegram_core` — new grammar: `/run [N] [backend]`, `/batch`,
  `/endphase [last]`, `/audit` facets; admin gating unchanged.
- `test_telegram_wiring` — `ALL_COMMANDS` registration.
- Check `test_surface_switch` for fallout.

## 6. Decisions

| ID | Decision | Status |
|---|---|---|
| D-sb-1 | Per-action backend is a `[run.backends]` map resolved **in the runner** (so CLI + bot both inherit); keys ∈ {plan,execute,review,close}, values ∈ {claude,codex}. | accepted |
| D-sb-2 | Precedence: `--backend` > `[run.backends][action]` > `[run].backend` > `claude`. | accepted |
| D-sb-3 | `/run [N] [backend]` = single-backend series; `/batch` = full-phase per-action map (no N). Drop the e2e `batch N` (multi-step) semantic. | accepted |
| D-sb-4 | Consolidate bot reads under `/audit` (default summary + facets); rename `/use`→`/setdir`, `/help`→`/commands`, `/clearboundary`→`/endphase [last]`; drop `/next`, `/projects`, `/status`. | accepted |
| D-sb-5 | Per-action **model** selection is out of scope (backend-only for now). | accepted |
| D-sb-6 | The CLI's own command set is unchanged; only `i2c run` gains map-awareness. | accepted |

## 7. Rollout

One change set (Part A then Part B in the same pass, since the bot depends on
the runner's map support). Lands behind no flag — fully backward-compatible:
absent `[run.backends]`, `i2c run` and the bot behave exactly as today. After
this: **task 2** (Change 2) can run via the redesigned `/batch` with the
claude/codex split.

# Packaging & Distribution v1

> Design memo. Captures how i2c becomes a shareable, open-source framework
> that a newcomer can install and use for their own builds — replacing the
> current "copy the framework files into each project and invoke the
> pipeline from an adjacent `../i2c/` checkout" model with an installed
> Python dependency. The copy-and-sync model is the single biggest barrier
> to sharing: it requires every consumer to keep `tools/`, `instructions/`,
> `schemas/`, `WORKER_SPEC.md`, and adapters byte-synced with upstream via
> preflight hash diffs (FU-19/FU-21 ABI rules), and it silently rots — the
> clankercourts assembler drifted a full FU behind upstream (stale 5-line
> exit reminder, pre-FU-7) before anyone noticed, surfaced 2026-06-21 while
> porting FU-35.
>
> Status: **proposed / discussion** (2026-06-21). Not yet scheduled. This
> memo records the decisions taken in the packaging discussion and the
> design they imply; implementation is phased (see §9).
>
> Authors: operator + assistant, 2026-06-21.

---

## 1. Problem

i2c works well for one operator running several projects off a shared
disk. Distribution to a stranger who "just wants to clone a repo and get
going" fails on the current model:

- **The dependency is copy-based.** A consumer carries verbatim copies of
  the framework (`tools/`, `instructions/`, `schemas/`, `WORKER_SPEC.md`,
  `CLAUDE.md`/`CODEX.md`). They must stay byte-identical to upstream —
  the documented "preflight diff" in `FOLLOWUPS.md` exists precisely to
  catch drift, and the worker procedures hard-depend on `state.py`
  argument-form features (FU-19 bare-filename resolve, FU-21
  `--from-file`). There is "no may-lag mode that is actually safe."
- **The pipeline is invoked from an adjacent checkout.** Autonomous runs
  call `python3 ../i2c/tools/run_iteration.py`, which only resolves
  because i2c sits next to the consumer on the same disk. Not portable.
- **The repo assumes Meta.** Internal server (`pirozhok`) over SSH,
  Meta-laptop subprocess sandbox (FU-28), Devmate slash-command loading
  (FU-20), codexbot/Telegram surface, internal paths (`p:\shared\`,
  `~/claude-code-workspace`), and internal project names throughout the
  docs and `FOLLOWUPS.md`.
- **Bootstrap is a manual 6-step checklist** (README "Bootstrap a new i2c
  project") — copy files, hand-write `.state/` JSON, author project docs,
  run the smoke test.

Net: the framework is not self-contained, not versioned as a unit, and
not installable.

## 2. Goals / Non-goals

**Goals**
- A newcomer installs i2c and scaffolds a working governed project in a
  handful of commands ("clone-and-go"), with no copying and no adjacent
  checkout.
- The framework is **self-contained** — a consumer project carries only
  its own state and documents, not framework code.
- Drift is structurally impossible: the framework version is pinned like
  any dependency.
- Preserve i2c's transparency ethos: state stays on disk and git-tracked;
  operators can still customize instructions/adapters.
- A clean layered driver model: a deterministic command surface (CLI/chat)
  with an optional pluggable orchestrator on top, and a swappable worker
  backend underneath (see §7).
- Open-source-ready: clean public docs, license, examples, CI.

**Non-goals (v1)**
- A GUI / VS Code extension (that is `FUTURE_waymark.md`).
- Hosting a managed service. i2c stays a local tool.
- Migrating existing internal consumers off the copy model in lockstep —
  the package and the legacy copy model can coexist during transition.

## 3. Decisions taken in this session

| # | Decision | Notes |
|---|----------|-------|
| **D-pkg-1** | Distribution target is **public open-source** (PyPI + a public Git host). | Drives licensing, full de-Meta-ification, and public-quality docs. |
| **D-pkg-2** | Dependency model is an **installed package** (`pip install i2c`), made **as self-contained as possible** — not copy-and-sync. | The project carries only `.state/` + project docs + (optionally) editable overrides. |
| **D-pkg-3** | **Abstract the backend** behind an interface; `claude` and `codex` are the first two adapters. Deferrable if it proves a big lift, but desired — the operator keeps hitting per-backend limits and wants a third backend (e.g., Gemini). Must stay conscious of **provider-specific behavior**, especially prompt caching (FU-35). | See §6. |

## 4. Target experience

```
pip install i2c                 # or pipx install i2c
cd my-project && git init
i2c init                        # scaffolds .state/, PROJECT.md, ARCHITECTURE.md, adapter, i2c.toml
#  ...edit PROJECT.md + ARCHITECTURE.md...
i2c plan                        # supervised first phase (assembles context, pauses for approval)
i2c run --backend claude        # autonomous single-iteration loop
```

No `../i2c/`, no file copying, no preflight hashing. Everything the
framework needs ships in the package; everything the project owns is
created by `i2c init` and lives in the project's git history.

## 5. Architecture

### 5.1 The key unlock — change the worker tool surface

Today the worker's procedures say `python3 tools/state.py ...`, which is
*why* every consumer must carry an ABI-compatible local `state.py`. If the
procedures instead call a **stable console command** —

```
i2c state set project.json state=execute
i2c state append devlog.jsonl --from-file payload.json
```

— the consumer carries **zero** framework code, and the entire
copy-and-sync apparatus (preflight diffs, ABI rules, "may safely lag")
disappears. This is the single highest-value change in the whole effort.
It touches: `instructions/*.md`, the adapter Tool Rules, and the runner's
internal subprocess calls.

> **D-pkg-4 (decided):** the worker tool surface is the `i2c` console
> command, not `python3 tools/<x>.py`. Procedures, adapters, and the
> runner are rewritten to call it.

### 5.2 Make `tools/` an importable package

The current sibling-import pattern (`sys.path.insert(0, tools_dir);
import assemble_context`) does not survive `pip install`. Restructure into
a proper package with absolute imports and `console_scripts` entry points:

```
i2c/
├── __init__.py
├── cli.py                  # `i2c` dispatcher: init, plan, run, state, assemble, validate, migrate
├── state.py  validate.py  assemble_context.py
├── state_machine.py  invariants.py  run_iteration.py
├── data/                   # packaged via importlib.resources
│   ├── schemas/*.json
│   ├── instructions/*.md
│   ├── WORKER_SPEC.md
│   └── adapters/{claude,codex}.md
```

Schemas, instructions, `WORKER_SPEC.md`, and adapter defaults ship as
**package data** read through `importlib.resources` — not files on the
consumer's disk.

### 5.3 Preserve hackability via override resolution

i2c's identity is transparency and operator control (tweakable
instructions). Installing the framework must not hide it. Resolution
order for every governance source becomes:

```
project-local override (e.g. ./instructions/plan.md)  →  packaged default
```

`i2c init` optionally scaffolds editable copies into the project for those
who want to customize; absent an override, the packaged default is used.
Best of both: clean and version-pinned by default, fully hackable when
wanted. (This generalizes the assembler's existing "read from disk"
behavior into an explicit override-then-default lookup.)

### 5.4 `i2c init` (bootstrap CLI)

Replaces the manual 6-step bootstrap: seed `.state/` (`project.json`,
empty `phases/steps/decisions`, `devlog.jsonl`), write PROJECT.md and
ARCHITECTURE.md templates, generate a filled adapter stub, add
`logs/loop/` to `.gitignore`, and stamp `schema_version` (§7).

### 5.5 Configuration

Replace remembered CLI flags with an `i2c.toml` at the project root:
backend, model, budget defaults, and override paths. CLI flags still win
for one-off overrides. API auth/secrets stay out of the repo (env vars,
documented in the quickstart).

## 6. Backend abstraction

Today `run_iteration.py` hard-codes two backends with provider-specific
invocation: claude (`claude -p --output-format json --model …
--max-budget-usd …`, and now FU-35's `--append-system-prompt-file
--exclude-dynamic-system-prompt-sections`) and codex (`codex exec -
--json`, config-driven). Usage telemetry is already normalized to a
common `{input, output, cached}` shape — good prior art for an interface.

Proposed `Backend` protocol (one adapter per provider):

- `invoke(stdin_prompt, *, system_prompt=None, model, budget) -> (rc, text, usage)`
- `parse_usage(raw) -> {input, output, cached}` (already exists per backend)
- **capability flags** — the part the caching work makes essential:
  - `supports_system_prompt_cache: bool` — claude routes the FU-35 stable
    prefix through `--append-system-prompt-file`; codex/OpenAI and Gemini
    rely on automatic server-side prefix caching and take the **full**
    prompt on stdin. The runner asks the backend whether to split rather
    than branching on a backend string (which is what the current
    claude/codex `if` does).

This means the FU-35 split logic (`assemble_context --emit system|user`)
stays backend-agnostic in the assembler, and *the backend adapter decides*
whether to consume the split or the full prompt. Adding a third backend
(e.g., Gemini) becomes: implement the protocol + declare its caching
capability.

> **D-pkg-5 (decided):** prompt-caching strategy is a backend capability,
> not a runner-level `if backend == "claude"`. The assembler's `--emit`
> split (FU-35) is the provider-neutral substrate; adapters opt in.

**Deferral note:** if the protocol proves a big lift, ship Phase 2 with
the existing two backends still hard-branched, and land the abstraction in
Phase 3. But the capability-flag framing should be designed in from the
start so the split logic isn't re-tangled.

## 7. Control surface & orchestration

i2c already runs this architecture informally — codexbot drives it with
deterministic commands; Claude Code acts as the orchestrator. The work is
to formalize the split so it is clean, transport-agnostic, and not held
together by fragile text-parsing. There are **three independent pluggable
axes**:

```
        ┌──────────────── DRIVERS (compose primitives) ────────────────┐
        │  Human-via-CLI   TG bot   Discord bot   Policy   LLM Agent    │  ← axis 1 (surfaces) + axis 3 (orchestrator)
        └───────────────────────────────┬──────────────────────────────┘
                                         │  stable, DETERMINISTIC command API
        ┌────────────────────────────────▼─────────────────────────────┐
        │  i2c.control:  status · next_action · run_iteration ·          │
        │                audit · clear_boundary · decisions · logs       │
        └──────┬──────────────────┬──────────────────┬──────────────────┘
               │ deterministic     │ deterministic    │ invokes ONE worker
          state.py (.state/)   state_machine +    run_iteration → Backend   ← axis 2 (worker backend)
                                  assembler          (claude / codex / gemini)
```

### 7.1 Axis 1 — Transport / surface (TG, Discord, CLI, HTTP)

A surface is a thin adapter: parse a message → call an `i2c.control`
function → format the structured result for that medium. The crucial
design rule is that **`i2c.control` returns structured data, not formatted
prose.** Today codexbot shells out to the assembler and parses its *prose*
output — exactly the prose-vs-structure fragility i2c was built to
eliminate, leaking back in at the chat layer. Fix it once in the API and
every transport (Telegram, Discord, …) becomes a tiny shim shipped as an
optional extra (`pip install i2c[telegram]`, `i2c[discord]`).

### 7.2 Axis 2 — Worker backend

The per-action LLM executor inside `run_iteration` (claude/codex/gemini).
This is §6. It is a *different role* from the orchestrator and varies
independently.

### 7.3 Axis 3 — Orchestrator (optional)

The loop *driver*. The elegant part: **an orchestrator and a chat surface
are the same thing — a driver over the deterministic command API.** The
only difference is who pulls the levers:

- **Human** via chat/CLI — supervised, today's default.
- **Policy** — a deterministic rule set ("advance through phases until
  done; halt on escalation"). Essentially Phase 3.C's multi-iteration loop
  with a trivial policy. No LLM.
- **Agent** — an LLM that reads phase-summaries and decides
  advance-vs-terminate, handles escalations with judgment. This is what
  Claude Code does today. Optional, pluggable, provider-agnostic.

The orchestrator interface is small:
`decide(state_snapshot) -> {run_iteration | clear_boundary(advance|terminate) | escalate_to_human | stop}`,
and it calls the **same** `i2c.control` primitives a surface calls.

### 7.4 Why "deterministic only" on the surface

The surface is a trust boundary. A bot anyone can message must never do
open-ended reasoning on command — it dispatches only predefined, auditable
operations, and can be permission-gated. **LLM nondeterminism lives
strictly in two opt-in places: inside the worker (one bounded ACTION,
fully logged to `logs/loop/` + state via `state.py`) and inside the
optional orchestrator — never in the surface.** Even `/run` is
deterministic *as a command* ("perform exactly one ACTION"); the
nondeterminism is contained and audited downstream.

### 7.5 What it takes to do cleanly

1. **Extract `i2c.control`** — a stable Python command API over the
   deterministic layers, returning structured results (dataclasses), not
   prose. Biggest lift, highest value: kills codexbot's text-parsing,
   makes surfaces testable, and is the shared contract for both surfaces
   and orchestrators. The Phase-2 CLI (`i2c run`, `i2c status`) should be
   a thin caller of this same API, not a parallel implementation.
2. **Finish FU-34** — the `--section escalation` / `--section iteration`
   projections back `escalation()` / `logs()` and unblock the deferred
   codexbot commands (`/decisions`, `/escalation`, `/logs`, `/review`).
   Prerequisite for the *complete* command set.
3. **Define the orchestrator protocol** + ship references: a
   `HumanOrchestrator` (supervised default), a `PolicyOrchestrator`
   (deterministic multi-iteration), and document the `AgentOrchestrator`
   pattern (operators bring their own Claude Code / codex / gemini).
4. **Transport adapters** (TG, Discord) as thin optional extras over
   `i2c.control`.

This factoring also defuses the operator's recurring "I keep hitting
backend limits" pain twice over: the **worker backend** (axis 2) and the
**orchestrator** (axis 3) swap independently — e.g., a codex worker under
a Claude orchestrator, or a Gemini worker driven by a deterministic
policy.

## 8. Versioning & migration

- Semver the package; publish a CHANGELOG (the public-facing counterpart
  to the internal `FOLLOWUPS.md`).
- Add `schema_version` to `project.json`. A project declares which
  framework version its `.state/` targets.
- `i2c migrate` performs versioned, in-place `.state/` migrations. (FU-30
  already did a hand-rolled in-place migration on CC for the 7-state
  lifecycle — that pattern becomes a real, tested command for strangers.)

## 9. De-Meta-ification & licensing

- **License: MIT** (D-pkg-6) — the maximally permissive, maximally
  understood standard. Ship `LICENSE` at the repo root and an SPDX header
  convention if desired.
- **Telegram / Discord** become clean transport adapters over `i2c.control`
  (§7.1), shipped as optional extras — *not* Meta-coupled. What gets
  stripped is the Meta-specific plumbing: the `pirozhok` server,
  SSH-from-laptop operation, and codexbot's internal wiring.
- **FU-28 (laptop sandbox), pirozhok-over-SSH, internal paths, internal
  project names** → removed from distributed docs.
- **`FOLLOWUPS.md`** is the internal lab notebook; it does not ship. Public
  repo gets a clean README, CHANGELOG, CONTRIBUTING, and the MIT LICENSE.
- Production-incident anecdotes in `WORKER_SPEC.md` are e2e-vintage
  (FU-10) — review for anything Meta-identifying before publishing.

## 10. Phased rollout

- **Phase 1 — shareable demo:** clean public README + MIT LICENSE, strip
  Meta refs, `pyproject.toml` so `pip install git+…` works, an `examples/`
  end-to-end sample, documented prerequisites. Keeps the copy model; lets
  people *try* it and gives early feedback before the refactor.
- **Phase 2 — real package (the core of this memo):** §5.1 tool-surface
  switch to `i2c state`, importable package + entry points (§5.2),
  override resolution (§5.3), `i2c init` (§5.4), `i2c.toml` (§5.5),
  package-data schemas/instructions. **Design `i2c.control` (§7.5 #1) here**
  so the Phase-2 CLI is a thin caller of it, not a parallel path. Delivers
  true clone-and-go and eliminates the sync pain.
- **Phase 3 — polish:** backend abstraction (§6); control surface &
  orchestration (§7) — FU-34 projections, transport extras (TG/Discord),
  orchestrator protocol + reference drivers; CI matrix (Linux/macOS/Windows
  — FU-27 cross-platform, FU-18 test speed off the share); semver +
  `schema_version` + `i2c migrate` (§8); provider-auth docs.

## 11. Open questions

- **Q-pkg-1:** package/repo name and PyPI availability (`i2c` is likely
  taken on PyPI; needs a distinct distribution name).
- **Q-pkg-2:** do instructions/adapters ship *only* as package data, or
  always scaffold editable copies on `init`? (§5.3 proposes
  override-then-default; default-to-scaffold vs default-to-hidden is a UX
  call.)
- **Q-pkg-3:** ~~license choice~~ — **resolved: MIT** (D-pkg-6).
- **Q-pkg-4:** how much of the backend protocol to design up front vs
  defer (§6 deferral note).
- **Q-pkg-5:** transition plan for the existing internal consumers
  (clankercourts) — adopt the package, or stay on the copy model until
  the package stabilizes?
- **Q-pkg-6:** authentication / permission model for public chat surfaces —
  who may issue mutating commands (`run`, `clear_boundary`) vs read-only
  ones (`status`, `audit`)? Per-surface (TG admin list) or in `i2c.control`?
- **Q-pkg-7:** which transports and orchestrator references to ship first
  (e.g., Telegram + PolicyOrchestrator), and how much of the
  `AgentOrchestrator` to provide vs leave to operators.

## 12. Decisions

| # | Decision | Status |
|---|----------|--------|
| D-pkg-1 | Public open-source distribution (PyPI + public Git). | decided |
| D-pkg-2 | Installed-package dependency model; maximally self-contained. | decided |
| D-pkg-3 | Abstract the backend; claude + codex first; provider-specific behavior (caching) respected; deferrable. | decided (scope/timing open) |
| D-pkg-4 | Worker tool surface is the `i2c` console command, not `python3 tools/<x>.py`. | decided |
| D-pkg-5 | Prompt-caching is a backend capability flag over the assembler's `--emit` split, not a runner-level backend `if`. | decided |
| D-pkg-6 | License is **MIT**. | decided |
| D-pkg-7 | A deterministic command API (`i2c.control`) returns structured data (no prose parsing); no LLM logic lives in any surface. | decided |
| D-pkg-8 | An orchestrator is an optional, pluggable driver over `i2c.control`; reference impls: Human (default), Policy (deterministic), Agent (LLM). | decided |
| D-pkg-9 | Three independent pluggable axes: transport/surface, worker backend, orchestrator. Surfaces and orchestrators are both drivers over the same command API. | decided |
| D-pkg-10 | Transport adapters (Telegram, Discord, …) are thin optional extras over `i2c.control`, not Meta-coupled. | decided |

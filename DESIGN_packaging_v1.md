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
> Status: **partially implemented** (updated 2026-06-22). Phase 1 shipped
> 2026-06-21 (shareable demo) and Phase 2 shipped 2026-06-22 (the real
> package — §5 + `i2c.control`, plus §8 versioning/migration pulled forward);
> Phase 3 (backend abstraction §6, control/orchestration §7, CI) remains.
> This memo records the decisions taken in the packaging discussion and the
> design they imply; current status lives in `FOLLOWUPS.md` (internal) and
> `CHANGELOG.md` (public). Implementation is phased (see §10).
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
i2c assemble --action plan --mode supervised   # supervised first phase (assembles context, pauses for approval)
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
├── cli.py                  # `i2c` dispatcher: init, eject, status, next-action, phase-summary, decisions, clear-boundary, run, state, assemble, migrate
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
`logs/loop/` to `.gitignore`, and stamp `schema_version` (§8).

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

### 6.1 Two backend *kinds* (the real scoping fork)

Not every "backend" is the same shape, and this materially affects the
protocol:

- **Agentic-CLI backends** (today's `claude -p`, `codex exec`): the CLI
  *is* the agent — it runs tools itself (edits files, runs tests, calls
  `state.py`, commits) from a single piped prompt. i2c just hands it a
  prompt and reads the result.
- **Raw-API backends** (Gemini API, **OpenRouter**, plain
  chat-completions): text-in / text-out only. They do **not** run tools.
  To use one as a worker, i2c (or the adapter) must supply the agentic
  harness — parse tool calls, execute them, loop — which the CLIs give us
  for free.

So "add Gemini / OpenRouter" is two different sizes of job depending on
the path:
- Use a provider's **agentic CLI/SDK** where one exists (e.g., the Gemini
  CLI) → fits the existing agentic-CLI shape; small.
- Point an **existing agentic harness** (aider/opencode/etc.) at
  OpenRouter, or grow a **minimal i2c agentic loop** for raw APIs → larger,
  but unlocks *every* OpenRouter-hosted model through one adapter.

This is the substance of Q-pkg-4: the protocol must model the agentic
harness as a backend responsibility (or an i2c-provided shim), not assume
every backend is a self-driving CLI.

### 6.2 Candidate backends (roadmap)

- **Gemini** — high value; the operator keeps hitting per-backend limits
  and wants a third. Prefer the agentic Gemini CLI path if viable.
- **OpenRouter** — one adapter, *many* models (already used in the
  operator's Diplomat experiments). Highest leverage, but raw-API → needs
  the harness per §6.1.

> **Weaker-model hypothesis (worth validating):** i2c's governance is
> deterministic and structurally rigid — the model only ever performs one
> bounded ACTION with pre-assembled context and writes through a validated
> CLI. That rigidity may let *non-frontier* models drive real builds where
> they'd flounder in an open-ended agent loop. The governance constraint
> helps; the open risk is **agentic reliability** (e.g., FU-29: codex
> skipping the exit signal) and tool-call fidelity, not governance
> comprehension. Validating "how weak a model can i2c carry?" is a
> genuinely interesting experiment OpenRouter makes cheap to run.

## 7. Control surface & orchestration

i2c already runs this architecture informally — codexbot drives it with
deterministic commands; Claude Code acts as the orchestrator. The work is
to formalize the split so it is clean, transport-agnostic, and not held
fragile text-parsing. There are **three independent pluggable
axes** (below), plus one cross-cutting dimension — the **scope** at which a
driver runs, single-project or portfolio (§7.6):

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
  done; halt on escalation"). No LLM. (This is *not* the multi-step
  protocol: "Phase 3.C" multi-step is a worker-invocation budget *inside*
  one ACTION — see §5 / `--step-budget` — whereas a Policy driver wraps
  whole `run_iteration` calls and clears boundaries. A multi-iteration loop
  is the natural first Policy.)
- **Agent** — an LLM driver. **This already exists: it is the operator +
  an assistant (Claude Code / Devmate).** Crucially it does far more than
  advance-vs-terminate — it selects which project to work on, discusses
  scope, drafts/updates PROJECT.md / ARCHITECTURE.md / ARCH_*, dispatches
  loops, and monitors/debugs them. Most of that work is **supervised and
  interactive**, with autonomous-loop dispatch just one action among many.
  It is *not* reducible to a `decide()` and needs no protocol — it is the
  assistant exercising judgment over the same `i2c.control` primitives a
  surface calls.

The small fixed interface
`decide(state_snapshot) -> {run_iteration | clear_boundary(advance|terminate) | escalate_to_human | stop}`
describes the **Policy** driver only (deterministic, single-project). The
Human and Agent drivers are open-ended and call the same `i2c.control`
primitives directly.

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

### 7.6 Driver scope — single-project vs portfolio

Orthogonal to *who drives* (§7.3) is *how many projects* a driver drives:

- **Single-project** — a driver bound to one `.state/` (one project root).
  Today's surfaces and the `i2c` console are all single-project: they
  resolve one project and act on it.
- **Portfolio** — a driver operating across a parent folder of project
  subfolders: choosing which project to work on, doing supervised/
  interactive work on it, dispatching its loop, and monitoring/debugging
  loops across all of them. **This is not a new component — it is the Agent
  driver (§7.3) at portfolio scope: the operator + an assistant**, exactly
  as run today from Devmate or a Claude Code session at the workspace root.

Because the portfolio driver is just the assistant, the framework owes it
**structured primitives, not an orchestrator**. The deliverable is
portfolio-scope `i2c.control` views, so the meta-work runs on structured
data instead of `cd`-ing into each subfolder and parsing prose `status`
output (the §7.1 prose-vs-structure hazard, one level up):

- **Cross-project status / next-action** — discover every `.state/` under a
  root and render each project's phase, state, next action, and open
  escalations in one structured view (`i2c.control.status` mapped over N
  roots; shipped as `control.portfolio` / `i2c portfolio [--root PATH]`).
  Highest-value item: it answers "which project needs me?" for the common
  supervised case.
- **Cross-project monitor / debug** — surface *which* loop halted and why,
  across all projects. Depends on the FU-34 `escalation` / `logs`
  projections backing `control.escalation()` / `control.logs()`.

Dispatch needs nothing new (`cd <proj> && i2c run`); a portfolio runner is
optional sugar. Plan and doc-authoring stay the assistant's judgment work —
the framework's role there is context assembly, which already exists.

### 7.7 One projection layer — no prose/structure derivation split

Phase 2 added `i2c.control` as the structured command API (D-pkg-7) but did
**not** retire the assembler's operator-facing `--section` modes (`status`,
`phase-summary`, `devlog`). The two now derive the *same* projections from
`.state/` independently — the assembler renders them to prose, `control` to
dataclasses. Duplicated today, line-for-line in places: budget computation
(`_compute_budget` vs the inline branch in `render_status_project`), the
phase-step filter, the open-decision filter, phase-summary composition, and
devlog interpretation. This is exactly the prose-vs-structure dual-maintenance
hazard that §7.1 and D-pkg-7 exist to kill — reintroduced one level up, *inside
the framework itself*. i2c's whole thesis is one structured source of truth
with no parallel representation to keep in sync (cf. `archive/DESIGN_governance_v3.md`
§5, "no persistent rendered views"); the framework must hold itself to the rule
it enforces on consumers.

It is a transitional artifact, not a design: the assembler grew operator views
*because, pre-`control`, there was nowhere else to put them*; Phase 2 then stood
`control` up **beside** those views instead of **instead of** them.

**End-state — three concerns, one home each:**

- **Assembler → worker-prompt assembly only.** The byte-locked, cache-stable
  machinery (regions, conditional markers, the FU-35 `--emit` split). Its
  determinism constraint is *hard* and not shared by operator views, so it
  stays an isolated seam — its prompt sections (`PROJECT CONTEXT`, …) belong
  here and stay.
- **`control` → the single structured projection + command layer** over
  `.state/` (and, per FU-34, `logs/loop/`). One derivation; dataclasses out.
- **`cli` / surfaces → thin formatters** over `control` dataclasses. Prose
  lives at the surface, never in the core (D-pkg-7).

**The rule going forward:** every operator/surface view is a `control`
projection plus a surface formatter. **No new operator-facing `--section` modes
are added.** The assembler's existing operator `--section` modes (`status`,
`phase-summary`, `devlog`) are **deprecated** and removed once `control` + the
CLI formatters cover them (a deprecation note lands in `ARCH_assembler.md`;
removal tracked in FU-39). `--section module` / `architecture` are verbatim file
passthroughs, not derived views — they may stay or move to a `control` doc-read,
decided at removal time.

**Boundary to respect:** operator-view de-duplication must **not** perturb
worker-prompt bytes. The prompt renderers are golden-tested and cache-stable
(FU-35); they are *not* coupled to `control`. Shared leaf derivations may be
extracted across the two only where byte-identical prompt output is preserved —
and that sharing is optional. The goal is met when operator views have exactly
one derivation (in `control`) and the assembler no longer carries
operator-facing sections.

> **D-pkg-14 (decided):** `control` is the single structured projection /
> command layer over project state; operator and surface views are dataclasses
> formatted at the surface. The assembler is worker-prompt assembly only — its
> operator-facing `--section` modes are deprecated and removed once superseded.
> New operator views are never added as assembler sections.
>
> **D-pkg-15 (decided):** worker-prompt assembly stays isolated from the
> projection layer; operator-view de-duplication must not alter worker-prompt
> bytes (FU-35 cache stability + golden tests). Leaf derivations are shared
> across the two only where byte-identical prompt output is preserved.

## 8. Versioning & migration

> **Shipped in Phase 2 (2026-06-22), pulled forward from the Phase-3 plan
> below.** `schema_version` (optional; absent ⇒ legacy v0, `CURRENT=1`),
> `i2c migrate [--check|--dry-run]` with the real 0→1 migration (drop the
> retired `blocked` field, stamp the version), `i2c init` version stamping,
> and a `CHANGELOG.md` all landed. See `CHANGELOG.md` for per-version notes.

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
- **Phase 3 — final-form control surface + backends + polish.** Sequenced so
  every consumer is built on the layer it will permanently sit on — no
  transitional duplicates (the Phase-2 lesson: `control` was added *beside* the
  assembler's operator sections, not *instead of* them, creating the §7.7
  split). Control-surface track, in order:
  - **3a — single projection layer (§7.7, D-pkg-14):** consolidate the
    operator-facing derivations into `control`; make the `i2c` CLI (`status`,
    `phase-summary`, …) thin formatters over it; deprecate the assembler's
    operator `--section` modes (mark now, remove once the CLI formatters land).
    Foundation for everything below. **(Shipped 2026-06-25 / FU-39: assembler
    operator sections removed; `i2c devlog` added; worker prompts byte-identical,
    proven by `tests/test_prompt_golden.py`.)**
  - **3b — FU-34 `escalation()` / `logs()`** as `control` projections on the
    3a layer (dataclasses, *not* assembler sections). Built final-form.
    **(Shipped 2026-06-25: `control.escalation` / `logs` / `logs_transcript`
    + `i2c escalation` / `i2c logs`; index parsed from `summary.log`,
    transcripts on demand.)**
  - **3c — portfolio-scope views (§7.6):** `control.status` / `escalation`
    mapped over N roots (`i2c portfolio`).
    **(Shipped 2026-06-25: `control.discover_projects` + `control.portfolio`
    → `i2c portfolio [--root PATH]`; one `ProjectBrief` per project over the
    existing `status`/`escalation`/`next_action` projections, ordered
    escalations/boundaries first, per-project load errors captured.)**
  - **3d — transports + orchestration (§7):** TG/Discord extras and the
    orchestrator protocol + reference drivers (Human/Policy/Agent), all over
    `control`.
    **(Telegram shipped 2026-06-26: `i2c/surfaces/telegram_core.py` pure
    dispatch + `i2c/surfaces/telegram.py` PTB shell; `pip install i2c[telegram]`,
    `i2c serve telegram`; surface-enforced admin allowlist. Discord + the `/ask`
    Agent layer + orchestrator references remain.)**
  - **Backend abstraction (§6)** — independent parallel track (axis 2):
    Gemini (agentic-CLI path) then OpenRouter (raw-API + harness, §6.1).
  - **Polish:** CI matrix (Linux/macOS/Windows — FU-27 cross-platform, FU-18
    test speed off the share); provider-auth docs.

  (semver + `schema_version` + `i2c migrate` (§8) were pulled forward and
  shipped in Phase 2.)

**Sequencing principle (no crutches):** build the final-form layer before the
consumers that sit on it. A view is never added to a surface it will later move
off of — it lands as a `control` projection from the start. 3a precedes 3b–3d
precisely so we don't lay another duplicated brick.

## 11. Open questions

- **Q-pkg-1:** package/repo name and PyPI availability. **Leaning
  `idea2code`** (tentative). Deferrable to publish time with zero rework:
  Python lets the PyPI distribution name, import package, and CLI command
  differ, so keep the import package + console command as `i2c` and decide
  the public distribution/repo name at Phase-1 publish (check PyPI
  availability then).
- **Q-pkg-2:** ~~package-data only vs scaffold copies on `init`~~ —
  **resolved (D-pkg-11):** split by file type. Instructions ship as
  package-data with per-file override (`i2c eject`); adapters are
  scaffolded on `init` (they carry per-project content).
- **Q-pkg-3:** ~~license choice~~ — **resolved: MIT** (D-pkg-6).
- **Q-pkg-4:** ~~how much of the backend protocol to design up front vs
  defer~~ — **resolved (D-pkg-12):** defer the full protocol until the
  third backend (Gemini) is added; design against three real backends
  rather than two-plus-a-guess. Keep the capability-flag shape (D-pkg-5)
  now. Open sub-point: the protocol must model the agentic harness for
  raw-API backends (§6.1).
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
| D-pkg-11 | Instructions ship as package-data with per-file override (`i2c eject`); adapters are scaffolded into the project on `init`. | decided |
| D-pkg-12 | Defer the full backend protocol until the 3rd backend (Gemini) lands; design it against three real backends. Keep the capability-flag shape now. Protocol must model an agentic harness for raw-API backends (Gemini API / OpenRouter), not assume self-driving CLIs. | decided |
| D-pkg-13 | Roadmap backends: **Gemini** (prefer agentic CLI path) and **OpenRouter** (one adapter, many models; raw-API → needs harness). Validate the weaker-model hypothesis (§6.2) once a raw-API path exists. | decided (roadmap) |
| D-pkg-14 | `control` is the single structured projection/command layer; operator & surface views are dataclasses formatted at the surface. Assembler = worker-prompt assembly only; its operator `--section` modes (status, phase-summary, devlog) are deprecated and removed once superseded. No new operator views as assembler sections. | decided |
| D-pkg-15 | Worker-prompt assembly stays isolated from the projection layer; operator-view de-dup must not alter worker-prompt bytes (FU-35 cache stability + golden tests). Shared leaf derivations only where byte-identical prompt output is preserved. | decided |

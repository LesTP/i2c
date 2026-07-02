# i2c Followups — Design Notes and Tooling Gaps

Running list of items deferred or noted during build sessions. Lower-priority
than the rollout plan phases; revisited when triggers surface (real friction,
Phase 2 pilot feedback, or downstream work that needs the gap closed).

As of 2026-07-02 this file is also the **single ongoing tracker**: the **Active
Roadmap** section (below the cold-start) carries strategic tracks, cross-project
priorities, and the current recommendation (merged from the former Desktop
`i2c TODO.md`); the FU tables remain the fine-grained backlog.

Distinct from `FUTURE_waymark.md` (a roadmap for one specific deferred
initiative) — this is the catch-all log of "noticed during the build, doesn't
block the current deliverable, worth tracking."

ID scheme: `FU-N` (Follow-Up). Status: `open` / `accepted` (will do, scheduled
to a phase) / `partially closed` / `closed` / `wontfix`.

---

## Cold-start summary (next session entry point)

**Where we are (2026-07-02).** Foundation (data + prose + autonomous-loop),
**state lifecycle v1** (7-state enum: `plan`, `execute`, `review`, `close`,
`audit_boundary`, `audit_escalation`, `done`), and **packaging Phase 1–2**
(installable package, `i2c` console, `i2c init`/`eject`, `i2c.toml`,
`schema_version` + `i2c migrate`) are all shipped. **Packaging Phase 3 — the
control surface — is now complete:** `i2c.control` is the single structured
projection/command layer (3a / FU-39 removed the assembler's duplicate operator
sections; worker prompts proven byte-identical by golden snapshots), exposing the
full read surface (`status` / `phase-summary` / `decisions` / `devlog` /
`escalation` / `logs` / `portfolio`, all `--json`) plus the `clear-boundary`
action, a cross-project portfolio view (3c), and a Telegram bot (3d,
`pip install i2c[telegram]` → `i2c serve telegram`). The doc set was consolidated:
a single decisions index (`DECISIONS.md`), historical design memos moved to
`archive/`, and `WORKFLOW.md` / sub-READMEs de-duplicated. CC drove **Phases
2–14** autonomously across both backends (claude + codex) earlier in the project.
See **Recently shipped** below for per-item detail and **Active priorities** for
what's next.

**Since 2026-06-26 (added 2026-07-02).** Four things landed after the summary
above was written: **recovery v1** (`i2c diagnose` / `reconcile` — deterministic
workflow-drift detect-and-repair; README §Recovery, `archive/DESIGN_recovery_v1.md`);
the **telemetry sidecar** (`.state/telemetry.jsonl`, a runner-authored,
schema-validated per-iteration execution envelope — Increments 1–2, commits
`707aec8` / `636a192`; observational only, never control state); **FU-40** began
centralizing commits in the runner (CLOSE increment `9d39390`, 2026-07-01); and
**diplomat migrated to i2c** (2026-07-01) — now live at **phase 51**, driven by
the i2c bot with a per-action `[run.backends]` split (plan=claude, execute=codex,
review=claude, close=codex). The new dominant strategic thread is a
**model-benchmark initiative** (telemetry → a phase-level `tests` action as a
real oracle → benchmark + routing); it is tracked in the **Active Roadmap §7**
below, with full detail in `DESIGN_{telemetry,tests_action,benchmark}_v1.md`.

`FOLLOWUPS.md` is the rolling backlog + live spec for the remaining
FU-32 Δ5 work (deferred until template proves out further).

**Epistemic note (carried over from the original Phase 1 pilot debrief,
2026-06-06):** the original clankercourts Phase 1 ran inside the same
continuous session that built i2c itself — operator knew the framework's
contracts intimately and caught friction (FU-12, FU-19, FU-21, FU-22)
before it compounded. "Worker contract held" claims from that period
reflect session discipline, not framework enforcement. Subsequent CC
Phases 2–4 ran autonomously (separate sessions, no operator in the
loop mid-iteration) and validated the framework properly.

FU-32 below carries the live spec for the remaining autonomous-PLAN
readiness work (Δ5 + CC ARCH validation).

**Tooling now available:** Since packaging Phase 2 the canonical surface is
the `i2c` console — `i2c <subcommand>`, or `python -m i2c.cli <subcommand>`
if the scripts dir isn't on PATH; the worker write tool is `i2c state …`.
The `python tools/<x>.py` commands below are historical — code now lives at
`i2c/<x>.py`, reached via the console or `python -m i2c.<x>`.

- `python tools/state.py {append,append-record,update-record,append-gotcha} --from-file <path>` for
- Bare schema filenames (`steps.json`, `phases.json`, ...) auto-resolve to `.state/<name>` when CWD has `.state/` (FU-19 closed).
- `python tools/state_machine.py` outputs ACTION + NEXT (read-only).
- `python tools/invariants.py --action <name>` checks the post-action invariants from FU-22.
- `python tools/run_iteration.py [--backend claude|codex] [--model sonnet] [--max-budget-usd 5.00]` drives one cold-start worker invocation end-to-end. **Codex backend** is functional (Phase 3.D shipped). Per-iter `tokens_in / tokens_out / tokens_cached` appended to `summary.log` for both backends (FU-33 closed 2026-06-10).
- `i2c assemble --section architecture | module` for worker mid-step file context. **Operator views moved to the `i2c` CLI in Phase 3a (FU-39):** `i2c status`, `i2c phase-summary --phase N`, `i2c decisions [--phase N]`, `i2c devlog --phase N` (all `--json`-capable; control-backed).
- `python tools/assemble_context.py --step-budget N` controls whether `multi_step_only` subsections appear (default 1 strips; >1 keeps). Runner still hard-codes 1 — multi-iteration loop is Phase 3.C, not yet shipped.
- `python tools/assemble_context.py --action A --phase N --emit {full,system,user}` (FU-35) splits the prompt into a cache-stable prefix (`system` = WORKER CONTRACT + TOOL RULES) and a per-iteration body (`user` = PROJECT CONTEXT + ACTION CONTEXT + Output Contract); `full` (default) is byte-identical to before. The claude runner path routes `system` through `--append-system-prompt-file` for prompt-cache reuse; codex uses `full` + server-side prefix caching.
- **Codexbot Telegram surface for i2c projects** (`75ca84e`, 2026-06-09): `/start` renders assembler status, `/run N [to-review]` invokes the consumer-local shim, `/close` advances `phase=N+1 state=plan`, `/audit` renders phase-summary. Restart per `~/claude-code-workspace/projects/pirozhok/README.md`.

**Active track — open-sourcing (`DESIGN_packaging_v1.md`):** turning i2c
into a shareable, installable open-source project. **Phase 1 shipped
2026-06-21** — public README, MIT LICENSE (Mike Yeluashvili), all
shippable docs de-Meta'd (instructions, `ref/`, WORKER_SPEC, WORKFLOW),
a metadata-only `pyproject.toml`, and an `examples/` walkthrough.
Internal-only docs (`FOLLOWUPS.md`, `DESIGN_*`, `FUTURE_waymark.md`) stay
internal per the curated-export model (D-pkg-2). **Phase 2 shipped
2026-06-22** — the real package: `tools/` → an importable `i2c/` package
with framework assets (schemas, `WORKER_SPEC.md`, `instructions/`, adapters,
templates) shipped as package data; the `i2c.control` command API
(dataclasses, not strings; typed exceptions, not `sys.exit`); the `i2c`
console surface (`i2c/cli.py`); `i2c init` / `i2c eject` scaffolding;
`i2c.toml` run config; and **§8 `schema_version` + `i2c migrate`**
(versioned in-place `.state/` migrations). This eliminates the consumer
copy-and-sync model — consumers now `pip install` and carry only their own
`.state/` + docs.

**Phase 3 — control surface (shipped 2026-06-25/26).** The whole control-surface
track landed final-form on the single projection layer: FU-39 (3a, `control` is
the one structured view; the assembler's operator `--section` modes removed),
FU-34 (3b, `escalation`/`logs`), the portfolio view (3c, §7.6), and the Telegram
bot (3d, §7.1 / D-pkg-10). **Remaining in §7:** a Discord extra and the optional
`/ask` Agent layer + orchestrator-protocol references. The pluggable backend
protocol (FU-38 — Gemini / OpenRouter, §6) runs as an independent parallel track;
then the public distribution name + first release/tag (Q-pkg-1, at which point
CHANGELOG's `Unreleased` is cut to a release).

**Active priorities & tracks:** see the **Active Roadmap** section below (merged from the former Desktop `i2c TODO.md`) for current tracks, priorities, and the updated recommendation.

**Recently shipped (2026-06-26):**
- **3d — Telegram surface (`DESIGN_packaging_v1.md` §7.1, D-pkg-10).** A clean,
  i2c-native bot — *separate from the internal e2e codexbot*, which stays as-is
  serving e2e (its `LogReader` path is untouched). Structure: a pure
  `i2c/surfaces/telegram_core.py` `dispatch()` (over `i2c.control`, no telegram
  import, fully unit-tested) + a thin `i2c/surfaces/telegram.py` PTB wiring shell
  (lazy-imported). Commands (canonical list in README "Chat surface"): read = the `/audit` hub
  (summary | `phase N` | `decisions [N]` | `devlog [N]` | `escalation` | `logs [N]` | `logs iter N`) plus `/diagnose /portfolio /setdir /commands`; admin-gated mutating
  = `/run /batch /reconcile /endphase`. *(An earlier draft listed a flatter set — `/status /next /phasesummary /projects /use … /clearboundary` — the pre-implementation plan; the shipped bot uses the `/audit` hub and `/endphase`, the surface over control action `clear_boundary`.)* Auth is surface-enforced via an `[telegram].admins`
  allowlist (Q-pkg-6 answered: surface, not `control`); token from
  `I2C_TELEGRAM_TOKEN` (env only). Ships as `pip install i2c[telegram]`,
  run via `i2c serve telegram`. Reads run in-process; `/run`/`/batch` shell
  `i2c run` with the project as CWD (run_iteration resolves project from CWD) on
  a worker thread. Also extracted the operator-text renderers into `i2c/render.py`
  shared by the CLI and the bot (no new duplication). The deferred codexbot
  commands (`/escalation`, `/logs`, `/review`) are obsoleted by this bot for i2c
  projects. clankercourts moves here when it resumes (no codexbot stopgap needed).

**Recently shipped (2026-06-25):**
- **FU-39 — single projection layer (packaging Phase 3a).** Removed the assembler's operator-derived `--section` modes (`build_section_status` / `build_section_phase_summary` / `build_section_devlog` + their operator-only renderers); `SECTIONS` is now `(architecture, module)` (verbatim file passthroughs / worker mid-step providers only). Added `control.devlog()` + `i2c devlog`, completing CLI parity (`i2c status` / `phase-summary` / `decisions` / `devlog`, all `--json`). Operator views are now single-sourced in `i2c.control`, formatted at the CLI — the prose/structure duplication is gone (D-pkg-14/15, D-arch-13). **Worker-prompt bytes proven unchanged** by `tests/test_prompt_golden.py` (18 golden snapshots across action×backend×mode, generated pre-removal, green post-removal). ARCH_assembler §8 rewritten as the "operator views moved to control" note; docs (README/WORKFLOW/examples/templates) point at the CLI; smoke test step 9 uses `i2c status`. FU-34 (Phase 3b) is now unblocked. **codexbot must migrate** its prose-parsing of `i2c assemble --section …` to `i2c <cmd> --json` (lockstep with this removal).

**Recently shipped (2026-06-22):**
- **Packaging Phase 2 + §8** — see the *Active track* above. `tools/` moved
  to the importable `i2c/` package; `i2c.control` / `i2c` console / `init` /
  `eject` / `i2c.toml`. `schema_version` added to `project.json` (absent ⇒
  legacy v0; CURRENT=1) with `i2c migrate [--check|--dry-run]` (the 0→1
  migration drops the legacy `blocked` field and stamps the version;
  validate-before-stamp keeps a failed migration re-runnable). `CHANGELOG.md`
  (Keep-a-Changelog) added; build artifacts (`*.egg-info/`, `build/`, `dist/`)
  gitignored. 401 tests green; smoke test passes.

**Recently shipped (2026-06-21):**
- **Packaging Phase 1** — see the *Active track* above.
- **Pre-packaging hardening pass:** FU-17 closed (reject `--phase` on the
  `--section` modes that ignore it), FU-11 closed (a test that validates
  every inline `state.py` JSON example in `instructions/*.md` against its
  schema), and FU-37 cut the dead `STEP_BUDGET` env stub from
  `state_machine.py`.
- **FU-35** prompt-cache support — `assemble_context.py --emit
  {full,system,user}` splits the prompt at the WORKER CONTRACT + TOOL RULES
  / PROJECT + ACTION boundary; the runner routes the stable prefix through
  Claude Code's `--append-system-prompt-file` +
  `--exclude-dynamic-system-prompt-sections`; codex relies on server-side
  prefix caching. Measure via `tokens_cached` in `summary.log` on iter 2+
  of a phase.
- **FU-38** opened — add Gemini / OpenRouter backends (design in
  `DESIGN_packaging_v1.md` §6).

**Pending operational items:**
- **FU-29** — CODEX/CLAUDE adapter Output Contract patched in all four files; full closure waits for a `templates/` layer for adapters.

**Quick orientation commands** (from a project root that already has
`.state/`, with the package installed via `pip install -e .`):

```powershell
$env:PYTHONIOENCODING="utf-8"
i2c next-action                               # ACTION + NEXT (or: python -m i2c.cli next-action)
i2c status                                   # control-backed project snapshot (--json for structured)
i2c phase-summary --phase <N>                # operator boundary view
i2c migrate --check                          # schema-drift check (exit 1 if a migration is needed)
```

From the i2c repo itself (tests + end-to-end smoke):

```powershell
python -m unittest discover -s tests
python examples\smoke_test.py
```

To dry-run the runner (writes to `logs/loop/` but invokes a real `claude -p`):

```powershell
i2c run --backend claude --max-budget-usd 2.00
```

**Canonical references:**
- Build status: `README.md` table
- Assembler contract: `ARCH_assembler.md` (per-section spec; §8b for phase-summary)
- Architectural rationale: `archive/DESIGN_governance_v3.md`; state lifecycle: `archive/DESIGN_state_lifecycle_v1.md` (both historical — see `archive/README.md`); decisions index: `DECISIONS.md`
- ARCH-file authoring template: `ref/SPEC_architecture.md` + `ref/GUIDE_architecture.md`
- Workflow diagrams: `WORKFLOW.md`
- This file: the rolling backlog + live spec for FU-32 (progress log below)
- Pilot project: `p:\shared\clankercourts\` (first real consumer)

---

## Active Roadmap (tracks + priorities)

> Merged from the former Desktop `i2c TODO.md` (2026-07-02). This file is now the
> single ongoing tracker: strategic tracks + priorities live here, the detailed
> per-item backlog in the FU tables below.

### 1. Near-term, small
- **External launch decision (Q-pkg-1 / D-pkg-1)** — pick the public name, decide PyPI + public-git. This is "v1, externally facing," gated on the open core tracks below — not mechanical. (The internal 0.2.0 cut is already done.)

### 2. Parallel medium tracks
- **Backend abstraction (FU-38)** — *see `DESIGN_backend_v1.md`.* **(a) Gemini** agentic-CLI backend — **spec'd, shelved** (run Gemini/Gemma as OpenRouter **model-ids** for the benchmark; native CLI only later as a free/subscription **prod** cost-opt). **(b) OpenRouter** — **Option C (reuse codex) is BLOCKED on codex 0.124** (live smoke 2026-06-30): codex dropped `wire_api="chat"`, now requires `"responses"` (OpenAI Responses API); OpenRouter is Chat-Completions-native and hung on the responses wire. **Re-ranked:** **B** = a chat-completions agent CLI (aider/opencode/OpenHands/…) → OpenRouter, used as a §1.1 CLI backend (now preferred); **A** = in-house harness over toolkit's `OpenRouterProvider` (diplomat-proven, but bigger + toolkit dep / `i2c[openrouter]`). Salvage C only via OpenRouter adding a Responses endpoint, a Responses↔Chat proxy (LiteLLM), or an older codex (conflicts with the 0.124 bot).
- **Multi-iteration loop (Phase 3.C / FU-32 #2)** — wrap `run_iteration` with `--step-budget > 1`. *Also the first real Policy orchestrator driver* (see §4). Gated on first measuring FU-35 cache hits (caching may already cover the token-savings motive).

### 3. Fleet migration
- **toolkit** — done (migrated).
- **diplomat** — **migrated & live (2026-07-01)**: fully on i2c at **phase 51 / plan**, driven autonomously via the i2c bot with a `[run.backends]` split (plan=claude, execute=codex, review=claude, close=codex); Stage 0+1 import committed `1c5014c` (49 phases + snapshot history serialized into `.state/`). Residual: normalize the 13 flagged decision statuses; converter FU — handle `Closed (…)` / `Superseded by` / `| Priority:` status suffixes.
- **phosphene** — blocked on **Q-mig-7 / D-mig-4** (integer phase-id schema vs `MVP.4d`); needs a schema/renumber decision.
- **codexbot + others** — audit not yet done.
- **Ratify D-mig-2..7** — paper-only (toolkit + diplomat are the evidence).

### 4. Orchestrator + remaining surfaces (§7) — collapsed
- **Human** driver = exists (CLI/Telegram). **Policy** driver = `/batch` shipped; the **multi-iteration loop is the next Policy** (so this overlaps §2, not a separate track). **Agent** driver = already exists as operator + assistant; "needs no protocol."
- Genuinely-optional remainders: a `/ask` in-product LLM agent surface, and a **Discord** extra.

### 5. Larger net-new initiatives (deferred, well-specified)
- **Recovery `fix` agent (`FUTURE_recovery.md`)** — code-class sibling to reconcile: `diagnose`(code) → `.state/diagnoses.json` + `fix.md` worker → human-gated repair; later self-healing. Held until recovery v1 is exercised in anger.
- **Waymark VS Code extension (`FUTURE_waymark.md`)** — **deferred indefinitely** (2026-07-01): the read-only web dashboard (below) subsumes its Scope A; a VS Code plugin re-enters only as the future *control* surface (Scope B), if ever.
- **Portfolio dashboard — SPEC'D (`DESIGN_dashboard_v1.md`, committed `21b339c`).** Read-only, browser-viewable view over `i2c.control` + `.state/` + telemetry + `doctor`; panels: portfolio / project drill / telemetry / health / topology (the "what runs where" conceptual aid). **Read = web (portable); control = local/trusted** (CLI/Telegram now, VS Code plugin later). Stages: **v0 static HTML generator** (no server/auth) → v1 local read-only server (`i2c[web]`) → v2 remote (WireGuard/tunnel + auth) → v3 control (separate). Future-proofed now: no-secrets allowlist + single auth choke point.
- **Explicit brownfield path** — Reverse Architecture → CODEBASE.md → scoped discovery, plus the brownfield-archaeology skill. i2c's README is greenfield-focused.

### 6. Rolling backlog (small FUs, opportunistic)
FU-16 (naive Available-Modules fallback) · **FU-40** (centralize commits in the runner; resolves FU-8's unenforced `phase.step:` format — load-bearing for recovery's `commit_exists_step_pending`; **CLOSE increment shipped `9d39390`**, EXECUTE/REVIEW migration pending) · FU-29 (adapter Output Contract → `templates/` layer) · FU-20 (Devmate project-level commands) · FU-18 (slow tests on share) · FU-9 (Refine devlog iteration field) · FU-10 (refresh WORKER_SPEC anecdotes) · FU-36 (reason-first prose) · FU-37 (rolling dead-surface audit) · FU-15 / FU-4 / FU-3 / FU-14 (low-pri ergonomics). Full rows in the FU tables below.
- ~~FU-32 Δ5~~ → **deprioritized.** No occurrence; current soft-handling beats the spec; only worth it for externally/migration-authored ARCHes, and then scoped to `## Phasing` only.

### 7. Model-benchmark initiative (telemetry · test isolation · benchmark)
Strategic thread: **find the cheapest model that still succeeds per kind of step, and route to it.** Three sub-tracks, in dependency order.

- **Telemetry sidecar — SHIPPED.** `.state/telemetry.jsonl`: runner-authored, schema-validated, git-tracked execution envelope (model, tokens, cost, timing, git deltas, prompt hash, phase meta, drift, outcome) per autonomous iteration. Observational only; never control state; never fatal. *Increment 1* (data plane: schema, `telemetry.py`, runner capture, scaffold seed, tests) committed `707aec8`; *Increment 2* (cost/tier from bundled `pricing.json` + `[telemetry.pricing]`; opt-in `tests_pass` oracle via `[telemetry].test_cmd`) committed `636a192` (548 green). *Deferred* (schema already nullable, additive later): structured `review_findings`, exact cache-aware cost, `tool_calls`, codex model capture. Spec: `DESIGN_telemetry_v1.md`.
- **Test isolation — SPEC'D (`DESIGN_tests_action_v1.md`).** New regime-conditional, phase-level `tests` action (`plan → tests → execute → review → close`, Build only): freezes a contract-derived acceptance suite **before** EXECUTE so the implementation is graded against tests it didn't author — turning `tests_pass` from self-graded into a **real oracle** (the linchpin the whole benchmark rests on). Surface enumerated + code-verified in §8 of the doc (state enum, `state_machine` `VALID_STATES`+`decide`, `instructions/tests.md`, assembler `ACTIONS`+recipe, `config._RUN_ACTIONS`, WORKER_SPEC, goldens, no-op version bump; `run_iteration` needs no change). Open: **D-tests-1** ordering (recommended `plan→tests`, not literal `tests→plan` — PLAN creates the phase record/regime), suite identification (path convention); EXECUTE must not edit the frozen suite (integrity rule).
- **Benchmark + routing — SPEC'D (`DESIGN_benchmark_v1.md`).** Measure success vs model tier per `(action × regime × size)` bucket; find the saturation knee. **Substrates:** clankercourts = proven hermetic replay substrate (689/692 offline; 62 commit-linked EXECUTE steps); diplomat = forward data firehose post-migration; phosphene/diplomat prose = labeling only (not replay). **Next:** replay harness on clankercourts (run on **pirozhok** — cross-mount path bug on the laptop), e2e prose normalizer (labeling), **routing v0** (top tier for PLAN/REVIEW/TESTS by blast radius; cheap-first-with-escalation for EXECUTE/CLOSE). **Deps:** model panel rides FU-38 (OpenRouter Option C blocked, see §2). Oracle building blocks = toolkit `prompt_regression` + `edit_classifier`. **Clean-room rule:** for benchmark-generator projects the operator runs the loops; the assistant authors only spec/arch (`DESIGN_benchmark` §7.3).

### Recommendation (updated 2026-07-02)
1. **Benchmark thread (§7)** — now the highest-leverage line (diplomat migration, previously #1, is done). Land **test isolation** (the `tests` action) next — the oracle linchpin — then the replay harness on clankercourts and routing v0. Diplomat is now the forward telemetry firehose; the model panel rides FU-38.
2. Otherwise pick one big track by what's hurting: **backends** (FU-38; also unblocks the benchmark panel) if rate limits bite, or the **multi-iteration loop** (after a cache-hit check; doubles as the first Policy orchestrator).
3. Hold `fix`, Waymark, Discord/`/ask`, and the external launch until a migration or two has shaken out recovery v1 and the core tracks.

---

## Tooling — state.py CLI gaps

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-2 | No subcommand to append a new step mid-phase | partially closed | `state.py append-record steps.json '{...}'` exists (covers PLAN-time and in principle mid-phase). The original framing was specifically about EXECUTE-time step creation; `instructions/execute.md` still funnels deferred work through devlog `Deferred:` flags so PLAN owns step authoring. If a real workflow demands runtime step append from EXECUTE, lift the restriction in execute.md prose - the CLI now supports it. | Phase 2 pilot shows execute-time step append is wanted; loosen the prose. |
| FU-3 | `state.py set` only handles JSON object files, not arrays | partially closed | `update-record` (added for review/close authoring) covers single-record updates on array files via `--match KEY=VALUE`. Generic-set-on-array still isn't supported (e.g., updating fields on N records at once). | A real workflow needs bulk update across multiple records (rare). Until then, `update-record` covers the gap that drove FU-3 in practice. |
| FU-4 | No named lifecycle-transition op (e.g., `state.py escalate --reason "..."`) | open (low priority) | Post-FU-30 the `blocked` field is gone; lifecycle transitions use `state.py set project.json state=<enum>`. The dense `set` syntax works for autonomous and supervised use alike. A named op like `state.py escalate --reason "..."` (or `state.py raise-gate`) could log a structured reason and read more naturally, but adds API surface for little gain. | Revisit if supervised UI grows a need for richer transition flows. |
| FU-14 | No read-side query helper in `state.py` (e.g., `state.py query devlog.jsonl --where 'contracts != []'`) | accepted (deferred) | The assembler (Phase 1.3) exposes pre-formatted views: `--section devlog --phase N` gives a bulleted phase tail, `--section status` an orientation snapshot. Ad-hoc queries still fall back to `jq`. Per ARCH §2 / §10, the assembler intentionally does not absorb general-purpose queries. | Phase 2 pilot reveals a repeated query pattern worth absorbing into `--section`. |
| FU-19 | Instruction examples use bare filenames (`phases.json`) but `state.py` is CWD-relative — works only if CWD is `.state/` | **closed** (Phase 3.A) | See resolution note below. Bare filename auto-resolution shipped via `resolve_state_path()` in `state.py`; instruction examples now work as written without `.state/` prefixes. |
| FU-21 | `state.py {append,append-gotcha,append-record,update-record} --from-file <path>` for multi-line / `$`-laden payloads | **closed** (Phase 3.A) | See resolution note below. `--from-file` shipped across all four payload-bearing subcommands; mutually exclusive with the inline positional. Closes FU-12 in practice once operators adopt the flag. |

## Tooling — assembler (`assemble_context.py`)

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-7 | `exit_signal.schema.json` is permissive (`additionalProperties: true`) | **closed** (2026-06-12) | See resolution note below. Trimmed to 2-line block (`exit_code` + `reason`) on the principle that the structured state in `.state/project.json` is the canonical source of truth; the signal carries only the worker's verdict bit and a human-readable summary not derivable from state. Schema strict (`additionalProperties: false`, `exit_code` enum `[0, 2]`). |
| FU-15 | `Module Contract` section is hard-required when `phases.json[current].module` is set | open (mitigated by discipline) | Per ARCH §11.1 / §4.1, the assembler exits 1 if a phase declares a `module` field but no `ARCH_<module>.md` file exists. This is strict by spec — useful for catching missing contracts early — and the FU-32 Δ4 ARCH-authoring discipline (per `ref/SPEC_architecture.md`) now codifies pre-authoring as the standard: every module's ARCH is written during architecture-phase work *before* any PLAN runs on that phase. **Original CC pilot (2026-06-05)** workaround (pre-author `ARCH_resolver.md` at bootstrap) is now the documented norm rather than a workaround. | Only revisit if a future workflow genuinely needs to plan against an unwritten ARCH (e.g., research/spike phases). For now the strictness is correct. |
| FU-16 | Available Modules ARCHITECTURE.md fallback is naive | open | When the adapter's `## Available Modules` section is placeholder-only, the assembler grabs `## Implementation Sequence` from `ARCHITECTURE.md` verbatim and surfaces its body. If projects use richer Implementation Sequence tables (extra columns, longer prose), the rendered Available Modules section will be noisy. | Phase 2 / 3 pilots show real-world Implementation Sequence tables overflow the section. Tighten the fallback to extract only module names, or document a project convention for the fallback shape. |
| FU-17 | `--phase` accepted (but ignored) with `--section status` | **closed** (2026-06-21) | See resolution note below. `_validate_args` now rejects `--phase` with `--section {status,architecture,module}` (the sections that don't consume it); ARCH §11.3 documents it; tests added. |
| FU-18 | Assembler tests slow on Windows network share | open | `tests/test_assemble_context.py` runs in ~60s on `\\192.168.0.50\shared\...`. Primary cost: `TempProject(with_framework=True)` copies the full `instructions/` directory and WORKER_SPEC + both adapters per test invocation. | If iteration cost becomes painful, refactor `TempProject` to copy only what each test class needs (most renderer tests don't read instructions), or cache the framework copy per pytest session. Not a correctness issue. |
| FU-23 | Assembler `--section status` omits `Budget:` line when `budget_type` is set but no counter populated | **closed** (2026-06-25, obviated by FU-39) | `--section status` was removed in Phase 3a; the operator snapshot is now `i2c status`, whose budget rendering lives in `cli._fmt_budget` (renders whatever `control.status().budget` provides). The original cosmetic gap no longer has a surface. |
| FU-34 | `escalation()` / `logs()` projections for surface consumers | **closed** (2026-06-25, Phase 3b) | Landed as **`control` dataclass projections** (not assembler sections): `escalation(phase=None) -> EscalationView` (the `audit_escalation` flag + last `escalate`/`blocked` devlog entry + up to 3 preceding in-phase entries + phase-tagged open decisions — pure `.state/`, mirrors `phase_summary`); `logs(limit=10) -> list[IterationLog]` (parses `logs/loop/summary.log` via a regex matching `run_iteration.write_summary_line`) + `logs_transcript(iter=N)` (attaches `iteration_NNN.txt` on demand; `NotFoundError` for an unknown iter). CLI parity added: `i2c escalation [--phase N]`, `i2c logs [--iter N] [--limit N]`, both `--json`. Tests in `tests/test_control.py` (`TestEscalation`, `TestLogs`) + `tests/test_cli.py` (`TestLogsAndEscalationCli`). Replaces the old `--section escalation`/`--section iteration` plan (which predated the control architecture and would have deepened the §7.7 duplication). | Unblocks the deferred codexbot commands (`/escalation`, `/logs`, `/review` → thin callers of `control`) and the portfolio cross-project monitor (§7.6, `control.escalation` over N roots). |
| FU-39 | Single projection layer — de-duplicate operator views between assembler and `control` | **closed** (2026-06-25, Phase 3a) | Phase 2 added `i2c.control` (structured) but left the assembler's operator-facing `--section` modes (`status`, `phase-summary`, `devlog`) in place, so the same `.state/` projections were derived twice. **Shipped:** removed those three section builders + their operator-only renderers from `assemble_context.py` (`SECTIONS` is now `(architecture, module)`); added `control.devlog()` + `i2c devlog` for CLI parity; operator views are now single-sourced in `i2c.control`, formatted by the `i2c` CLI (`status`/`phase-summary`/`decisions`/`devlog`, all `--json`). Worker-prompt bytes proven unchanged by `tests/test_prompt_golden.py` (18 golden snapshots, generated pre-removal). ARCH_assembler §8 → "operator views moved to control" note + D-arch-13. Shared leaf renderers (Gotchas / Current Phase Steps / Recent Activity) and the byte-locked worker-prompt path were left untouched (D-pkg-15). | Foundation for Phase 3; FU-34 (Phase 3b) now unblocked. Follow-up: codexbot migrates prose-parsing → `i2c <cmd> --json`. |

## Tooling — runner

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-22 | Runner post-close invariant check - assert `blocked == true` + current phase `status: complete` after every CLOSE | **closed** (Phase 3.A) | See resolution note below. Shipped as `tools/invariants.py` (`check_post_action(root, action)`); the single-iteration runner calls it after every CLOSE dispatch and halts-and-surfaces on failure. Reusable from supervised tooling too. |
| FU-33 | Runner doesn't surface token/quota counts in `summary.log` | **closed** (2026-06-10) | See resolution note below. Both backends emit token telemetry in their JSON output; runner now extracts and appends `tokens_in=N tokens_out=M tokens_cached=K` to each summary line. |
| FU-35 | Prompt-cache support for the stable prompt prefix | **closed** (2026-06-21) | See resolution note below. The original `cache_control`-marker framing was unreachable — the runner pipes plaintext to the `claude -p` / `codex exec` CLIs, and markers are a raw-API construct. Shipped instead as a system/user prompt split. |
| FU-32 | PLAN action not yet autonomous-capable; needs five framework deltas + ARCH-file discipline | **partially closed** (in progress; see progress log below) | After CC Phase 4 EXECUTE shipped supervised (commit `97e9ea4`), the meta-question surfaced: i2c's autonomous loop runs EXECUTE/REVIEW/CLOSE cleanly, but PLAN's step-breakdown step still requires human authoring because ARCH files aren't constrained enough to drive mechanical step decomposition. e2e solves this via a two-step workflow (pre-arch design separately, autonomous batch implementation); i2c lacks the ARCH-authoring discipline and the safety-net escalation triggers that make autonomous PLAN safe. Five deltas identified — see the progress log below for current state and the Δ5 spec. | Continue with CC Phase 5+ ARCH authoring against the new template; Δ5 follows once the template is validated. |
| FU-40 | Centralize commits in the deterministic runner (worker stops running git) | open (started — CLOSE increment shipped 2026-07-01, `9d39390`) | Commits are model-owned today: EXECUTE commits code (`phase.step:`), CLOSE (`close.md` step 10) commits docs + `.state/`; the runner and `state_machine.py` never committed (`state_machine` is read-only by design, so the committer is the runner). **Increment 1 shipped:** the runner commits `.state/`+telemetry after a successful CLOSE — only a post-worker committer can capture the close tail (close devlog / audit_boundary / the runner-authored telemetry row all land *after* the worker's own commit). **Direction:** move *all* commits to the runner — the worker edits files + writes `.state/` via `i2c state`; the runner commits (code as `N.M:` using the devlog `summary` as the body; `.state/`+telemetry at the boundary). This **resolves FU-8** (the runner enforces the `phase.step:` format that recovery's `commit_exists_step_pending` depends on) and removes worker-git hazards (interactive-hang, wrong scope, forgotten commits). Commits are scoped (`git commit -- .state`) so operator WIP is untouched. | Migrate EXECUTE/REVIEW deliberately after the CLOSE increment proves out; requires rewriting `execute.md`/`close.md` to drop worker git + a golden regen. |

### FU-32 progress log

This entry carries both the historical record and the live spec for any
remaining deltas. When the last delta (Δ5) lands, FU-32 flips to fully
closed and the live-spec subsection is dropped.

**Decisions closed (Q1–Q5 from the original plan):**

- **Q1 — template placement.** `i2c/ref/` (operator/assistant reference, not assembler input). 2026-06-09.
- **Q2 — Δ5 escalation names missing section.** Yes — devlog message names which Required section is missing. To be implemented when Δ5 lands. 2026-06-09.
- **Q3 — decisions.json phase field optional or required.** Optional (existing records lack it; back-fill via `state.py update-record decisions.json --match id=D-N phase=K`). 2026-06-09.
- **Q4 — phase-end review checklist.** Defer. Insufficient autonomous phase-boundary review experience to write a checklist that holds up. After 2–3 autonomous phases under the new template, what gets missed becomes empirical. Tracked as a potential Δ6. 2026-06-09.
- **Q5 — what counts as a "module".** Don't constrain. Template supports per-module ARCH (default), combined-spec single-file (for projects where boundaries aren't real), MVP/full split (phosphene pattern, for staged delivery). 2026-06-09.

**Path picked:** Path A (CC-first authoring → codify framework deltas from lessons). Inverse of the original plan's Path B preference; landed because the ARCH template needs validation in real authoring before its details lock. 2026-06-09.

**Deltas:**

- **Δ1 — optional `phase: integer` field on `decisions.schema.json`.** ✓ Shipped 2026-06-09. Schema accepts the field; `state.py update-record` validates correctly; back-filled on CC's D-18/D-19/D-20 (Phase 4 decisions). 4 schema tests + integration with `--section phase-summary`. ~10 LOC + 4 tests.
- **Δ2 — `plan.md` escalation triggers enumeration.** ✓ Shipped 2026-06-09. Added step 2.5 to `instructions/plan.md`: 5 project-general triggers (source-vs-ARCH drift, multi-regime scope, cross-module breakage at plan time, step-shape ambiguity, dep-probe contract mismatch) in a table with detect-when / reason-string / resolution columns; plus module-specific triggers reference (pulls from ARCH's `## Escalation Triggers` section per Δ4 template); plus how-to-escalate snippet. Synced to CC. Verified via the `test_plan_autonomous_smoke` assembler test (asserts all 5 trigger names travel into the worker prompt). ~58 lines of doc; no code.
- **Δ3 — `--section decisions --phase N`.** ✓ Obviated 2026-06-09 by the broader `--section phase-summary --phase N` (same filter + steps + devlog + open items + header). Δ3 dropped as a standalone deliverable.
- **Δ4 — ARCH template port from e2e + augment.** ✓ Authored 2026-06-09 at `i2c/ref/SPEC_architecture.md` + `i2c/ref/GUIDE_architecture.md`. **v2 pass shipped 2026-06-09** — restructured around Pattern A (per-module ARCH files) / Pattern B (single-document ARCHITECTURE.md) collapse after reviewing operator's prior projects (lyonel, noise-machine, PoP_port) showed 0/3 used the per-module shape that v1 documented as the default. Pattern B added with optional Layer Contracts sub-section (PoP_port exemplar) and worked-example pointers covering its expressive range — flat (lyonel), multi-phase (noise-machine), layered (PoP_port). Required / Recommended / Optional taxonomy preserved within each pattern. Original 4-pattern sketch (Combined-Spec-small, Combined-Spec-multi-phase, Single-doc-with-inline-contracts, Per-module) collapsed to 2 because the first three are operator-choice variations within Pattern B, not separate authoring disciplines. Escalation Triggers re-classified from "0/11 organic" (v1) to "1/12 organic" after PoP_port's Layer 1 `**Escalation triggers:**` was added to the sample — the pattern is rare-but-real, not novel. SPEC: 480 lines; GUIDE: 749 lines. **Validated (Pattern A), 2026-06:** clankercourts authored 13 Pattern-A ARCHes under this template and ran Phases 2–14 autonomously against them (`.state/phases.json`: ids 1–14 `complete`). **Pattern B still unvalidated** — no Pattern-B consumer built yet (candidates: the diplomat / phosphene migrations).
- **Δ5 — PLAN precondition check on ARCH completeness.** **Unblocked** (Δ4 validated, above) but **low-priority — no recorded occurrence.** Every CC ARCH was pre-authored with the Required sections, so autonomous PLAN (Phases 2–14) never hit a missing-section ARCH — the case was prevented by authoring discipline (FU-15's "mitigated by discipline" gap), and FU-15 already hard-errors on a missing ARCH *file*. Stronger evidence against the original spec: `ARCH_resolver` / `ARCH_validator` ship **without** `## Escalation Triggers`, yet their phases (2/3/4) ran autonomously to completion under the current soft-handling (`plan.md`: "continue planning but note the gap"). The original spec below (escalate if `## Phasing` *or* `## Escalation Triggers` is missing) would therefore have **false-halted real, successful phases**. **If ever built:** scope the hard check to `## Phasing` only (the part load-bearing for step decomposition) and leave `## Escalation Triggers` soft. **Trigger:** value appears only when ARCHes are authored *without* the operator's discipline — external users, or migrating projects (diplomat/phosphene) whose ARCHes predate the template. **Original spec (kept for reference):** PLAN reads the assembled `Module Contract` section; if it lacks the required section(s), worker writes a devlog entry naming the missing section (per Q2) and sets `state=audit_escalation` with reason `"ARCH lacks autonomous-PLAN-ready sections — needs collaborative authoring session per ref/SPEC_architecture.md"`. ~15 lines of doc, or ~10 LOC at the assembler.

**Adjacent work shipped this session:**

- **`--section phase-summary --phase N` on the assembler.** Operator's `state=audit_boundary` view: header + steps + decisions-added-this-phase (Δ1-dependent filter) + phase devlog + open items. Distinct from `--section status` (project-wide, current-state) and `--section devlog` (just the devlog tail). Spec in `ARCH_assembler.md` §8b. ~80 LOC + 10 tests. Validated against CC Phase 4 end-to-end (3 phase-tagged decisions surfaced cleanly; 17 untagged decisions properly noted via back-fill footer; full step+devlog narrative reads in one screen-and-a-half).

### Invocation guidance: running the loop from an i2c-consumer project

> **Superseded by packaging Phase 2 (2026-06-22).** Consumers now
> `pip install` i2c and run the `i2c` console (`i2c run …`) from their own
> project root; the framework resolves from the installed package, not a
> sibling `../i2c/tools/` checkout. The copy-and-sync / preflight-diff
> guidance below is retained for historical context and the pinned-snapshot
> scenario only — its `tools/<x>.py` paths are now `i2c/<x>.py` package
> modules.

Once `tools/run_iteration.py` ships (Phase 3.A), the canonical invocation from
a consumer project (e.g. clankercourts) is:

```powershell
cd /path/to/your-consumer-project
python3 ../i2c/tools/run_iteration.py --backend claude --model sonnet --max-budget-usd 5.00
```

**Why run from i2c upstream rather than copy the runner into the consumer.**
The runner imports `assemble_context`, `state_machine`, `invariants`, and
`validate` as Python siblings. Python adds the script's directory
(`i2c/tools/`) to `sys.path[0]`, so all framework code resolves to i2c
upstream. The consumer-local `tools/` is only invoked when the **worker's
procedure text** tells it to (`python3 tools/state.py ...`) — at which point
CWD is the consumer root (set explicitly via `subprocess.run(..., cwd=root)`),
so the consumer's local `state.py` runs.

This gives a clean version split:

- **Framework pipeline** (runner, state_machine, assembler, invariants) → i2c
  upstream. One source of truth, hermetic upgrades.
- **Worker tool surface** (`tools/state.py` invoked by the worker for state
  writes) → consumer-local, **but must be ABI-compatible with the procedure
  text**. ABI here means both the subcommand set *and* the argument-form
  features the procedures assume (FU-19 bare-filename auto-resolve;
  FU-21 `--from-file` payload path; future flag additions). The
  consumer-local copy must move in lockstep with i2c upstream — sync it
  every time you sync `instructions/*.md`. There is no "may lag" mode that
  is actually safe; pre-FU-19 state.py fails every worker write because
  the procedure examples write bare filenames.
- **Procedure prose** (`instructions/*.md`) → consumer-local. The assembler
  reads whatever the consumer has on disk and embeds it in the prompt.
- **Project state + contracts** (`.state/`, `PROJECT.md`, `ARCH_*.md`) →
  consumer-local, as expected.

**Preflight diff — before each consumer's first autonomous run, and after
each i2c upstream pull.** Compare these files between the consumer and i2c
upstream:

PowerShell:

```powershell
cd /path/to/your-consumer-project
foreach($f in @(
  'instructions/plan.md','instructions/execute.md',
  'instructions/review.md','instructions/close.md',
  'tools/state.py','tools/assemble_context.py','tools/validate.py'
)) {
  $cc = "$f"; $i2 = "../i2c/$f"
  if ((Test-Path $cc) -and (Test-Path $i2)) {
    $h1 = (Get-FileHash $cc -Algorithm SHA256).Hash
    $h2 = (Get-FileHash $i2 -Algorithm SHA256).Hash
    if ($h1 -eq $h2) { Write-Host "MATCH    $f" }
    else { Write-Host "DIFFER   $f  delta=$((Get-Item $i2).Length - (Get-Item $cc).Length)" }
  }
}
```

bash:

```bash
cd /path/to/your-consumer-project
for f in \
  instructions/plan.md instructions/execute.md \
  instructions/review.md instructions/close.md \
  tools/state.py tools/assemble_context.py tools/validate.py
do
  if [ -f "$f" ] && [ -f "../i2c/$f" ]; then
    if cmp -s "$f" "../i2c/$f"; then echo "MATCH    $f"
    else
      d=$(( $(stat -c%s "../i2c/$f") - $(stat -c%s "$f") ))
      echo "DIFFER   $f  delta=$d"
    fi
  fi
done
```

Acceptance criteria for invoking from i2c upstream:

| File | Must match? | If divergent |
|---|---|---|
| `instructions/*.md` | **Yes** | Worker reads stale procedure text the assembler (newer) and invariants (newer) may reject or work against. Sync into the consumer with an explicit commit before the autonomous run. |
| `tools/validate.py` | **Yes** | Schema validation must agree across the pipeline. Sync. |
| `tools/state.py` | **Yes** (procedures depend on FU-19 / FU-21 features) | Pre-FU-19 state.py fails every worker write — instruction examples write bare filenames like `state.py append devlog.jsonl '...'` and require auto-resolve to `.state/devlog.jsonl`. Pre-FU-21 state.py works only with shell-quoted JSON (PowerShell `$`-interpolation surface area). Sync state.py whenever you sync `instructions/*.md`. |
| `tools/assemble_context.py` | No (i2c is canonical) | Read from i2c upstream via sibling import. The consumer's local copy is unused for autonomous runs and may safely lag. |

**Alternative — pin framework into the consumer (hermetic builds).** Copy
`tools/{run_iteration,state_machine,invariants}.py` from i2c and overwrite
`tools/{state,assemble_context}.py` and `instructions/*.md` in the consumer,
then invoke `python3 tools/run_iteration.py` locally. Each i2c upgrade
becomes a "framework snapshot" commit in the consumer's history. Use when
the consumer needs framework versions pinned to commits in its own repo
(audit, compliance, or air-gapped deploy scenarios).

(First documented during CC autonomous-loop preflight, 2026-06-06.)

## Prose — instructions, WORKER_SPEC, adapters

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-8 | execute.md commit-format suggestion (`phase.step: title`) is not enforced anywhere | open | The prose says "default commit message format `phase.step: short title`" but nothing validates it. A pre-commit hook or a `state.py complete --validate-commit-msg` check could enforce. **Superseded by FU-40** — centralizing commits in the runner enforces the format by construction. | Phase 2 pilot reveals workers drift from the format and downstream tooling (codexbot `/diff <phase>`, waymark commit-by-phase view) needs consistency. |
| FU-9 | Refine regime in execute.md uses `step: null` for devlog entries | open | The schema allows `step: null` and the prose recommends it for Refine iterations. But there's no constraint that ties a Refine entry to *which* iteration (no iteration counter field). The commit message carries it (`14.iter3:`) but the structured data doesn't. | Phase 2 pilot does enough Refine work that iteration-by-iteration analytics matter. Add `iteration: int` optional field to `devlog_entry.schema.json`. |
| FU-10 | Production-incident anecdotes in WORKER_SPEC §3 are e2e-vintage | open | Per D-prose-8 the Codex 105k-char and Claude 5-3 incidents stay verbatim — they have pedagogical value. But once i2c has its own incidents, those should be added or substituted to keep the pedagogy current. **2026-06-21:** the public de-Meta pass dropped the explicit `(e2e)` / `state_machine.sh` labels (the anecdotes now read as neutral "A Codex/Claude iteration"); the refresh-with-i2c-native-incidents ask still stands. | i2c accumulates 2+ documented loop-discipline failures of its own. Add a refresh pass to WORKER_SPEC §3. |
| FU-11 | Per-file JSON-example validation isn't automated | **closed** (2026-06-21) | See resolution note below. `tests/test_instruction_examples.py` lifts every inline `state.py` JSON record example across `instructions/*.md` and validates it against the registered schema (devlog → entry schema; append-record → array `items`), with a floor-count guard against extractor regressions. 45 examples validate. |
| FU-26 | `close.md` and `plan.md` disagree on who advances `project.json.phase` | **closed** (2026-06-08 lifecycle redesign) | See resolution note below. |
| FU-29 | i2c's `CODEX.md` and `CLAUDE.md` adapters lacked an inline `## Output Contract` section; codex skips the 5-line exit signal as a result | open (pilot-confirmed; partial fix applied; surface reduced 2026-06-12 by FU-7) | The e2e template (`templates/CODEX_worker.md` line 136) and diplomat (`CODEX.md` line 188) both ship an explicit `## Output Contract` section that reads *"End every invocation with exactly these five lines — no additional text after"* plus the 5-line example and an exit-code table. i2c's adapters only reference `WORKER_SPEC.md` (which contains the contract) without inlining it. Claude is robust enough to follow the contract from the reference alone; codex is not. **CC Phase 3 iter 15 (2026-06-07):** first codex-on-i2c production run after the runner gained `--backend codex` support. Codex completed step 3.4 correctly — commit `f7620cc`, 228/228 tests pass, `state.py` writes for steps.json and devlog.jsonl landed coherently — but emitted prose-only output with no 5-line EXIT signal. Runner correctly reported `exit=2 "signal missing or malformed"` even though the work was substantively successful. **Partial fix applied (2026-06-07):** ported the e2e Output Contract section verbatim (with i2c-specific wording — `.state/project.json` rather than `DEVPLAN`) into `i2c/CODEX.md`, `i2c/CLAUDE.md`, `clankercourts/CODEX.md`, `clankercourts/CLAUDE.md`. Section sits between `## Runner Info` and `## Mode`. Re-firing the next codex iter on clankercourts validates the fix. **Surface reduction (FU-7, 2026-06-12):** contract is now 2 lines instead of 5; codex iter 65 (2026-06-11) showed the partial fix is necessary-but-not-sufficient (still skipped the block). A smaller block has less surface to skip but doesn't address the root cause (codex defaulting to conversational tail without the EXIT/REASON pair). | **Remaining work for full resolution:** (a) once i2c grows a `templates/` directory for bootstrap adapters (today the top-level CODEX.md / CLAUDE.md double as both reference and template), make sure the templates include the Output Contract section so every new i2c project ships with it; (b) consider whether claude adapters genuinely need it — claude historically follows the contract from WORKER_SPEC alone, but inlining is belt-and-suspenders and harmless. The CC pilot's own adapters have been patched, so this FU stays open until the template-layer fix lands. |
| FU-36 | Worker-facing prose drifts toward threat-framing instead of reason-first | open (rolling; apply at next prose touch) | Style principle, surfaced 2026-06-10 while rewriting the decisions-phase rule. **Pattern:** `field — does X for purpose Y. Read from Z.` Sourcing second, purpose first. **Anti-pattern:** imperative + reasoning-from-error + operator-context remediation + escape-hatch enumeration. The worker doesn't act on what-happens-if-broken or on the operator's recovery procedure; including that text adds prompt budget and shifts processing tone defensively. Worked example: `**Always include phase: ...** -- Decisions without phase do not appear ...; the operator has to back-fill later.` (3 lines) → `phase: <current phase id> -- marks the decision as belonging to this phase, so it appears in the phase audit (...). Read from Project State.` (2 lines, same info). | Apply the lens at next touch of `WORKER_SPEC.md`, `instructions/{plan,execute,review,close}.md`, or adapter Tool Rules. Not urgent enough for a dedicated pass; opportunistic cleanup. Likely candidates: `close.md` (many "do X so that Y" lines with Y as operator context), `plan.md` step 2.5 escalation-triggers table's Resolution column. |
| FU-37 | Periodic dead-surface audit (lightweight, opportunistic) | open (rolling) | i2c has accumulated invisible structure — assembler section catalog, conditional markers, CLI flags, env vars, schema fields — that's hard to verify is still load-bearing without explicit checking. Two recent cleanups (FU-7 exit-signal trim 2026-06-12; STOP_BEFORE_REVIEW removal 2026-06-12) were both surfaced by ad-hoc audits prompted by other work, each removing 50+ LOC of dormant or vestigial machinery. The framework's reliability gain over e2e came from moving prose into code; the opacity cost is that dead code is invisible where dead prose was self-announcing. **Categories to mine** (from the 2026-06-12 audit session): schema fields written but never read; CLI flags / env vars declared but never set in production; assembler `--section` modes with no live callers; conditional markers in `instructions/*.md` that always evaluate the same way; procedure steps workers never perform; tools nobody imports outside tests; documented invariants nobody checks. | Apply opportunistically at natural session starts ("before adding to X, is anything in X unused?"). When a cleanup candidate surfaces, scope it like FU-7 / STOP_BEFORE_REVIEW: mine the codebase + CC corpus for consumers, surface evidence, decide cut-or-keep, execute as one focused commit. Avoid big-bang audit projects; the practice is the audit. **2026-06-21 pass:** removed the no-op `STEP_BUDGET` env-var read in `state_machine.py` (read into `_` and discarded — distinct from the live assembler `--step-budget` flag), plus the `_parse_int_env` helper, the now-unused `import os`, and the test that only guarded the no-op. |

## Cross-platform

| ID | Title | Status | Context | Trigger to address |
|----|-------|--------|---------|--------------------|
| FU-12 | Multi-line JSON / `$`-laden state.py payloads on PowerShell | **closed** (2026-06-09) | See resolution note below. Code fix shipped via FU-21 (`--from-file`); adapter Tool Rules now recommend the `--from-file` path in all four CLAUDE/CODEX adapters (i2c + CC). |
| FU-20 | Templates assume `.claude/commands/` autoloads in Devmate; project-level `.llms/commands/` also doesn't get picked up in the workflows seen so far — operator-global `~/.llms/commands/` is the only reliable surface | open (pilot-confirmed) | `templates/README.md` states "Devmate / Claude Code picks up the project's `.claude/commands/` automatically — no extra configuration step." True for Claude Code, false for Devmate. Devmate's `agent_customization` skill documents `.llms/commands/` as the project-level convention. **CC pilot (2026-06-05/06) found both don't work in practice for the current Devmate session:** the operator's Devmate session is reading commands from `C:\Users\myeluashvili\.llms\commands\` only; neither `p:\shared\clankercourts\.claude\commands\*.md` nor `p:\shared\clankercourts\.llms\commands\*.md` showed up in the personal_context skills list. Possible causes: network-share path not scanned, workspace-root mismatch, requires Devmate restart to pick up new project-level commands, or project-level scanning not actually implemented for this Devmate build. **Workaround applied:** copied the 5 i2c slash commands to `~/.llms/commands/i2c-*.md` (operator-global with `i2c-` prefix). Now `/i2c-phase-plan`, `/i2c-cold-start`, etc. are available in any Devmate session; global `/phase-plan` continues to call e2e (Diplomat workflow unaffected). The commands shell out to `python tools/...` so they only do useful work inside an i2c project root; elsewhere they fail with a clear error. | Address before next i2c bootstrap: **(a)** update templates to ship the `i2c-` prefixed commands at both `templates/.claude/commands/` (for Claude Code) and `templates/.llms/commands/` (for Devmate at project-level if/when that works), AND document a manual copy-to-global step for Devmate users (`xcopy templates\.llms\commands\* %USERPROFILE%\.llms\commands\`); **(b)** investigate whether Devmate project-level `.llms/commands/` actually loads — may be a config / workspace setup question, or a network-share limitation. If (b) confirms project-level works under some setup, document the prerequisites. |
| FU-27 | Windows `Path.cwd().resolve()` expands mapped network drives to UNC; breaks `subprocess.run(cwd=...)` for `claude.exe` | **closed** (2026-06-06) | See resolution note below. `find_project_root` now uses `.absolute()`, preserving the mapped-drive letter and avoiding the UNC-cwd that Windows CMD rejects under `claude.exe`'s plugin loader. |
| FU-28 | Meta laptop sandbox prevents `claude -p` subprocess autonomy; consumer projects must invoke the loop from a server | **wontfix** (2026-06-09) | See resolution note below. Constraint is real and unchanged; documentation deemed unnecessary — operator has internalized that autonomous runs always invoke from `pirozhok` over SSH, supervised runs happen on the laptop. The original misfire was a miscommunication about *where* the loop was running, not a missing constraint doc. |

---

## Closed / decided

Items resolved, with a one-line resolution note. Historical context is cheap.

| ID | Resolution |
|----|------------|
| FU-2 (CLI side) | `append-record` subcommand added when authoring `instructions/plan.md` surfaced that PLAN needs to write new records to all three JSON-array files (steps, phases, decisions). Generic over per-record-type subcommands per design discussion: same shape as the existing `append` for JSONL, schema-validated, atomic. The prose-side framing (EXECUTE defers to PLAN via `Deferred:`) is unchanged — the CLI gap that drove FU-2 is closed but the design rule it embodied stands. |
| FU-5 | Phase 1.3 `tools/assemble_context.py` implements the conditional-section marker mechanism per ARCH §7 (evaluator registry, `requires=dependencies_nonempty`, `autonomous_only`, `supervised_only`). Markers in `instructions/plan.md` and `instructions/close.md` strip deterministically. |
| FU-6 | Phase 1.3 tests cover both leaf and non-leaf paths; `examples/smoke_test.py` also exercises `--section status` end-to-end. |
| FU-13 | `update-record FILE --match KEY=VALUE field=value ...` added when authoring `instructions/review.md` + `close.md` surfaced the need to close open decisions and flip phase status mid-flight. Generic: matches one record by a single key=value (errors on no-match or multi-match), updates one-or-more fields, validates the whole array, atomic write. Sibling to `append-record` in pattern. |
| FU-19 | Phase 3.A: `tools/state.py` ships `resolve_state_path()` — if `arg` doesn't exist and CWD has a `.state/` directory and `arg` is a bare schema filename (any key of `SCHEMA_BY_FILENAME` or `devlog.jsonl`), auto-resolve to `.state/<arg>`. Applied in every payload-handling subcommand. Instruction examples now work as written without a `.state/` prefix; explicit paths still work unchanged. Test coverage in `tests/test_state.py::TestResolveStatePath` (6 cases). |
| FU-21 | Phase 3.A: `--from-file PATH` flag landed on `append`, `append-record`, `update-record`, `append-gotcha`. Manually-enforced mutex with the positional payload (argparse's `add_mutually_exclusive_group` doesn't compose with `nargs='*'` positionals). For `update-record`, the file content must be a JSON object of field updates; for the other three it's UTF-8 text (JSON for append/append-record; plain prose for append-gotcha). Bypasses PowerShell `$`-interpolation and heredoc edge cases. Test coverage in `tests/test_state.py::TestFromFile*` (14 cases including the `$`-laden gotcha round-trip from FU-12). |
| FU-22 | Phase 3.A: `tools/invariants.py` provides `check_post_action(root, action)` returning a list of failure messages. v1 invariants cover CLOSE (`blocked == true` + phase `status == complete`), REVIEW (`state == close`), PLAN (`state == execute`), EXECUTE (`state ∈ {execute, review}`). `tools/run_iteration.py` calls it after every CLOSE dispatch and halts-and-surfaces (exit 2) on failure. Reusable from supervised tooling — operators can run `python tools/invariants.py --action close` after a manual close to catch drift. Test coverage in `tests/test_invariants.py` (18 cases). |
| FU-24 | Phase 3.A.1: prompt compaction + region reorder shipped after reading a real assembled prompt (~744 lines / ~70 KB). Two new evaluators (`multi_step_only`, `omit_in_prompt`) added to `assemble_context.py` plus a `--step-budget` flag. `WORKER_SPEC.md` multi-step subsections + production-incident anecdotes marked `multi_step_only` (strips at the v1 single-step default); shell-command discipline moved into `CLAUDE.md` and `CODEX.md` Tool Rules; `instructions/*.md` Examples / Known tooling gaps / Behavior modes marked `omit_in_prompt`; `Available Modules` gated to EXECUTE/CLOSE only (dedup with `Architecture`); regions reordered to **WORKER CONTRACT → TOOL RULES → PROJECT CONTEXT → ACTION CONTEXT** so the procedure lands at the prompt tail where model recency works in our favor. Net: ~55% token reduction with no information loss for what the action actually needs. Test coverage in `tests/test_assemble_context.py` (14 new cases: multi_step_only, omit_in_prompt, --step-budget validation, region order, Available Modules gating). |
| FU-25 | Phase 3.A.2: `Decisions` table dropped from EXECUTE recipe in `_PROJECT_CONTEXT_BY_ACTION`. Project-wide decision history is reference, not per-step load-bearing; PLAN / REVIEW / CLOSE still include it. Worker can pull mid-step via `--section` if needed. ~22 lines saved per EXECUTE iteration (multiplied across N steps per phase). Test coverage: `test_execute_includes_step_and_recent_activity` asserts `## Decisions` is absent; `test_decisions_present_for_plan_review_close` guards against accidental drop on other actions. Build/Refine regime split deferred per session discussion — savings are smaller and risk of harming silent-drift recognition outweighs the win until autonomous evidence accrues. |
| FU-1 | **Wontfix.** Phase 3 close (2026-06-07): `in_progress` dropped entirely from `phases.json` and `steps.json` schemas. Binary `pending` / `complete` is sufficient — the active phase is identified by `project.json.phase`, not by an in-flight status field; the active step is identified by being the lowest-numbered `pending` step in the current phase. Same logic resolves the previously-undocumented contradiction between `plan.md` (leaves phases `pending`) and `close.md` (had expected `in_progress`). Both procedure files now agree on binary status. The "promote pending → in_progress" CLI op (FU-1's original ask) is no longer needed; no code path writes the dropped value. `update-record` covers any remaining mid-flight field updates on phase/step records. Files touched: `schemas/{phases,steps}.schema.json`, `instructions/{plan,execute,close}.md`, mirrored to clankercourts. See iter 21 / iter 22 of CC autonomous loop for the symptom that surfaced this. |
| FU-31 | Post-clankercourts-Phase-3 audit (2026-06-07) surfaced that `instructions/close.md` had no step for `ARCHITECTURE.md`. Only step 5 covered per-module `ARCH_<module>.md` propagation and step 7 covered optional PROJECT.md risks; the project-wide `ARCHITECTURE.md` (Implementation Sequence table + Component Map + Coupling Notes + Key Decisions summary) was nobody's job. Result: clankercourts' Implementation Sequence still said Phases 1-3 were "Not started" even though `.state/phases.json` had them all `complete`. Fix: inserted new step 7 "Update ARCHITECTURE.md" (renumbering 7→8 through 11→12), placed after the decisions-closing step so it can reference closed decisions in the optional Key Decisions summary update. Implementation Sequence status flip is required; Component Map / Coupling Notes / Key Decisions edits are optional and conditional on what the phase actually changed. Commit step now includes `ARCHITECTURE.md` in the default `git add`; both worked examples and devlog summary examples updated. Mirrored to clankercourts (`instructions/close.md` hashes match). Clankercourts ARCHITECTURE.md immediate-drift fix (Phases 1-3 → Complete) shipped in the same session. **Note:** commits 41bcc9d (i2c) and aa08991 (CC) reference this work as "FU-29" by mistake — the open FU-29 (CODEX.md output contract) was already in use at the time; the entry was renumbered to FU-31 in Stack E of the state-lifecycle redesign. |
| FU-30 | Stack A–D of the state-lifecycle redesign (DESIGN_state_lifecycle_v1.md, 2026-06-08) replaced the three-way overload of `blocked` with the 7-state `state` enum (`plan`, `execute`, `review`, `close`, `audit_boundary`, `audit_escalation`, `done`). State machine returns `ACTION: EXIT` for all three halt states; `audit_boundary` covers post-CLOSE gate (was: `state=close, blocked=true`); `audit_escalation` covers mid-phase halts (was: any `blocked=true` set by EXECUTE/REVIEW escalations); `done` is the terminal state — distinct from `audit_boundary`, recoverable only by deliberate `set state=plan` write. The terminus ambiguity FU-30 originally flagged is gone: `done` and `audit_boundary` are different enum values with different machine behavior and different human-recovery procedures. Conservative closure per D-state-3: CLOSE worker always transitions to `audit_boundary` and never sets `done` directly — the human/wrapper makes the "more phases or terminate" call. Files touched: schema, state_machine, invariants, assembler renderer + tolerance, every instruction file, WORKER_SPEC, CLAUDE/CODEX adapters, slash-command templates, DESIGN_governance_v3 banner, fixture + smoke test + 50+ tests; mirrored to clankercourts with `.state/project.json` migrated in place. Commits: i2c 224aaf5 (memo), e2a71ec (Stack A), 9e53e62 (Stack B), a4d88b5 (Stack C); CC 1c126db (Stack D). |
| FU-26 | Lifecycle redesign (FU-30) collapsed the contradiction. Today's procedure: `close.md` step 12 sets `state=audit_boundary` and does NOT touch `phase`; the human/wrapper clears the gate by setting `phase=N+1 state=plan` atomically; `plan.md` step 1 catches mis-dispatch against a completed phase by escalating to `audit_escalation` (reason "plan called on completed phase"). All three positions now agree. No code changes beyond the lifecycle work itself; verified in current `instructions/{close,plan}.md` (2026-06-09). |
| FU-27 | `tools/assemble_context.py::find_project_root` switched to `.absolute()` instead of `.resolve()` (2026-06-06). `.absolute()` preserves the mapped-drive letter (avoids UNC expansion that breaks Windows CMD `cwd=` for `claude.exe`'s plugin loader); doesn't normalize `..` segments but that's irrelevant for CWD-derived paths. POSIX behavior unchanged. |
| FU-7 | Shipped 2026-06-12 as a 2-line trim rather than a 5-line tighten. Mining the 87-iter CC corpus revealed (a) 95% of iterations emitted clean 5-line blocks, (b) `next_action` field in the schema was never emitted (vestigial; runner derived from `action_type`), (c) `exit_code: 1` never emitted (state machine short-circuits halt-states before dispatch — dead branch under lifecycle v1), (d) `action_type`/`action_id` echoed information the runner already knew from dispatch, and (e) `steps_completed` semantics were inconsistent across workers (0/1/3 for PLAN) and never branched on. Conclusion: the signal was double-bookkeeping with `.state/project.json` (the canonical lifecycle source under FU-30). Trimmed contract: `EXIT: 0 \| 2` + `REASON: <one-line>`. Schema: `required: [exit_code, reason]`, `additionalProperties: false`, `exit_code` enum `[0, 2]`, `reason` 1–500 chars. Runner: dropped `RE_ACTION_TYPE`/`RE_ACTION_ID`/`RE_STEPS_COMPLETED` regexes; `parse_exit_signal` returns `{exit_code, reason}` only; `summary.log` `action=` column already sourced from the dispatched action (verified at `run_iteration.py:478` and `:596`, no change needed). Prose synced across `WORKER_SPEC.md` §4, `CLAUDE.md`/`CODEX.md` §Output Contract, `assemble_context.py` _OUTPUT_CONTRACT_REMINDER, `instructions/{plan,execute,review,close}.md` ("5-line" → "2-line"; stale "§6" reference → correct "§4"). CODEX.md turn-health-check formula renamed `steps_completed * 50` → `actions_performed * 50` since `steps_completed` is no longer an emitted field. Tests updated; old fixtures dropped. Mirrored to clankercourts. CC corpus mining script (`tools/_mine_exit_signals.py`) deleted. |
| FU-12 | Code fix shipped via FU-21 (Phase 3.A: `--from-file PATH` on `append`, `append-record`, `update-record`, `append-gotcha`). Doc fix shipped 2026-06-09: all four CLAUDE.md / CODEX.md adapters (i2c + CC) now carry a Tool Rules bullet recommending `--from-file` for multi-line or `$`-laden payloads, with a one-sentence pointer to the PowerShell interpolation gotcha. Closes the production hazard end-to-end. |
| FU-28 | **Wontfix** (2026-06-09). The technical constraint is real: Meta-issued Windows laptops cannot run `claude -p` as a subprocess (sandbox restricts non-interactive child-process semantics; CC autonomous-loop iteration 1 attempt hung 25min before manual kill). The standard operational practice — invoke `run_iteration.py` from `pirozhok` over SSH, Samba-mounted shared disk so `.state/` is seen by both laptop and server — has been in production since 2026-06-06 and worked cleanly for CC Phases 2–4. Original incident was a miscommunication about where the loop was running, not a missing constraint doc. Operator has internalized the laptop-vs-server rule and explicitly declined the doc-side fix. |
| FU-35 | **Closed** (2026-06-21). The `cache_control`-marker plan was unreachable: the runner pipes plaintext over stdin to the agentic CLIs (`claude -p`, `codex exec`), and `cache_control` is a raw-API content-block construct — and we can't drop the CLIs (the worker needs their tool-running). Shipped instead as a prompt split exposed by the assembler: `assemble_context.py --emit {full,system,user}` (default `full` is byte-identical to before; `system` = WORKER CONTRACT + TOOL RULES, `user` = PROJECT CONTEXT + ACTION CONTEXT + Output Contract reminder; `full == system.rstrip() + "\n\n" + user`). The runner's claude path assembles both, writes `iteration_NNN_system.md`, and passes `--append-system-prompt-file <that> --exclude-dynamic-system-prompt-sections` so Claude Code prompt-caches the stable prefix and reuses it on iter 2+ within the cache TTL; the volatile body stays on stdin. Codex sends `--emit full` and relies on OpenAI's server-side prefix caching (no system-prompt flag). Reliability-neutral (identical content, relocated). Measured via the existing `tokens_cached` column in `summary.log` (FU-33). Files: `tools/assemble_context.py` (`--emit` + `build_stable_prefix`/`build_volatile_body`), `tools/run_iteration.py` (`assemble_prompt(emit=...)`, `invoke_claude(system_prompt_file=...)`), `ARCH_assembler.md` (§3.2/§6/§11.3/D-arch-12/§15), tests (`TestEmitSplit`, `TestInvokeClaudeCacheFlags`, `TestCodexNoSplit`; 299 tests pass). Pre-existing quirk fixed in the same work: `assemble_prompt` no longer hardcodes `--backend claude` -- it passes the dispatched backend, so codex runs embed CODEX.md Tool Rules (a lock-in test asserts the two backends produce different prompts). |
| FU-17 | Phase 3.4 pre-packaging pass (2026-06-21): `_validate_args` rejects `--phase` with `--section {status,architecture,module}` — those sections report on `project.json.phase` or ignore phase, so accepting `--phase` silently misled the caller. ARCH §11.3 documents the exit-2 case; tests in `TestCliArgumentErrors`. |
| FU-11 | Pre-packaging pass (2026-06-21): `tests/test_instruction_examples.py` extracts every inline `state.py append`/`append-record` JSON record across `instructions/*.md` (string-aware balanced-brace scan) and validates each against the registered schema (devlog → entry schema; array files → their `items`). A floor-count guard fails the test if the extractor matches nothing. 45 examples validate. |

---

## How to use this file

- When you notice a gap or design note during a build session, add it as a new `FU-N` row in the right section. One-line title, brief context, explicit trigger.
- When you act on one, move it to **Closed / decided** with a one-line resolution note. Don't delete — historical context is cheap and useful.
- Reference these IDs from instructions/, plans, or commit messages when relevant.
- This file does not gate any phase. It is a backlog, not a blocker list.
- When picking up after a break, read the **Cold-start summary** above first — it captures where things were left and what to do next.
- Strategic tracks, cross-project priorities, and the current recommendation live in **Active Roadmap** (above the FU tables); the tables are the fine-grained backlog. This is the single ongoing tracker — the standalone Desktop `i2c TODO.md` was merged in on 2026-07-02.

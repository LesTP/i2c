# i2c — status, roadmap, and the refine-backlog pointer

The single ongoing tracker for i2c: a static "how it works" preamble (here), a
dynamic **Status** (session entry point), and the **Active Roadmap** (strategic
tracks + priorities). The fine-grained open backlog itself is the **`i2c fu`
command** (`.state/followups.json`) — not a table in this file.

Distinct from `FUTURE_waymark.md` / `FUTURE_recovery.md` (each a roadmap for one
deferred initiative); this is the catch-all orientation + tracker.

## How to use

- **Capture** a gap or design note: `i2c fu add --kind <k> --title "…"
  [--context --trigger]` → lands in `.state/followups.json` (never hand-edit it).
  Big ideas get a DESIGN memo the FU `refs`. Kinds: prose · dead-surface ·
  doc-reconciliation · cli-ergonomics · test-hardening · structural-refactor ·
  experiment-log · other.
- **Prioritize / close:** `i2c fu prioritize <id> --priority now|next|eventually|icebox`;
  `i2c fu close <id> [--resolution "…"] [--status closed|wontfix]`.
- **Read:** `i2c fu list [--status --kind --priority]`, `i2c fu show <id>`,
  `i2c fu render`. The backlog is advisory — it gates no phase.
- **History:** pre-command closures live in `archive/followups_closed.md` (frozen);
  anything closed via `i2c fu` is queryable with `i2c fu list --status closed`.
- Reference FU IDs from instructions/, plans, or commit messages when relevant.

---

## Status (session entry point)

**Where we are (2026-07-07).** Foundation + **state-lifecycle v1** (now 8-state:
`plan|tests|execute|review|close|audit_boundary|audit_escalation|done`)
+ **packaging** (installable `i2c` package/console, `i2c init`/`eject`, `i2c.toml`,
`schema_version` + `i2c migrate`) are shipped. The **control surface** is complete:
`i2c.control` is the single structured projection layer (`status` / `phase-summary`
/ `decisions` / `devlog` / `escalation` / `logs` / `portfolio`, all `--json`) with a
`clear-boundary` action and a Telegram bot (`i2c serve telegram`). **Recovery v1**
(`i2c diagnose` / `reconcile`) and the **telemetry sidecar** (`.state/telemetry.jsonl`)
have landed. **FU-40 is complete (2026-07-04)** — the runner now owns *all* git commits (EXECUTE code, REVIEW fix-ups, CLOSE docs, and the .state/ tail); the worker no longer runs git for any action (also closes FU-8).
The **refine tier** shipped — the ad-hoc backlog is now the `i2c fu` command;
**Proposal B shipped** — the deterministic `i2c refine <fu-id>` single-shot loop
(assembler `refine` recipe + `instructions/refine.md`, `run_refine.py`, the
sub-phase invariant, `devlog`/`telemetry` refine support per D-refine-8, schema v4
guard, the `i2c refine` CLI) **and the admin-gated `/refine` Telegram command**
(FU-54); followups-only-repo dogfooding (FU-55) is the remaining deferred piece.
The **`tests` action** (test/impl separation — the benchmark oracle) **shipped
2026-07-06 (`a110138`)** and ran clean end-to-end on clankercourts Phase 17.
**diplomat** migrated to i2c (2026-07-01), live at **phase 51** via the bot;
clankercourts is driving the `state_manager` phases autonomously (Phase 17 done
via `plan→tests→execute→review→close`; Phase 18 next).

**Update (2026-07-28) — oracle signal group shipped (FU-51/52/44).** The first
greenfield/typed/codex run, **build-a-stew** (`P:\shared\build-a-stew`, 59 iters),
exposed that the shipped `tests` oracle wasn't *operationally* trustworthy: all 59
telemetry rows had `tests_pass=null` (scaffold ships `test_cmd` commented, and unset
was silent), the oracle ran an unscoped/whole-suite command (a compiled project could
false-green on broken `tsc`), and TESTS authored fragile / contract-incomplete suites.
Fixed the **signal group** in-session (i2c is followups-only, so by hand — not a phase
or `i2c refine`, cf. FU-55): **FU-51** (contract-coverage checklist + oracle
anti-patterns in `tests.md`), **FU-52** (runner warns when `test_cmd` unset; template
documents typecheck/build chaining), **FU-44** (`{phase}` interpolation scopes
`tests_pass` to `tests/acceptance/phase_<N>/`, graceful-skips when the suite is absent).
701 tests green. **Remaining pre-benchmark work:** the integrity pair **FU-43** (hard
CLOSE suite-digest invariant) + **FU-50** (sanctioned oracle-correction lane), and
**FU-53** (per-iteration runaway guard — build-a-stew iter 25 burned 11.4M tokens). A
live build-a-stew signal check needs **pirozhok** (laptop vitest fails on rollup/NTFS —
the FU-52 env cause).

**What's next.** The dominant thread is the **model-benchmark initiative**
(telemetry → a phase-level `tests` action as a real oracle → benchmark + routing). **tests_action is SHIPPED** (2026-07-06, `a110138`; validated on clankercourts Phase 17 — oracle integrity held, acceptance suite frozen at the `N.tests` commit). The next benchmark step is the **replay harness on clankercourts + routing v0**.
See **Active Roadmap** below for tracks, priorities, and the current recommendation.

**Next session (planned order).** (1) **FU-20** (ship `templates/.llms/commands/` for Devmate + the loading check) — *maybe*. (2) then the **benchmark replay harness + routing v0** (tests_action shipped 2026-07-06 — the oracle is now real; **FU-44** scopes `tests_pass` to the acceptance suite and rides the harness). Proposal B is now fully shipped (the `i2c refine` loop core + the `/refine` bot), so the fix/bugfix consolidation is unblocked.

**Quick orientation** (from a project root with `.state/`, package installed):

```powershell
$env:PYTHONIOENCODING="utf-8"
i2c next-action            # ACTION + NEXT
i2c status                 # project snapshot (--json for structured)
i2c fu list --status open  # the refine backlog
i2c migrate --check        # schema-drift check (exit 1 if a migration is needed)
```

From the i2c repo itself: `python -m unittest discover -s tests` and
`python examples\smoke_test.py`.

**Canonical references:** build status → `README.md`; decisions → `DECISIONS.md`;
assembler contract → `ARCH_assembler.md`; ARCH-authoring → `ref/SPEC_architecture.md`
+ `ref/GUIDE_architecture.md`; workflow → `WORKFLOW.md`; changelog → `CHANGELOG.md`;
historical memos → `archive/`.

---

## Active Roadmap (tracks + priorities)

> Merged from the former Desktop `i2c TODO.md` (2026-07-02) and `i2c next.md` (2026-07-03). This file is now the
> single ongoing tracker: strategic tracks + priorities live here, the detailed
> per-item open backlog is the `i2c fu` command (see How to use, above).

### 1. Release readiness (gated on the core tracks below)
- **Name — decide soon (Q-pkg-1 / D-pkg-1).** Check PyPI/GitHub for `idea2code` (or alternatives), reserve both, and start using it in content. Keep `i2c` as the import/command name (Q-pkg-1 solved). Zero eng cost; unblocks everything public-facing. (The internal 0.2.0 cut is already done.)
- **Showcase repo.** Push the best completed project (CC's autonomous phase run, or a sanitized equivalent) public with full `.state/` history + devlog + telemetry + commit trail; link from README as "what a governed project looks like." One afternoon of curation; strongest sales artifact.
- **First tagged release.** CHANGELOG `Unreleased` → `0.x`, publish to PyPI under the new name, GitHub release. Ship rough — the window favors speed. Discord / `/ask` / dashboard v0 come *after*.
- **Security paragraph — mandatory before public.** Half a page in the README: what the worker can touch, why the gates exist, recommended container setup. Table stakes before strangers run auto-approving agents.

### 2. Parallel medium tracks
- **Backend abstraction (FU-38)** — *see `DESIGN_backend_v1.md`.* **(a) Gemini** agentic-CLI backend — **spec'd, shelved** (run Gemini/Gemma as OpenRouter **model-ids** for the benchmark; native CLI only later as a free/subscription **prod** cost-opt). **(b) OpenRouter** — **Option C (reuse codex) is BLOCKED on codex 0.124** (live smoke 2026-06-30): codex dropped `wire_api="chat"`, now requires `"responses"` (OpenAI Responses API); OpenRouter is Chat-Completions-native and hung on the responses wire. **Re-ranked:** **B** = a chat-completions agent CLI (aider/opencode/OpenHands/…) → OpenRouter, used as a §1.1 CLI backend (now preferred); **A** = in-house harness over toolkit's `OpenRouterProvider` (diplomat-proven, but bigger + toolkit dep / `i2c[openrouter]`). Salvage C only via OpenRouter adding a Responses endpoint, a Responses↔Chat proxy (LiteLLM), or an older codex (conflicts with the 0.124 bot). **Decide via a time-boxed spike:** point aider or opencode at OpenRouter as a §1.1 CLI backend (Option B); if it slots into the table, "backend-agnostic" is demonstrably true (three backends) and D-pkg-12's protocol design unblocks — if not, you learn where the contract leaks before a user does.
- **Multi-iteration loop (Phase 3.C / FU-32 #2)** - **RESOLVED 2026-07-05 (D-run-1/D-run-2): single-iteration-per-invocation is the design.** Cross-action multi-step is incompatible with per-action backend routing; batched-EXECUTE (the only coherent unit) is deferred pending model-benchmark evidence. The --step-budget flag + multi_step_only machinery were removed and WORKER_SPEC / assembler / contract docs simplified.

### 3. Fleet migration

> The bullets below are the **Build** migration. **Refine-tier (`i2c fu`) adoption** is a separate track: i2c done; **clankercourts done (2026-07-05, `1fd85f7`)**; toolkit + diplomat pending (**FU-41**) - diplomat is selective (parked items only; `WORK_SEQUENCING` / `RESEARCH_NOTES` / `TUNING_LOG` stay put).

- **toolkit** - done (migrated).
- **diplomat** — **migrated & live (2026-07-01)**: fully on i2c at **phase 51 / plan**, driven autonomously via the i2c bot with a `[run.backends]` split (plan=claude, execute=codex, review=claude, close=codex); Stage 0+1 import committed `1c5014c` (49 phases + snapshot history serialized into `.state/`). Residual: normalize the 13 flagged decision statuses; converter FU — handle `Closed (…)` / `Superseded by` / `| Priority:` status suffixes.
- **phosphene** — blocked on **Q-mig-7 / D-mig-4** (integer phase-id schema vs `MVP.4d`); needs a schema/renumber decision.
- **codexbot + others** — audit not yet done.
- **Ratify D-mig-2..7** — paper-only (toolkit + diplomat are the evidence).

### 4. Orchestrator + remaining surfaces (§7) — collapsed
- **Human** driver = exists (CLI/Telegram). **Policy** driver = `/batch` shipped; the **multi-iteration loop is the next Policy** (so this overlaps §2, not a separate track). **Agent** driver = already exists as operator + assistant; "needs no protocol."
- Genuinely-optional remainders: a `/ask` in-product LLM agent surface, and a **Discord** extra.

### 5. Larger net-new initiatives (deferred, well-specified)
- **Discovery/Architecture interview kit — highest *adoption* lever; pre-release.** Package the browser-chat Discovery + Architecture prompts as shippable assets — `i2c init --interview` emitting a guided prompt to paste into any assistant, or a `docs/bootstrap/` prompt kit. i2c needs ARCH files authored *before* autonomous PLAN works (cf. FU-32 / FU-15), so a new user is otherwise "installed, then stuck." Nothing else on the backlog moves adoption as much.
- **Recovery `fix` agent (`FUTURE_recovery.md`)** — code-class sibling to reconcile: `diagnose`(code) → `.state/diagnoses.json` + `fix.md` worker → human-gated repair; later self-healing. Held until recovery v1 is exercised in anger.
- **Waymark VS Code extension (`FUTURE_waymark.md`)** — **deferred indefinitely** (2026-07-01): the read-only web dashboard (below) subsumes its Scope A; a VS Code plugin re-enters only as the future *control* surface (Scope B), if ever.
- **Portfolio dashboard — SPEC'D (`DESIGN_dashboard_v1.md`, committed `21b339c`).** Read-only, browser-viewable view over `i2c.control` + `.state/` + telemetry + `doctor`; panels: portfolio / project drill / telemetry / health / topology (the "what runs where" conceptual aid). **Read = web (portable); control = local/trusted** (CLI/Telegram now, VS Code plugin later). Stages: **v0 static HTML generator** (no server/auth) → v1 local read-only server (`i2c[web]`) → v2 remote (WireGuard/tunnel + auth) → v3 control (separate). Future-proofed now: no-secrets allowlist + single auth choke point.
- **Explicit brownfield path** — Reverse Architecture → CODEBASE.md → scoped discovery, plus the brownfield-archaeology skill. i2c's README is greenfield-focused.

### 6. Rolling backlog (small FUs, opportunistic)
FU-16 (naive Available-Modules fallback) · ~~**FU-40**~~ (**closed 2026-07-04**: runner owns all commits — EXECUTE/REVIEW/CLOSE + .state/ tail; resolved FU-8)  · FU-29 (adapter Output Contract → `templates/` layer) · FU-20 (Devmate project-level commands) · FU-18 (slow tests on share) · FU-9 (Refine devlog iteration field) · FU-10 (refresh WORKER_SPEC anecdotes) · FU-36 (reason-first prose) · FU-37 (rolling dead-surface audit) · FU-15 / FU-4 / FU-3 / FU-14 (low-pri ergonomics). Full detail via `i2c fu show <id>`.
- ~~FU-32 Δ5~~ → **deprioritized.** No occurrence; current soft-handling beats the spec; only worth it for externally/migration-authored ARCHes, and then scoped to `## Phasing` only.

### 7. Model-benchmark initiative (telemetry · test isolation · benchmark)
Strategic thread: **find the cheapest model that still succeeds per kind of step, and route to it.** Three sub-tracks, in dependency order.

- **Telemetry sidecar — SHIPPED.** `.state/telemetry.jsonl`: runner-authored, schema-validated, git-tracked execution envelope (model, tokens, cost, timing, git deltas, prompt hash, phase meta, drift, outcome) per autonomous iteration. Observational only; never control state; never fatal. *Increment 1* (data plane: schema, `telemetry.py`, runner capture, scaffold seed, tests) committed `707aec8`; *Increment 2* (cost/tier from bundled `pricing.json` + `[telemetry.pricing]`; opt-in `tests_pass` oracle via `[telemetry].test_cmd`) committed `636a192` (548 green). *Deferred* (schema already nullable, additive later): structured `review_findings`, exact cache-aware cost, `tool_calls`, codex model capture. Spec: `archive/DESIGN_telemetry_v1.md`.
- **Test isolation — SHIPPED (2026-07-06, `a110138`; `archive/DESIGN_tests_action_v1.md`).** Regime-conditional, phase-level `tests` action (`plan → tests → execute → review → close`, Build only): freezes a contract-derived acceptance suite **before** EXECUTE so the implementation is graded against tests it didn't author — turning `tests_pass` from self-graded into a **real oracle** (the linchpin the whole benchmark rests on). Full surface landed (state enum, `state_machine` `VALID_STATES`+`decide`, `instructions/tests.md`, assembler `ACTIONS`+recipe, `config._RUN_ACTIONS`, WORKER_SPEC, goldens, no-op schema-version bump 1→2, **plus** a runner `N.tests:` commit block — the FU-40 correction to §8's original "runner: no change"). Suite identity = path convention `tests/acceptance/phase_<N>/`; integrity is **soft** in v1 (EXECUTE must not weaken the frozen suite; REVIEW flags weakening as a Must-fix). **Validated end-to-end on clankercourts Phase 17**: oracle held (suite byte-frozen at the `17.tests` commit, 57 contract tests green, review integrity check confirmed it unchanged). Deferred (benchmark-era): **FU-43** hard CLOSE integrity invariant, **FU-44** scope `tests_pass` to the acceptance suite, **FU-45** supervised phase-plan folding.
- **Benchmark + routing — SPEC'D (`DESIGN_benchmark_v1.md`).** Measure success vs model tier per `(action × regime × size)` bucket; find the saturation knee. **Substrates:** clankercourts = proven hermetic replay substrate (689/692 offline; 62 commit-linked EXECUTE steps); diplomat = forward data firehose post-migration; phosphene/diplomat prose = labeling only (not replay). **Next:** replay harness on clankercourts (run on **pirozhok** — cross-mount path bug on the laptop), e2e prose normalizer (labeling), **routing v0** (top tier for PLAN/REVIEW/TESTS by blast radius; cheap-first-with-escalation for EXECUTE/CLOSE). **Deps:** model panel rides FU-38 (OpenRouter Option C blocked, see §2). Oracle building blocks = toolkit `prompt_regression` + `edit_classifier`. **Clean-room rule:** for benchmark-generator projects the operator runs the loops; the assistant authors only spec/arch (`DESIGN_benchmark` §7.3).
- **Content (ready now):** the "agentic coding evals are self-graded" finding (oracle contamination; analysis done in `DESIGN_benchmark_v1`) is a standalone essay/talk section — it markets i2c without marketing it.

### Recommendation (updated 2026-07-03)
0. **Refine tier — ✅ shipped** (Proposal A: the `i2c fu` backlog; **and Proposal B**: the `i2c refine <fu-id>` single-shot loop + the `/refine` bot command). The drift class it targeted is closed; the only remaining refine item is followups-only-repo dogfooding (FU-55).
1. **Benchmark thread (§7) is the highest-leverage line.** Test isolation (the `tests` action) **is shipped** (2026-07-06) — the oracle linchpin. Next: the **replay harness on clankercourts + routing v0** (run on pirozhok — laptop cross-mount path bug), with **FU-44** (scope `tests_pass` to the acceptance suite) riding it. Diplomat is the forward telemetry firehose; the model panel rides FU-38.
2. In parallel, **decide the backend via the §2 spike** (aider/opencode → OpenRouter) — it validates "backend-agnostic" and picks Option A vs B empirically. The **multi-iteration loop** is the alternate big track (after a cache-hit check).
3. **Sleeper: the Discovery/Architecture interview kit (§5)** — the top *adoption* lever and a pre-release gate; don't leave it in the TBD bucket if public release is near-term.
4. Hold `fix`, Waymark, Discord/`/ask` until a migration shakes out recovery v1. **Release readiness (§1)** is gated on the core tracks, but the **name** + **security paragraph** are cheap to start now.

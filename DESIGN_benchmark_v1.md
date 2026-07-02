# DESIGN — Model Benchmark & Step-Complexity Routing (v1)

> **Status:** Draft / proposed. Captures the design discussion of 2026-06-30.
> No code yet. Decisions below are proposals (D-bench-*) pending ratification.
>
> **One-line goal:** Find, per kind of step, the *cheapest model that still
> succeeds* — and route work to it automatically — using data i2c generates as
> a by-product of normal development.

---

## 1. Motivation

i2c breaks work into discrete, typed actions (PLAN / EXECUTE / REVIEW / CLOSE,
plus probe / integration_check). Today every action runs on whatever backend +
model the project's `i2c.toml` names — typically a single capable (expensive)
model for everything. But steps are wildly unequal in difficulty: a CLOSE that
promotes a gotcha and runs the phase tests is near-mechanical; a PLAN that
decomposes a module is judgment-heavy.

**The hypothesis:** if we measured success rate as a function of model capability
per *kind* of step, we'd see the success curve **saturate** — beyond some tier,
a stronger model buys nothing. The cheapest model at that knee is the
*minimum sufficient* model for that bucket. Routing each step to its minimum
sufficient model could cut cost (and rate-limit pressure) substantially with no
loss in success.

This document specifies how to measure that knee and how to act on it.

---

## 2. Goals / Non-goals

**Goals**
- A repeatable way to benchmark a *panel* of models against a *corpus* of real
  i2c steps, scored by an objective-ish oracle.
- A per-bucket "saturation" analysis that yields a minimum-sufficient tier.
- A routing policy that uses the result at runtime.
- Make the data **a by-product of normal i2c runs**, not a hand-curated dataset.

**Non-goals**
- A universal/public benchmark. This calibrates *our fleet* under *our* model
  lineup; it is explicitly fleet-local and non-stationary (see §10).
- Replacing REVIEW or human judgment as the quality gate. The benchmark informs
  routing; it does not certify code.
- Auto-tuning in a closed loop (no online learning of the router in v1).

---

## 3. Two separable problems

Keep these apart; they have different time-horizons and difficulty.

1. **Measurement** — build the benchmark (corpus + panel + oracle + analysis)
   that tells us the minimum-sufficient tier per bucket.
2. **Routing** — a runtime policy that picks a model per step.

Routing can ship as a crude heuristic **immediately** and be refined by
measurement later. They do not block each other.

---

## 4. Conceptual model

### 4.1 Difficulty ≠ blast radius

Two orthogonal axes decide how cheap we dare go:

- **Difficulty** — how capable a model must be to do the step *at all*.
- **Blast radius** — how much downstream damage a *silent* failure causes.

A bad **PLAN** poisons every EXECUTE in the phase; a missed Must in **REVIEW**
becomes tech debt the oracle never sees. These failures are high-blast and
*hard to detect locally*. EXECUTE/CLOSE are test-guarded and locally reversible.

**Consequence (D-bench-1, proposed):** pin PLAN and REVIEW to the top tier
regardless of measured local success; only cheap-out the test-guarded actions
(EXECUTE, CLOSE, probe). This captures most of the savings at near-zero risk,
*before* any benchmark exists.

### 4.2 The saturation knee

Per bucket, plot success rate vs tier. The cheapest tier at the knee is the
target. We expect the knee to differ by action type:
- CLOSE: low knee (mechanical) — cheap tier likely saturates.
- PLAN / REVIEW: high knee (judgment) — likely needs top tier.
- EXECUTE: bimodal — trivial steps vs gnarly ones; the interesting case.

### 4.3 Buckets

A "bucket" is `(action_type, regime, size-band)` where regime ∈
{build, refine, explore} and size-band is a coarse LOC/files-touched tier.
Leaf vs non-leaf is a secondary split (integration steps are harder).

---

## 5. The oracle problem (the crux)

A benchmark needs a success signal. i2c's natural oracle is *tests pass + commit
lands + REVIEW finds no Must/Should*. But the corpus audit (§6) exposed a
contamination problem:

**EXECUTE steps write implementation AND their own tests in the same commit**
(e.g. clankercourts 1.3: "Wrote schemas/map.schema.json … Added 11 unit tests …
All 30 tests pass"). So "run the tests" is partly *self-graded* — a weak model
can write trivially-passing tests and look successful.

Three oracle options, increasing trust:

| Oracle | How | Trust | Limit |
|---|---|---|---|
| **Self-tests** | run model X's own tests | low | self-graded |
| **Reference tests vs X's impl** | run the human-accepted committed tests against X's implementation | high | needs stable interface (X may rename) |
| **LLM-as-judge** | compare X's diff to the reference diff | medium | judge variance; reuse toolkit `prompt_regression` + `edit_classifier` |

**Granularity ↔ oracle tradeoff:**

- **Per-step replay:** ~70 units (§6), but contaminated oracle.
- **Per-phase replay:** replay a whole phase's EXECUTE steps, then run the
  *final phase-close test suite* (human-accepted, present in git at close) —
  clean oracle, but only ~16 units and costlier per trial.

**Decision (D-bench-2, proposed):** run **both** — per-phase for trustworthy
headline numbers, per-step (self-test signal) for resolution on the EXECUTE
knee, validating the noisy per-step signal against the per-phase truth.

**Forward fix (D-bench-3, proposed):** for *future* data, make steps
replay-friendly by construction — record a reference test command + start/end
commits per step in telemetry (§8), so the reference-tests oracle becomes
available without retrofitting.

---

## 6. Corpus audit (findings, 2026-06-30)

### 6.1 i2c JSONL — clean, structured, commit-linked (replay substrate)

| Project | Records | Phases | EXECUTE w/ commit | plan / review / close | other |
|---|---|---|---|---|---|
| clankercourts | 124 | 1–14 | **62 / 62** | 12 / 15 / 14 | 11 probe, 10 integ-check |
| toolkit | 18 | 5–6 | 8 / 9 | 2 / 2 / 2 | 1 probe, 2 integ-check |

~**70 EXECUTE steps, every one carrying a commit hash**, plus ~14 PLAN /
17 REVIEW / 16 CLOSE judgment samples. Only **1 escalate** in the whole set.
devlog fields today: `phase, step, action, outcome, summary, contracts,
commit, timestamp` (summary is prose).

**Mode-mixing caveat:** the historical corpus is *not* uniformly autonomous —
e.g. clankercourts' phase-1 PLAN summary reads "Supervised plan with operator
approval on step split." Supervised steps are **contaminated** for benchmark
purposes (§7.3) and must be excluded. Today mode is only inferable from prose,
so the clean autonomous count is **lower than 70** and currently un-counted —
fixed forward by the `mode` telemetry field (§8).

### 6.2 e2e prose — rich but messy (labeling only, not replay)

| File | Lines | Entries | autonomous | supervised | test mentions |
|---|---|---|---|---|---|
| diplomat archive | 4267 | 361 | 6 | 22 | 579 |
| phosphene archive | 2534 | 194 | **148** | 0 | 201 |
| phosphene active | 239 | 23 | 18 | 10 | 9 |

e2e entries are **milestones, not steps**: lumped commits ("25 commits"), no
per-step commit, mixed supervised/autonomous. **Usable for difficulty *labeling*
and feature mining — not for replay.** phosphene's archive (148 autonomous
entries with test deltas) is the best e2e seam; diplomat's archive is
supervised-heavy.

### 6.3 Hermeticity + replayability (clankercourts) — CONFIRMED

- **Hermetic:** 694 tests ran fully **offline** in ~46s → **689 passed,
  2 skipped, 3 failed**. The 3 failures are a Windows **cross-mount
  `os.path.relpath`** artifact (`\\192.168.0.50\shared` vs `C:` temp), **not**
  network/LLM. `agent_ladder`/`arena`/`bare_llm` tests use **fakes** (fake API
  keys; the one `OPENROUTER_API_KEY` ref is an env-interpolation assertion).
- **Replayable:** all sampled devlog hashes exist in git (165 commits);
  commits follow the `phase.step:` format → clean per-step checkout points.
- **Caveat (D-bench-4):** run replay on **Linux/pirozhok** (single mount), not
  the laptop over Samba, to avoid the cross-mount path failures. This aligns
  with the deployment model (autonomous runs already live on pirozhok).

**Verdict:** clankercourts is a proven hermetic replay substrate.

---

## 7. Two data strategies

### 7.1 Retrofit historical data — the noisy v0 (cheap, do now)

Harvest the existing i2c EXECUTE steps (~70 gross, fewer after dropping
supervised steps \u2014 §6.1/§7.3) + e2e labels. Good enough to tell us
*where to look* (which buckets vary).
and small N. Treat as v0, not ground truth.

### 7.2 Forward-generate clean data — the primary strategy

Instrument i2c **before** generating data, so the retrofit pains (normalization,
lumped commits, oracle contamination) become design decisions instead of cleanup
jobs. Every real phase becomes a benchmark datapoint at ~zero marginal cost.

Forward generation also unlocks what replay cannot: **observe real outcomes** and
optionally **A/B models live**:
- **Passive** (zero behavior change): record richer telemetry per action; harvest
  later. Build the schema now.
- **Active** (deliberate routing experiments): route some real steps to a cheaper
  tier and watch. Generates the saturation signal directly — but a cheap failure
  costs real rework, so restrict to low-blast actions (EXECUTE/CLOSE) and enable
  only after passive data shows safe zones.

**Decision (D-bench-5, proposed):** passive telemetry first (fleet-wide, in
i2c); active A/B deferred until passive data shows safe zones.

### 7.3 Clean-room generation (contamination control)

The benchmark measures a *worker model* performing a governed action. Any human
or assistant contribution to that action measures "human + model," not the
model — so it must be excluded.

**Rule (D-bench-8, proposed):**
- Benchmark-grade phases run **autonomously**. The worker performs PLAN /
  EXECUTE / REVIEW / CLOSE **unaided**.
- The human/assistant authors **only the spec layer** — PROJECT.md,
  ARCHITECTURE.md, ARCH_*.md (and the adapter). These are *inputs* to the
  measured action, not the action itself, so authoring them is clean.
- **Supervised intervention contaminates the datapoint.** Steps run in
  supervised mode (operator approves a step split, edits a plan, steers an
  execute) are tagged `mode: supervised` and **excluded from scoring**.
- Working agreement for generator projects: the assistant writes spec/arch;
  the operator runs the loops. The assistant does **not** perform governed loop
  actions on a project being used as a benchmark source.

This is why supervised projects (and i2c's own prose-tracked, supervised
self-development) yield no clean data, and why forward generation must be
explicitly autonomous to count.

---

## 8. Telemetry schema (the instrumentation)

> **Full spec:** `DESIGN_telemetry_v1.md` realizes this section. Key refinements
> there: a runner-authored **sidecar `.state/telemetry.jsonl`** (not enriched
> devlog records, since devlog is worker-authored + `additionalProperties:false`
> + append-only); cost is **derived/best-effort**; `review_findings` is deferred;
> `mode` is constant `autonomous` from the runner (presence ⟺ benchmark-eligible).

The smallest change that makes every future i2c run emit benchmark-grade data.
i2c already records `outcome/commit/contracts`; the gap is **structuring the
prose** and adding **cost/model/replay** fields. Proposed per-action devlog
extension (D-bench-6, proposed):

```jsonc
{
  // existing
  "phase": 12, "step": 3, "action": "execute", "outcome": "complete",
  "summary": "...", "contracts": [], "commit": "abc1234",
  "timestamp": "2026-06-30T08:00:00Z",

  // NEW — run mode (benchmark scoring uses autonomous only — see §7.3)
  "mode": "autonomous",            // autonomous | supervised

  // NEW — model / cost
  "model": "claude-sonnet-4.6", "tier": "T1", "backend": "claude",
  "tokens_in": 18240, "tokens_out": 2310, "cost_usd": 0.071,
  "wall_clock_s": 96, "tool_calls": 14,

  // NEW — oracle signal
  "tests_pass": true, "tests_cmd": "pytest -q",
  "review_findings": {"must": 0, "should": 1, "optional": 3},
  "drift_flag": false,              // from diagnose/reconcile post-action

  // NEW — replay keys
  "start_commit": "def5678",        // parent state the step began from
  "prompt_hash": "sha256:...",      // assembled prompt fingerprint

  // NEW — step features (for the complexity predictor)
  "regime": "build", "leaf": true,
  "files_touched": 2, "loc_added": 140, "loc_removed": 12
}
```

Most fields are already computable in `run_iteration.py` (cost/tokens from the
backend, LOC/files from git, drift from recovery). This is a self-contained i2c
phase and the single highest-leverage item: **put it in i2c and the whole fleet
emits data for free.**

---

## 9. Components

1. **Telemetry (§8)** — devlog schema + `run_iteration` logging. *In i2c.*
2. **Model panel** — the set of tiers under test. **Enabled by FU-38 backend
   abstraction:** the OpenRouter harness over `llm_client`
   (`i2c[openrouter]`) gives many cheap models through one interface; the Gemini
   agentic-CLI backend adds another tier; claude/codex are the incumbents. The
   benchmark's "panel" is literally FU-38's backend set. (See §11.)
3. **Replay harness** — given a devlog step: checkout `start_commit`, re-assemble
   the prompt (`i2c assemble`), run model X, run the oracle, record a telemetry
   row. Per-phase mode first (clean oracle), per-step second. *Runs on pirozhok
   (D-bench-4).* Reuses toolkit `prompt_regression` for the LLM-judge oracle.
4. **e2e normalizer** — parse phosphene/diplomat prose into the *labeling* schema
   (action, outcome, test-delta, LOC, contract-change). Feeds the predictor, not
   the replay.
5. **Analysis** — per-bucket success-vs-tier curves, knee detection, cost model.
6. **Router** — consumes the per-bucket table at dispatch (§12).

---

## 10. Project roles

The fleet context (telemetry queued, FU-38 incoming, diplomat being actively
developed + about to migrate, clankercourts migrated but low-priority) assigns
clean roles:

| Project | Role | Why |
|---|---|---|
| **clankercourts** | **Replay testbed + v0 dataset** | Proven hermetic (§6.3), 62 commit-linked EXECUTE steps. Not actively developed, but doesn't need to be — the data already exists. |
| **diplomat** | **Forward data firehose** | Actively developed; `i2c import` is queued (TODO §3, "48-phase stress test, highest signal"). Once migrated, 48 phases of real work emit clean telemetry. Likely hermetic (game engine; clankercourts' conftest "mirrors sibling Diplomat"). **Confirm hermeticity at migration.** |
| **toolkit** | Secondary replay (small) | 8 commit-linked steps; also *provides* `prompt_regression`/`edit_classifier` as harness deps. |
| **phosphene** | Labeling only — **avoid for oracle** | Non-hermetic (embeddings, live LLM APIs, real corpus). Prose archive good for difficulty labels. |
| i2c itself | The instrumentation home — **not a data source** | Telemetry lives here; benchmarking is dogfooded i2c tooling. But i2c is **not self-hosted** (no `.state/`; developed supervised via `DECISIONS.md`/`FOLLOWUPS.md` — bootstrap paradox), so its own development emits no clean governed-loop data. |

---

## 11. Dependencies on existing i2c work (`FOLLOWUPS.md` → Active Roadmap)

This initiative is mostly *enabled by* already-queued work rather than net-new:

- **FU-38 backend abstraction** → provides the **model panel** (Gemini CLI +
  OpenRouter harness + claude/codex). The panel work is largely FU-38; the
  benchmark consumes it. The OpenRouter extra is internal-only until toolkit is
  PyPI-able/vendored (D-pkg-12) — fine for fleet-local use.
- **Telemetry feature (queued)** → §8 is the concrete spec for it.
- **Multi-iteration loop (Phase 3.C / FU-32 #2)** → gated on measuring FU-35
  cache hits; the benchmark's cost model wants the same token/cost telemetry, so
  these share instrumentation.
- **toolkit `prompt_regression` + `edit_classifier`** → the LLM-judge oracle and
  edit categorization are already built; the harness wires them up.

---

## 12. Routing policy

**A. Static tier-per-bucket** — lookup from the benchmark table. Simple default.

**B. Cheap-first with escalation** — run the cheapest tier; because we have a
test oracle, detect failure ~for free; retry one tier up on failure. Expected
cost `cost_cheap + p_fail·(cost_detect + cost_retry_high)`; with tests as oracle
`cost_detect ≈ 0`, so B beats static-high whenever the cheap tier isn't terrible.
**Only where failure is cheaply + reliably detectable.**

**Policy (D-bench-7, proposed), shippable before any benchmark:**
- PLAN, REVIEW → **top tier** (blast radius, §4.1).
- EXECUTE, CLOSE, probe → **cheap-first with escalation**.
- Refine the EXECUTE threshold from measured knees as data lands.

---

## 13. Phased plan

1. **Routing v0 (now, no data needed):** D-bench-7 split — top tier for
   PLAN/REVIEW, cheap-first-with-escalation for EXECUTE/CLOSE.
2. **Telemetry (§8) in i2c:** structured devlog fields + `run_iteration`
   logging. Fleet-wide passive data starts accruing.
3. **v0 retrofit:** harvest the ~70 historical i2c steps + e2e labels; first
   (noisy) per-bucket picture; identifies high-variance buckets.
4. **Replay harness on clankercourts (pirozhok):** per-phase oracle (14 units)
   × tiers × k≥3; per-step EXECUTE resolution second.
5. **diplomat migration + dev:** the forward firehose; confirm hermeticity;
   accumulate clean telemetry across 48 phases.
6. **Analysis + knee detection:** set per-bucket minimum-sufficient tiers;
   tighten the router.
7. **Active A/B (optional, later):** deliberate cheap-tier routing on low-blast
   buckets once passive data shows safe zones.

---

## 14. Risks & caveats

- **Oracle contamination (§5)** — impl+tests bundled; mitigated by per-phase
  granularity + reference-tests + LLM-judge.
- **Non-stationarity / selection bias** — model lineup changes monthly; data is
  one-fleet, idiosyncratic per project. Keep the harness cheap to re-run; log
  enough features to stratify; don't over-claim generality.
- **Path/mount artifact (D-bench-4)** — replay on Linux/pirozhok, not laptop
  over Samba.
- **k-variance** — agents are nondeterministic; report pass@1 and pass^k; the
  gap quantifies retry value.
- **Active-experiment cost** — cheap-model failures on real projects cost
  rework; gate active A/B to low-blast buckets + strong oracle.
- **Small N** — ~70 historical EXECUTE units won't fill every bucket; forward
  generation is the fix but accrues at dev speed.

---

## 15. Open questions / decisions to ratify

- **Q-bench-1:** Final tier definitions for the panel (which OpenRouter models =
  T0? Gemini tier placement?). Blocked on FU-38 landing.
- **Q-bench-2:** Per-step reference-tests oracle — is the interface stable enough
  across replays, or do we lean on LLM-judge? Resolve empirically on
  clankercourts.
- **Q-bench-3:** Does diplomat's suite run hermetically + offline? Confirm at
  `i2c import`.
- **Q-bench-4:** Where does the harness live — an i2c subcommand
  (`i2c benchmark …`), a toolkit module, or a separate repo? (Leaning: i2c
  subcommand, since it consumes `.state/` + `assemble` + telemetry.)
- **D-bench-1..8:** proposed above; ratify when telemetry phase is planned.
  (D-bench-8 = clean-room/autonomous-only generation, §7.3.)

---

## 16. References

- Corpus audit raw numbers: §6 (this doc), generated 2026-06-30.
- i2c state/devlog model: `README.md` §"State model", `i2c/run_iteration.py`.
- Backend abstraction: `FOLLOWUPS.md` Active Roadmap §2 (FU-38).
- Oracle building blocks: toolkit `ARCH_prompt_regression.md`,
  `ARCH_edit_classifier.md`.
- Deployment / where replay runs: `rules/deployment.md` (pirozhok).

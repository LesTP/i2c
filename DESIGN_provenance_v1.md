# DESIGN: Prompt Provenance v1

**Status:** Accepted (scoped) - 2026-08-16. See §0 (Decision & scoping) for what
is actually being built, in which order, and what is deferred or folded. §1-§7
below are the original proposal, retained as the design rationale.
**2026-10-04:** Tier 1 shipped (FU-63). Tier 2 (FU-64) waits on the benchmark
replay harness, so it is **paused with the benchmark** (`DESIGN_benchmark_v1.md`).
**Scope:** `assemble_context.py`, `run_iteration.py`, `.state/`, new `i2c provenance` / `i2c replay` / `i2c fork` commands
**Depends on:** existing state model, invariants, recovery subsystem
**Anchored to:** the model-benchmark thread — `DESIGN_benchmark_v1.md` §8
(`prompt_hash` / `start_commit` replay keys) and §9.3 (replay harness).

---

## 0. Decision & scoping (2026-08-16)

Adopted **scoped**, anchored to the benchmark thread rather than as a standalone
5-phase build. The proposal is a rigorous *superset* of replay infrastructure the
benchmark already needs and partially has; only a subset earns its place now.

### Findings that reframe the proposal (verified against code)

- **The assembler is already pure for the five core actions.**
  `tests/test_prompt_golden.py` locks the assembled prompt byte-for-byte across
  every action × backend × mode. The *only* impurity is `render_failure_context`
  (recovery `diagnose`/`reconcile` only — it runs a live git/disk drift audit at
  assemble time; the golden test already skips it inside a git repo). So Phase A's
  "audit for impurity" is largely done and continuously enforced.
- **A per-dispatch prompt fingerprint already exists.** `telemetry.py:105
  prompt_hash()` computes `sha256:…` of the assembled prompt and
  `run_iteration.py` feeds it the real prompt text, so every autonomous iteration
  already records `prompt_hash` in `.state/telemetry.jsonl`. The minimal
  provenance fingerprint is live — what it lacks is a *guarantee it is
  reproducible* (Tier 1) and a recorded *input set* to re-derive from (Tier 2).
- **The manifest/replay pattern already has a working precedent.** The
  frozen-acceptance oracle hashes an artifact into `.state/tests_manifest.json`
  at a commit and recomputes+verifies it at CLOSE (`invariants.py:199`, D-tests-4).
  Provenance's manifest + `replay --check` is the same shape, generalized.
- **The benchmark already depends on this.** `DESIGN_benchmark_v1.md` §8 lists
  `prompt_hash` + `start_commit` as replay keys, and §9.3 defines the replay
  harness as *checkout `start_commit` → re-assemble the prompt → run model X → run
  oracle*. That re-assembly is exactly what hits the four reconstruction problems
  in §1 (per-step-not-per-dispatch granularity, dirty tree, wheel-versioned
  packaged instructions, time-relative devlog tail). It is autonomous-only by
  decree (D-bench-8) and runs on pirozhok (D-bench-4).

### Tiering (what to build, in order)

- **Tier 1 — now-ish (priority `next`): the purity lock.** = Phase A **minus**
  the source-registry refactor. Assert assembler purity as an invariant (it holds
  today but is not asserted): explicit double-assemble byte-identity +
  cwd/clock-independence, and resolve the one impurity by declaring **recovery
  actions out of scope** for `prompt_hash`/provenance. This makes the *already
  recorded* `prompt_hash` a trustworthy replay key. **Phase C (completeness) is
  folded in** as a light assertion — the recipe design already has no "read a file
  and splice it into the prompt" path, so the assemble-time raise is near-vacuous
  here. Tracked as an `i2c fu` (test-hardening). Sibling to FU-50 (trust
  `tests_pass`): this is "trust `prompt_hash`."
- **Tier 2 — deferred, gated on the benchmark replay harness (priority
  `eventually`): manifest + blobs + `i2c replay --check`** (Phases B + D). Build
  these *with* the clankercourts replay harness on pirozhok, because that is when
  you can **measure** whether checkout-and-reassemble is lossy enough to require a
  recorded input set. If `replay --check` against the recorded `prompt_hash`
  passes for historical iterations, blobs may be unnecessary; if it fails, they
  are justified. This is D-3's "measure before optimising" applied at the one
  moment measurement is possible. Tracked as an `i2c fu` (structural-refactor).
- **Tier 3 — deferred: `i2c fork`** (Phase E). Counterfactual fork enables the
  benchmark's *active* A/B routing experiments (D-bench-5 / benchmark §13 step 7),
  which are themselves deferred. Fork rides that work; not separately queued.

### Resolved open questions (from §7)

- **Supervised mode (§7.1):** **autonomous-only; supervised out of scope.**
  Benchmark data is autonomous-only (D-bench-8) and i2c is not self-hosted
  (benchmark §10), so there is no consumer for supervised provenance.
- **Completeness invariant (Phase C / D-4):** **folded into Tier 1**, not a
  separate phase — the property is already structurally true (no splice path).
- **`render_failure_context`:** **excluded from provenance v1** — recovery actions
  are out-of-band and rare, and the golden test already treats their prompt as
  non-deterministic inside a git repo.

### Decisions §5 status under this scoping

D-1 (runner-owned manifest) and D-2 (store hash, not prompt) stand as-is and are
partly realized already (`telemetry.jsonl` is runner-owned; `prompt_hash` stores
the hash). D-3 (store all input bytes as blobs) is **retained but gated** to
Tier 2 / measured. D-4 (completeness at assemble time) is **softened**: folded
into Tier 1 as a light assertion rather than an enforced pre-action raise, since
the single-producer recipe design already provides the guarantee structurally.

---

## 1. Problem

i2c can answer *what happened* in a past iteration. It cannot answer *what the worker saw*.

`devlog.jsonl` records outcomes. `logs/loop/` records runner output and is gitignored. `.state/*.json` records the current state, and every lifecycle write overwrites the previous value — `i2c state set project.json state=execute` destroys the prior value in place. The assembled prompt itself is piped to the backend and discarded.

Git is the de facto history, and it is insufficient for reconstruction, for four reasons:

1. **Granularity.** Commits land per step, not per dispatch. A phase produces PLAN, several EXECUTE, REVIEW and CLOSE dispatches; multiple dispatches share a commit, and some (PLAN, a failed EXECUTE) produce none.
2. **Dirtiness.** The assembler reads working-tree files. If `ARCH_foo.md` was edited but uncommitted at dispatch time, no commit contains what the worker read.
3. **Untracked inputs.** Instruction files resolve project-local-override → packaged default. The packaged defaults live in the installed wheel, versioned by the framework, not by project git. `i2c.toml`, the adapter, and the resolved backend/model are likewise outside the tracked set or outside the repo entirely.
4. **Time-relative selection.** The assembler includes a devlog *tail*. "Last N entries" resolves differently against a longer file. Even with every byte on disk unchanged in git, the same command produces a different prompt later.

Consequence: the question "why did the worker do that on iteration 47" is archaeology, and the experiment "re-run iteration 47 with one line of `instructions/execute.md` changed" is not available at all.

## 2. Non-goals

- **Reproducing worker output.** The model is stochastic and tools mutate the tree. This spec buys identical *inputs*, not identical runs.
- **Event-sourcing `.state/`.** Converting `project.json` to a projection over a transition log is a larger, separate change. This spec is compatible with it but does not require it.
- **Storing conversations.** i2c workers are single-action and ephemeral; there is no long session to compact. That is why the cheap version of this works here and does not in a chat-shaped harness.

## 3. Core concept: the input set

Model the assembler as a pure function:

```
assemble(input_set) -> prompt
```

An **input set** is the complete, enumerable collection of byte sequences that determine the assembled prompt. Every section of the prompt must trace to exactly one declared input. Nothing reaches the worker that is not a member.

The i2c restatement of *model-visible means logged*:

> **Worker-visible means declared.** Any content that reaches an assembled prompt must originate from a registered input source, and the resolved bytes of every source must be recorded at dispatch.

Three invariants follow, enforced at three different times.

| Invariant | Statement | Enforced |
|---|---|---|
| **Completeness** | The concatenation of source-attributed sections equals the emitted prompt, byte for byte, and every source appears in the manifest. | Assemble time — raise |
| **Purity** | `assemble` over a fixed input set yields identical bytes on repeat calls. | Property test in CI |
| **Reproduction** | Re-deriving iteration N from its manifest yields the recorded prompt hash. | Offline, `i2c replay --check` |

Note what is deliberately *absent*: a dispatch-time byte-match. DSH compares the outgoing request against a projection built by different code, so the comparison has content. In i2c the assembler is the only producer of the prompt; comparing its output to itself is vacuous. Completeness at assemble time is the real analogue — it is the check that refuses a write rather than the check that compares two reads.

## 4. Solution

### 4.1 Input source registry

Refactor `assemble_context.py` so each prompt section is produced by a registered source rather than by inline file reads. A source declares:

- `id` — stable logical name (`instructions/execute`, `state/project`, `docs/ARCH_assembler`, `devlog/tail`)
- `resolution` — how the bytes were located (`packaged@<version>`, `project-local`, `derived`)
- `bytes` — the resolved content

For selective sources, resolution must pin the selection, not the rule. `devlog/tail` records the line range actually included, not "last 20". Same for phase-scoped slices of `steps.json` and `decisions.json`.

Adding a new kind of worker-visible content means registering a source. There is no path that reads a file and splices it into the prompt, which is the structural property that prevents context-injection accretion.

### 4.2 Manifest

New state file: `.state/iterations.jsonl`, append-only, one record per dispatch, git-tracked.

```json
{
  "iteration": 47,
  "ts": "2026-08-16T09:14:22Z",
  "phase": 12,
  "action": "EXECUTE",
  "backend": "claude",
  "model": "sonnet",
  "framework_version": "0.9.3",
  "git_head": "a1b2c3d",
  "git_dirty": true,
  "prompt_sha256": "…",
  "inputs": [
    {"id": "instructions/execute", "resolution": "packaged@0.9.3", "sha256": "…"},
    {"id": "state/project",        "resolution": "project-local",  "sha256": "…"},
    {"id": "devlog/tail",          "resolution": "derived",        "sha256": "…",
     "selection": {"from_line": 118, "to_line": 137}}
  ]
}
```

**Written by the runner, never by the worker.** `i2c state` gains no verb for it. A worker cannot forge its own provenance — the separation matters as much as the record.

### 4.3 Blob store

`.state/blobs/<sha256[:2]>/<sha256>` — content-addressed, deduplicated, git-tracked.

Every declared input's bytes are stored. Do not attempt to classify which inputs are recoverable from git or from the wheel; the classification is the part that goes wrong, and the payload is tens of kilobytes of JSON and markdown per iteration with heavy inter-iteration duplication. Measure before optimising.

**The prompt itself is not stored — only its hash.** Storing it would create a second source of truth that can silently disagree with what the assembler produces from the same inputs. The prompt is derived on demand; the hash is a checksum against the derivation, not a substitute for it.

### 4.4 Commands

```bash
i2c provenance show 47              # manifest record, resolved sources, sizes
i2c provenance diff 46 47           # which inputs changed between dispatches
i2c provenance gc --before 30       # drop unreferenced blobs

i2c replay 47                       # re-derive and print the prompt
i2c replay 47 --check               # re-derive, assert prompt_sha256, exit 1 on mismatch
i2c replay --all --check            # CI: verify the whole archive

i2c fork 47 --override instructions/execute=./variant.md
                                    # re-derive with substitution, print or dispatch
```

`i2c fork` dispatches into a scratch git worktree checked out at the recorded `git_head`, so the counterfactual does not touch live state. If `git_dirty` was true at the original dispatch, fork reports reduced fidelity rather than silently proceeding.

### 4.5 Version skew

A framework upgrade can change the assembler and make old manifests non-re-derivable. Report it; do not repair it. `--check` distinguishes three outcomes: match, mismatch under the same `framework_version` (a real bug — the assembler is impure or the blobs are wrong), and mismatch across versions (expected; `--allow-skew` re-derives under the current assembler and prints a diff).

## 5. Decisions

```
D-1: Manifest is runner-owned
Status: Proposed | Priority: Critical
Decision: `.state/iterations.jsonl` is written only by run_iteration.py. No `i2c state` verb exposes it.
Rationale: Provenance asserted by the subject of the audit is not provenance.
Trade-offs: Supervised mode has no runner, so supervised assembles produce no manifest (see Open Questions).
Revisit if: supervised sessions need auditable provenance.
```

```
D-2: Store the prompt hash, not the prompt
Status: Proposed | Priority: Critical
Decision: Manifests record prompt_sha256; the assembled prompt is never persisted.
Rationale: A stored prompt is a second source of truth that can diverge from the assembler.
Trade-offs: Forensics after a breaking assembler change require --allow-skew rather than a direct read.
Revisit if: version skew becomes frequent enough that re-derivation is routinely unavailable.
```

```
D-3: Store all input bytes, content-addressed
Status: Proposed | Priority: Important
Decision: Every declared input is written to `.state/blobs/`, deduplicated by sha256, git-tracked.
Rationale: Recoverability classification (git vs wheel vs neither) is the failure-prone part; blob storage is cheap and unconditional.
Trade-offs: Repository growth. Mitigated by dedup and `provenance gc`.
Revisit if: measured growth exceeds ~50 MB on a real multi-phase project.
```

```
D-4: Enforce completeness at assemble time, reproduction offline
Status: Proposed | Priority: Critical
Decision: The assembler raises if any prompt bytes lack a registered source. No dispatch-time comparison.
Rationale: Only one component produces the prompt, so a dispatch-time byte-match compares a value to itself.
Trade-offs: An impure assembler is caught by CI and by --check, not at the moment of harm.
Revisit if: multiple prompt producers ever exist (e.g. a conversational agent layer).
```

## 6. Implementation

> **Scoping (see §0):** Phase **A** minus the source-registry refactor is
> **Tier 1** (do now); **C** is folded into it. Phases **B + D** are **Tier 2**
> (gated on the benchmark replay harness). Phase **E** is **Tier 3** (deferred,
> rides the benchmark's active A/B). The five-phase decomposition below is the
> implementation reference; the *sequencing/commitment* is §0.

Five phases, each independently useful and independently testable.

**A — Assembler purity.** Route every section through a registered source. Audit for impurity: wall-clock timestamps, absolute paths, `cwd`, environment reads, non-deterministic dict or set ordering, and the time-relative devlog tail. Test: two consecutive assembles over a frozen fixture are byte-identical; the `examples/initial_state/` fixture assembles identically under a stubbed clock and a different working directory. This phase is where the real bugs are, and it delivers value even if the rest is never built.

**B — Manifest and blobs.** Schema in `i2c/data/schemas/iterations.schema.json`. Runner writes the record *before* dispatch, so a crashed iteration still leaves provenance. `i2c provenance show` / `diff`.

**C — Completeness invariant.** Assemble-time raise on unregistered content. Add to `invariants.py` as a pre-action check to sit alongside the existing post-action ones.

**D — Replay.** `i2c replay N --check` and `--all --check` for CI. This is the phase that makes the archive trustworthy rather than merely present.

**E — Fork.** Scratch worktree, `--override`, fidelity reporting on `git_dirty`.

**Regime:** A through D are Build — machine-verifiable, autonomous-loop-safe. E is Build with a Refine tail, since worktree ergonomics need judgement.

**Cost:** a handful of sha256 computations and a few kilobytes of writes per dispatch, against an LLM call costing seconds and cents. The DSH equivalent serialises the full message history twice per dispatch because its unit of work is a turn. i2c's unit is a whole action, so the same discipline costs proportionally far less — this is the payoff of the ephemeral-worker design.

**Interaction with recovery:** `i2c diagnose` gains a real baseline. "Has `.state/` changed since dispatch N" becomes a hash comparison instead of an inference, and drift classification can distinguish worker-caused from human-caused changes.

## 7. Open questions

> **Resolved (see §0):** (1) supervised mode → **autonomous-only, out of scope**;
> (3) blob tracking → **retained but gated to Tier 2 / measured on the replay
> harness**. (2) iteration numbering and (4) retention remain open and are
> Tier-2 concerns — settle them when the manifest is built.

1. **Supervised mode.** There is no runner, so nothing writes the manifest. Options: `i2c assemble` writes a manifest with `backend: "supervised"`; or supervised is explicitly out of scope for provenance. The first is more honest, the second is less code.
2. **Iteration numbering.** Recovery already uses `--target N`. Confirm that identity is stable and monotonic across crashes and out-of-band dispatches before keying manifests on it.
3. **Blob tracking default.** Tracked gives remote backup and a git-visible audit trail; untracked keeps the repo small. Proposal is tracked with `gc`, but this deserves a measurement on a real project first.
4. **Retention.** Should `gc` be automatic past some iteration depth, or always explicit? Automatic deletion of audit material has an obvious failure mode.

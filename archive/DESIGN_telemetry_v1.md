# DESIGN - Telemetry (`.state/telemetry.jsonl`) v1

> **ARCHIVED (2026-07-07)** — historical *why* record; the current *what* lives in
> [`../STATUS.md`](../STATUS.md) §7 (telemetry track) and the live impl/schema
> (`i2c/telemetry.py` + `i2c/data/schemas/telemetry_entry.schema.json`). Deferred
> additive items remain noted below.
>
> **Status:** Implemented (increments 1 + 2). The sidecar, schema, runner
> capture, scaffolding, cost/tier derivation (bundled pricing + `[telemetry.pricing]`
> overrides), and the opt-in `tests_pass` oracle are shipped and tested. Still
> deferred (schema already nullable, no schema change needed): structured
> `review_findings`, exact cache-aware cost, `tool_calls`. Realizes the §8 schema
> of `DESIGN_benchmark_v1.md`. Decisions tagged D-tel-*.
>
> **One-line goal:** persist a schema-validated, git-tracked, per-action
> *execution envelope* (model, cost, tokens, timing, git deltas, oracle signals)
> for every autonomous iteration — so the benchmark (and dashboards, cost
> reports) have clean structured data as a by-product of normal runs.

---

## 1. Why a sidecar, not devlog fields (D-tel-1)

`DESIGN_benchmark_v1.md` §8 sketched one enriched record. Grounding it in the
code shows that must split into two stores:

- **`devlog.jsonl`** is **worker-authored** (`i2c state append`) and its schema
  is **`additionalProperties: false`** with `required: [phase, step, action,
  outcome, summary, timestamp]`. The worker is the wrong author for — and often
  *cannot know* — cost, token counts, wall-clock, tool-call counts, or the
  assembled-prompt hash. A model cannot reliably self-report its own token usage.
- devlog is **append-only history** (a stated invariant). Having the runner
  *patch* the worker's line post-hoc to add envelope fields would violate that.

**Decision (D-tel-1):** add a new **runner-authored sidecar**,
`.state/telemetry.jsonl`, one row per iteration, joinable to devlog. devlog stays
exactly as-is — the worker contract is untouched.

**Corollary (clean-room, ties to benchmark §7.3):** the runner always assembles
`--mode autonomous`, and supervised work never goes through `run_iteration`. So
**a telemetry row exists ⟺ the action ran autonomously.** Presence *is* the
benchmark-eligibility filter; `mode` is carried explicitly for future supervised
harnesses but is constant `"autonomous"` from today's runner.

**Separation of concerns (D-tel-2):** telemetry is **observational**, never
control state. The state machine, invariants, recovery/drift audit, and migration
**must not read it** for any dispatch decision. It is write-only from the runner's
perspective and read-only from analysis tooling.

---

## 2. Field ownership

Most of §8 is runner-side. Ownership and source:

| Field | Owner | Source / how |
|---|---|---|
| `schema_version` | runner | constant `1` (per-row; format can evolve over a long-lived log) |
| `iteration` | runner | `next_iteration_number()` (already computed) |
| `phase` | runner | `current_phase()` (already) |
| `action` | runner | dispatched action (already) |
| `mode` | runner | constant `"autonomous"` (runner uses `--mode autonomous`) |
| `backend` | runner | resolved backend (already) |
| `timestamp` | runner | UTC ISO-8601 (already used for summary.log) |
| `model` | runner | claude: the `--model` value (known). codex: parse from JSONL `session`/`turn` event (best-effort; null if absent — codex gets no `--model` flag) |
| `tier` | runner | `model → tier` lookup (pricing/tier table, §5) |
| `tokens_in` / `tokens_out` / `tokens_cached` | runner | **already computed** — `parse_claude_output` / `parse_codex_usage` normalized `{input, output, cached}` |
| `cost_usd` / `cost_source` | runner (derived) | usage × pricing table (§5); best-effort, null if model unpriced |
| `wall_clock_s` | runner | time the invoke call (new; trivial) |
| `tool_calls` | runner (derived) | count tool/command events in the backend stream; best-effort, null if not parsed |
| `start_commit` / `end_commit` | runner | `git rev-parse HEAD` before/after the worker (new) |
| `prompt_hash` | runner | sha256 of the assembled prompt sent (system+user for claude; full for codex) — runner already writes these to `logs/loop/` |
| `files_touched` / `loc_added` / `loc_removed` | runner (derived) | `git diff --numstat start_commit..end_commit` (new; null/0 for non-committing actions) |
| `regime` / `leaf` | runner (denormalized) | from `phases.json` current-phase record (`leaf` = empty dependencies) |
| `step` / `outcome` | runner (denormalized from devlog tail) | read the devlog line(s) the worker just appended; copy `step` + `outcome` (null if worker logged nothing) |
| `exit_code` | runner | parsed exit signal (already) |
| `tests_pass` / `tests_cmd` | runner (oracle, opt-in) | run configured `[telemetry].test_cmd` after the worker; **off by default** (§6) |
| `drift_flag` | runner | the post-action drift audit **already runs** (`_recovery.audit_state`); record whether any reconcilable drift was found |
| `review_findings` | worker (deferred) | structured must/should/optional counts — needs a worker-contract change; **null in v1** (§7) |

Only `review_findings` needs worker cooperation; everything else the runner
already has or can cheaply derive.

---

## 3. Schema (`i2c/data/schemas/telemetry_entry.schema.json`)

```jsonc
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://i2c.local/schemas/telemetry_entry.schema.json",
  "title": "Telemetry Entry",
  "description": "One row per autonomous iteration in .state/telemetry.jsonl. Observational sidecar; never control state. Join to devlog on end_commit == devlog.commit (committing actions) or (phase, action, iteration).",
  "type": "object",
  "required": ["schema_version", "iteration", "phase", "action", "mode", "backend", "timestamp"],
  "additionalProperties": false,
  "properties": {
    "schema_version": { "type": "integer", "const": 1 },
    "iteration":      { "type": "integer", "minimum": 1 },
    "phase":          { "type": "integer", "minimum": 1 },
    "step":           { "type": ["integer", "null"], "minimum": 1 },
    "action":         { "type": "string", "enum": ["plan","execute","review","close","probe","integration_check","diagnose","reconcile"] },
    "outcome":        { "type": ["string", "null"], "enum": ["complete","partial","blocked","escalate","failed", null] },
    "exit_code":      { "type": ["integer", "null"], "enum": [0, 2, null] },
    "mode":           { "type": "string", "enum": ["autonomous","supervised"] },
    "backend":        { "type": "string", "enum": ["claude","codex"] },
    "model":          { "type": ["string", "null"] },
    "tier":           { "type": ["string", "null"] },
    "timestamp":      { "type": "string", "format": "date-time" },

    "tokens_in":      { "type": ["integer", "null"], "minimum": 0 },
    "tokens_out":     { "type": ["integer", "null"], "minimum": 0 },
    "tokens_cached":  { "type": ["integer", "null"], "minimum": 0 },
    "cost_usd":       { "type": ["number", "null"], "minimum": 0 },
    "cost_source":    { "type": ["string", "null"] },
    "wall_clock_s":   { "type": ["number", "null"], "minimum": 0 },
    "tool_calls":     { "type": ["integer", "null"], "minimum": 0 },

    "start_commit":   { "type": ["string", "null"], "pattern": "^[0-9a-fA-F]{4,40}$" },
    "end_commit":     { "type": ["string", "null"], "pattern": "^[0-9a-fA-F]{4,40}$" },
    "prompt_hash":    { "type": ["string", "null"], "pattern": "^sha256:[0-9a-f]{64}$" },
    "files_touched":  { "type": ["integer", "null"], "minimum": 0 },
    "loc_added":      { "type": ["integer", "null"], "minimum": 0 },
    "loc_removed":    { "type": ["integer", "null"], "minimum": 0 },

    "regime":         { "type": ["string", "null"], "enum": ["build","refine","explore", null] },
    "leaf":           { "type": ["boolean", "null"] },

    "tests_pass":     { "type": ["boolean", "null"] },
    "tests_cmd":      { "type": ["string", "null"] },
    "drift_flag":     { "type": ["boolean", "null"] },

    "review_findings": {
      "type": ["object", "null"],
      "additionalProperties": false,
      "properties": {
        "must":     { "type": "integer", "minimum": 0 },
        "should":   { "type": "integer", "minimum": 0 },
        "optional": { "type": "integer", "minimum": 0 }
      }
    }
  }
}
```

A nullable, mostly-optional schema is deliberate: every derived field is
**best-effort** (§4). A missing backend usage block, an unpriced model, or a
non-committing action must yield `null`, never a failed write.

---

## 4. Write path & code changes

### 4.1 Generalize the validated JSONL append (`i2c/state.py`)

Today `cmd_append` hardcodes `if path.name == "devlog.jsonl"`. Generalize to a
per-line schema map so telemetry (and future JSONL files) validate the same way:

- Add a `JSONL_SCHEMA_BY_FILENAME = {"devlog.jsonl": DEVLOG_ENTRY_SCHEMA,
  "telemetry.jsonl": TELEMETRY_ENTRY_SCHEMA}` lookup (in `validate.py`).
- `cmd_append` uses it instead of the hardcoded name.
- `resolve_state_path`'s `known` set adds `"telemetry.jsonl"`.
- Register `telemetry_entry.schema.json` in `validate.py`
  (`TELEMETRY_ENTRY_SCHEMA` constant, like `DEVLOG_ENTRY_SCHEMA`).

Factor the validate-then-`atomic_append_jsonl` body into a reusable
`state.append_validated_jsonl(path, record, schema_name)` so **both** the CLI
(`i2c state append`) and the runner call one code path.

### 4.2 Runner hooks (`i2c/run_iteration.py`)

The runner already has `usage`, `iteration`, `phase`, `action`, `backend`,
`model`, `exit_code`, the assembled prompt text, and runs the drift audit. Add:

1. `start_commit = git rev-parse HEAD` before invoke; `end_commit` after.
2. `wall_clock_s` around the invoke call.
3. `prompt_hash` = `sha256:` of the prompt sent.
4. `files_touched`/`loc_*` from `git diff --numstat start..end` (when commits differ).
5. `regime`/`leaf` from `phases.json` current record.
6. `cost_usd`/`tier` from the pricing/tier table (§5).
7. `tool_calls`, codex `model` — best-effort stream parsing.
8. denormalize `step`/`outcome` from the devlog line(s) the worker appended:
   snapshot the devlog line count before invoke; any new lines are this worker's;
   take the last new line matching the dispatched `action`.
9. assemble the telemetry row and call `state.append_validated_jsonl`.
10. drift audit already computed → `drift_flag = bool(reconcilable)`.

**Build the row late** (after exit-signal parse + drift audit), at both return
points (the CLOSE-invariant-failure path *and* the normal path), so failed
iterations are still recorded — failures are the most interesting benchmark rows.

### 4.3 Non-fatal rule (D-tel-3)

**Telemetry must never change control flow or the exit code.** Wrap the whole
telemetry block in try/except: on any error (git missing, validation failure,
unwritable file) emit a `NOTE: telemetry skipped (<reason>)` to stderr and
continue with the worker's real exit code. This mirrors the existing non-fatal
drift advisory.

### 4.4 Scaffold & git (`i2c/scaffold.py`)

- `i2c init` seeds an empty `.state/telemetry.jsonl` (like `devlog.jsonl`).
- `telemetry.jsonl` is **git-tracked** (it's the retained dataset). Only
  `logs/loop/` stays gitignored. Note for reviewers: cost/token rows will appear
  in diffs — acceptable, consistent with devlog.

### 4.5 Migration

Purely additive — no existing state file changes. Existing projects need no
`i2c migrate`; `telemetry.jsonl` simply begins on the next autonomous run (the
runner creates it on first append; `resolve_state_path` already tolerates a
not-yet-existing JSONL for `append`). No `schema_version` bump on `project.json`.

---

## 5. Pricing / tier table (D-tel-4)

`cost_usd` and `tier` need a `model → {price_in, price_cached, price_out, tier}`
map. Constraints: **i2c's only runtime dep is `jsonschema`**; it must not depend
on toolkit. Options:

- **(chosen)** a small bundled data file `i2c/data/pricing.json` (per-model
  rates + tier label), overridable by an `[telemetry.pricing]` table in
  `i2c.toml`. Unknown model → `cost_usd: null`, `cost_source: "unpriced"`.
- toolkit's **`cost_accountant`** module has a richer price model; reference it
  as the upgrade path **if/when** the optional toolkit dep lands (D-pkg-12) — do
  not couple now.

**Cost accuracy caveat:** `parse_claude_output` currently folds
`cache_creation_input_tokens` into `input` and keeps only `cache_read` as
`cached`. Cache-creation is priced differently (~1.25×) from fresh and cache-read
(~0.1×). v1 cost is therefore **approximate**. A later refinement: retain the
three buckets (fresh / cache-creation / cache-read) for exact cost. Record
`cost_source` so approximate rows are distinguishable.

---

## 6. The tests oracle (`tests_pass`) — opt-in (D-tel-5)

Running the project suite after every iteration is the benchmark oracle (see
`DESIGN_benchmark_v1.md` §5) but adds wall-clock to every run. v1:

- Off by default. Enabled by `[telemetry] test_cmd = "pytest -q"` in `i2c.toml`
  (+ optional `run_tests = true`).
- When enabled, the runner runs `test_cmd` after the worker commits, records
  `tests_pass` (bool) and `tests_cmd` (string). Failure to *run* tests → `null`,
  never a runner failure (D-tel-3).
- **Self-grading caveat** (benchmark §5): for EXECUTE the worker writes its own
  tests, so `tests_pass` is a *self-test* signal, not the clean per-phase oracle.
  Adequate as a passive signal; the benchmark harness applies the stronger
  oracles separately.

---

## 7. Explicitly out of v1

- **Structured `review_findings`** — needs the REVIEW worker to emit must/should/
  optional counts (an `instructions/review.md` + worker-contract change, possibly
  via the exit signal). Left `null` in v1; tracked as a follow-up. (Interim: the
  benchmark harness can parse counts from the REVIEW devlog `summary` prose.)
- **Exact cache-aware cost** — approximate in v1 (§5).
- **Active model A/B routing** — telemetry is passive-only here; routing policy
  and experiments live in the benchmark/routing work, not this feature.
- **Dashboard** — telemetry is the data source for the planned i2c dashboard
  (TODO “More ideas”), but the UI is separate.

---

## 8. Testing

stdlib `unittest`, consistent with i2c. Seam the git/test-cmd/clock calls like
the existing `claude_invoker`/`codex_invoker` seams so tests stay hermetic:

- `state.append_validated_jsonl` accepts/rejects rows against the new schema
  (valid row; unknown field rejected by `additionalProperties:false`; nullable
  fields omitted OK).
- `cmd_append` now routes `telemetry.jsonl` through the new schema; `devlog.jsonl`
  behavior unchanged (regression).
- Runner: a fake invoker + fake git seam produces a well-formed row at both the
  normal and CLOSE-invariant-failure return points.
- Non-fatal rule: a telemetry write that raises does **not** change the iteration
  exit code (inject a failing append; assert exit code preserved + NOTE emitted).
- Cost: priced model → number; unpriced → null + `cost_source:"unpriced"`.
- `i2c init` seeds an empty `telemetry.jsonl`.

---

## 9. Open questions

- **Q-tel-1:** Join key — is denormalizing `step`/`outcome` from the devlog tail
  (§4.2.8) robust enough, or do we want the runner to pass an explicit
  `iteration` into the worker so devlog rows carry it? (Leaning: tail-read is
  enough for the single-worker model; revisit if the multi-iteration loop lands.)
- **Q-tel-2:** Should `tests_pass` default **on** for designated benchmark-
  generator projects (e.g. diplomat) via their `i2c.toml`, while staying off
  fleet-wide? (Leaning: yes — per-project opt-in.)
- **Q-tel-3:** Pricing table maintenance — bundled `pricing.json` vs require
  `i2c.toml`? Stale prices silently skew cost. (Leaning: bundle a dated default;
  stamp `cost_source` with the table version.)
- **Q-tel-4:** Does this interact with the multi-iteration loop (FU-32 #2)? One
  invocation covering several steps → one telemetry row or several? (Defer;
  current spec is one-row-per-invocation, which the loop will need to revisit.)

---

## 10. Relationship to existing work

- **Realizes** `DESIGN_benchmark_v1.md` §8 (this is its concrete spec); refines
  it: sidecar over enriched-devlog, derived/best-effort cost, deferred
  `review_findings`, constant `mode`.
- **Reuses** the FU-33 usage extraction already in `run_iteration.py`.
- **Mirrors** the existing non-fatal drift-advisory pattern for the
  never-break-the-run rule.
- **Feeds** the benchmark harness, future cost reports, and the i2c dashboard.
- **Backend-abstraction (FU-38)** note: as Gemini/OpenRouter backends land, each
  needs a usage extractor + price-table entries; the field-ownership table (§2)
  is the contract a new backend must satisfy for full telemetry.
```

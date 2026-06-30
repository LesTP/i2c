# DESIGN — Worker backends (Gemini CLI · OpenRouter) v1

> Consolidates the two FU-38 backend tracks. Surface verified against code
> 2026-06-30.
>
> **Status:**
> - **§1 Shared backend architecture** — the contract every backend satisfies.
> - **§2 Gemini agentic-CLI backend (FU-38a)** — spec complete; **implementation
>   shelved** by the operator 2026-06-30. CLI flags unconfirmed (the `gemini`
>   binary isn't installed in the pirozhok `claude-code` container; Node 22 +
>   npm 10 are present, `claude`/`codex` live at `/usr/bin`).
> - **§3 OpenRouter backend (FU-38b)** — **research + plan.** Recommends reusing
>   an existing agent harness over building one. No code yet.
>
> Decisions: D-be-* (shared), D-gem-*, D-or-*.

---

## 1. Shared backend architecture

**An i2c "backend" is an *agent*, not a *model*.** The runner assembles a prompt
and hands it to an **agentic worker** that: reads the prompt, edits source/test
code, runs tests, commits, writes outcomes via `i2c state`, and emits a 2-line
`EXIT: 0|2` / `REASON:` signal. The runner parses only that signal; the real
state lives in `.state/`. `claude` (`claude -p`) and `codex` (`codex exec -`) are
both agentic CLIs of this shape.

**This one distinction (agent vs model) is the whole story of this doc:**

- **Gemini** ships an agentic CLI (`gemini`) → a new backend is "shell out to
  another agent," mirroring codex. Small. (§2)
- **OpenRouter** is a *model API aggregator*, **not an agent** → a backend must
  *supply the agency* (a tool-using harness) or *borrow* one. Large/different.
  (§3)

Per-action backend resolution already exists: `[run.backends].<action>` →
backend, falling back to `[run].backend`, overridable by `--backend`
(`config._BACKENDS`, `_RUN_ACTIONS`; resolved in `run_iteration`).

### 1.1 Common surface — every **CLI** backend (verified 2026-06-30)

A new CLI backend `X` touches exactly these, mirroring `claude`/`codex`:

| File | Change |
|---|---|
| `i2c/config.py` | `_BACKENDS += "X"` (line 24) — makes `[run].backend`/`[run.backends]` validate (lines 102/127) |
| `i2c/assemble_context.py` | `adapter_path()` (155) 3-way map → `X.md`; `_tool_rules` heading (984) → `"X-Specific Tool Rules"`; `--backend` choices (1313) |
| `i2c/run_iteration.py` | `invoke_X()` + `parse_X_*` usage; `X_invoker` test seam; dispatch branch; validation `not in (...)` (559); `--backend` choices (788); telemetry `model_used` handling |
| `i2c/scaffold.py` | `_ADAPTER_TARGET += {"X": "X.md"}` (26) |
| `i2c/cli.py` | `--backend` choices for `run` (547) and `init` (594) + `both` expansion (261) |
| `i2c/doctor.py` | `_check_backends()` (193) probes `X` on PATH |
| `i2c/data/adapters/X.md` | **new** packaged adapter (`## X-Specific Tool Rules` + 2-line exit contract) |
| `i2c/data/pricing.json` | `X` model rates + tier (telemetry cost/tier) |
| `tests/test_prompt_golden.py` | `BACKENDS += "X"`; regen goldens (needs an `X.md` fixture) |
| README / docs | backend list + **login-shell PATH** note (binary on the runner's PATH; if the agent runs tool commands in a login shell like codex's `bash -lc`, `i2c` must be on *its* PATH too) |

An **in-house harness** backend (§3 Option A) does **not** fit this table — it adds
harness code *and a Python dependency* instead of shelling out.

### 1.2 Shared decisions

- **D-be-1:** backends are agents. A backend either *is* an agentic CLI or
  *provides* a harness; the runner contract (prompt in, 2-line signal out) is
  invariant.
- **D-be-2:** auth/credentials are **out-of-band on the host**, never in i2c —
  true for claude/codex today, and for both new backends.
- **D-be-3 (cost basis):** under subscription/free auth there's no per-token
  billing, but telemetry records **notional API-rate cost** (from `pricing.json`,
  stamped via `cost_source`) so the benchmark compares models apples-to-apples.
  Actual spend is an operator fact, not a per-iteration metric.

---

## 2. Gemini agentic-CLI backend (FU-38a) — spec complete, impl shelved

A standard CLI backend (§1.1), modeled on **codex** (single full prompt; no
claude FU-35 system-prompt split). Detail:

- **Auth-agnostic (D-gem-3).** `invoke_gemini` shells out to the same `gemini`
  binary regardless of auth; credentials are configured on pirozhok and
  interchangeable with zero code change:

  | Mode | Host config | Billing |
  |---|---|---|
  | OAuth login — **free** | one-time `gemini` login; creds cache, headless after | free, rate/daily-capped |
  | OAuth — **AI Pro/Ultra** | same login, subscribed account | flat fee, higher caps |
  | **API key** | `GEMINI_API_KEY`/`GOOGLE_API_KEY` | metered (expensive for agentic) |

  Free → Pro is a re-login, not a code change. **Throttle reality:** diplomat's
  `TUNING_LOG_archive.md` shows the Gemini *API* free tier 429-ing after ~3
  rounds — a *different, stingier* pool than the *CLI OAuth* tier this backend
  uses, so a yellow flag, not a prediction.

- **Invocation (D-gem-2), confirm flags on the Pi (Q-gem-1):**
  `gemini --yolo --output-format json [-m <model>]`, prompt piped on **stdin**
  (avoid ARG_MAX); `--yolo`/`--approval-mode` = auto-approve; the 2-line exit
  signal parses from the agent's final message (codex-style).
- **Usage/model (D-gem-4):** best-effort from `--output-format json` →
  normalized `{input,output,cached}`; null if absent.
- **Rate-limit handling (D-gem-5):** the CLI self-retries; on a hard cap, detect
  the throttle signature (`RESOURCE_EXHAUSTED`/`429`/`quota`) and surface
  `"throttled — retryable"` instead of a generic halt. No auto-retry in v1.
- **Single-model limitation (Q-gem-model):** one `[run].model` can't serve a
  mixed claude+gemini project; v1 = gemini-only projects (set
  `[run].model = "gemini-2.5-pro"`); mixing needs per-action model (deferred).
- **Open (confirm on the Pi):** Q-gem-1 (flags), Q-gem-2 (does the gemini agent
  run tools in a login shell → `i2c` PATH), Q-gem-retry, Q-gem-cost.

**Shelved state:** `gemini` not installed in the container; Node 22/npm 10 ready.
To resume: `npm i -g @google/gemini-cli` *(confirm package)*, then `gemini --help`
(read-only, no login) to settle Q-gem-1, then implement per §1.1.

---

## 3. OpenRouter backend (FU-38b) — research + plan

### 3.1 The architectural reality (decisive)

OpenRouter is a **model API aggregator** — one OpenAI-compatible endpoint
(`https://openrouter.ai/api/v1`) routing to many models — **not an agent.**
Confirmed: toolkit's `OpenRouterProvider` (subclass of `OpenAIProvider`) and all
of `llm_client` are **completion-only** —
`call(model, system_prompt, user_prompt, max_tokens, ...) → text`. **No tool /
function calling, no file / shell / git access.**

So, unlike Gemini, **there is no "OpenRouter agent" to shell out to.** An
OpenRouter backend must **supply the agency** — the read/write/patch/shell/test/
git/`i2c state` tool loop that claude/codex/gemini give for free.

**It's fragile to do naively.** diplomat's `TUNING_LOG.md` already hit
OpenRouter's heterogeneity on the *completion* path: reasoning models (DeepSeek
R1, Qwen3) return answers in `reasoning`/`reasoning_content` rather than
`content`, which hung toolkit's retry loop (the "R1 hang"). Tool-call *formats*
vary even more across OpenRouter's backends. A hand-rolled agent must absorb all
of that, per model.

### 3.2 Three ways to get an OpenRouter model panel

| | **A. In-house harness** | **B. Dedicated agent CLI → OpenRouter** | **C. Reuse codex → OpenRouter** |
|---|---|---|---|
| What | i2c builds a tool-using loop over `llm_client` | a flexible OSS agent CLI (aider/opencode/OpenHands/…) pointed at OpenRouter, used as a §1.1 CLI backend | configure the **existing** codex backend's custom provider → OpenRouter |
| i2c code | **large** (new harness) | small (one more CLI backend) | **~none** (host config) |
| Python dep | **+toolkit/openai** (`i2c[openrouter]`, D-pkg-12) | none (external binary) | none |
| Agency | rebuilt from scratch | the chosen CLI's (proven) | codex's (proven) |
| Model coverage | any OpenRouter model | broad (CLI-dependent) | models codex's tool protocol can drive |
| Risk | high (patches, sandboxing, heterogeneity, turn/context mgmt) | medium (CLI quirks/trust) | low, but narrower |

### 3.3 Recommendation (D-or-2) — Option C **blocked on codex 0.124** (smoke 2026-06-30); re-ranked

**Prefer reuse over rebuild** — but the cheapest reuse (C) is **blocked** (§3.4
smoke): codex 0.124 dropped `wire_api="chat"` and requires the OpenAI **Responses
API**, which OpenRouter (Chat-Completions-native) doesn't serve. Current ranking:

1. **B — preferred now.** A chat-completions agent CLI (aider / opencode /
   OpenHands / …) pointed at OpenRouter, adopted as a §1.1 CLI backend. Reuses a
   proven loop; no in-house harness; no Python dep.
2. **A — guaranteed-works fallback.** In-house harness over toolkit's
   `OpenRouterProvider`, which *does* speak Chat Completions to OpenRouter
   (diplomat-proven) — but it's the big build + toolkit dep (`i2c[openrouter]`,
   D-pkg-12); breaks i2c's single-runtime-dep cleanliness; internal-only.
3. **C — salvage only.** Revive iff OpenRouter adds a Responses endpoint, via a
   Responses↔Chat proxy (LiteLLM), or an older codex with `wire_api="chat"`
   (conflicts with the 0.124 bot).

### 3.4 Plan — Option C (BLOCKED; salvage path)

**Pi check (read-only, 2026-06-30):** container has **codex-cli `0.124.0`**;
`~/.codex` exists but holds **no `config.toml`/provider yet** (clean slate);
`codex --help` confirms **`-c, --config <key=value>`** dotted-path overrides
(`-c model="o3"` is the documented example). So model selection and config are
settable per invocation — the remaining unknown is only the custom *endpoint*.

**Smoke result (2026-06-30) — BLOCKED.** A live `codex exec` against a custom
OpenRouter provider was rejected: codex 0.124 errors *`wire_api = "chat"` is no
longer supported* (use `responses`), and `wire_api = "responses"` then **hung**
(OpenRouter doesn't serve the Responses API). codex *does* parse the custom
`model_providers` config and `-c` overrides — the wire mismatch is the blocker.
The plan below is therefore a **salvage path**, viable only if C is unblocked
(§3.3); otherwise use Option B.

- **Host (pirozhok):** add a codex `~/.codex/config.toml` `model_providers.openrouter`
  entry (`base_url = "https://openrouter.ai/api/v1"`, env key `OPENROUTER_API_KEY`)
  + select it, then `i2c run --backend codex` with an OpenRouter model id.
  *(Q-or-codex — narrowed: the `-c`/config mechanism and model selection are
  confirmed on 0.124; still verify the `model_providers` + `base_url` custom
  OpenAI-compatible endpoint works end-to-end against OpenRouter.)*
- **i2c code (small, for benchmark attribution):** today the codex backend passes
  **no** model flag (codex is config-driven), so telemetry `model` is **null**.
  **Preferred fix (now confirmed available):** have `invoke_codex` pass a
  per-invocation **`-c model=<id>`** override so the runner sets — and records —
  the model. (Cleaner than parsing the JSONL session event.) *(Q-or-model — still
  the per-action-model gap: one `[run].model` can't serve mixed backends.)*
- **Pricing:** add OpenRouter model rates to `pricing.json` (OpenRouter publishes
  per-model prices) → cost/tier in telemetry.
- **Coverage caveat:** codex's tool protocol is tuned for certain models; restrict
  the panel to tool-capable models; the benchmark itself reveals which OpenRouter
  models drive codex acceptably (Q-or-coverage).

This gets an OpenRouter panel into the benchmark with near-zero new i2c surface.

### 3.5 Plan — Option A, scoped (only if chosen)

If an in-house harness is later justified, it is **not** a §1.1 add; it's a build:

- **Tool schema:** `read_file`, `write_file`/`apply_patch`, `run_shell`,
  `run_tests`, `git_commit`, `i2c_state`, `finish` (emits the 2-line signal).
- **Agent loop:** ReAct/tool-call loop over `llm_client` (or extend `llm_client`
  with OpenAI `tools=`), with a turn + cost budget.
- **Heterogeneity handling:** read `content` *and* `reasoning`/`reasoning_content`
  (the R1-hang lesson); tolerate per-backend tool-format variance.
- **Safety:** shell sandboxing, patch validation, prompt-injection from tool
  output, partial-edit recovery.
- **Packaging:** new dep → `i2c[openrouter]` optional extra, internal-only until
  toolkit is PyPI-able/vendored (D-pkg-12).

Surface-wise it still plugs into §1.1 for *dispatch* (a `backend="openrouter"`
arm), but the bulk is the harness module + dep. Treat as its own DESIGN if pursued.

### 3.6 Decisions & open questions

- **D-or-1:** an OpenRouter backend must supply agency (OpenRouter is an API, not
  an agent; `llm_client` is completion-only).
- **D-or-2:** prefer reuse over rebuild, but **Option C is blocked on codex
  0.124** (responses-only wire vs OpenRouter chat; smoke 2026-06-30). Re-ranked:
  **B** (chat-completions agent CLI → OpenRouter) preferred, **A** (in-house
  harness, works via toolkit chat) fallback, **C** salvage-only.
- **D-or-3:** the in-house harness (A) is gated on the toolkit dep (D-pkg-12) +
  `i2c[openrouter]` extra; internal-only; its own DESIGN.
- **Q-or-codex:** confirm codex supports custom OpenAI-compatible providers
  (`model_providers` + base_url) and how it takes the model id.
- **Q-or-model:** per-action model (shared with Q-gem-model) — needed for
  per-model benchmark attribution and mixed-backend projects.
- **Q-or-coverage:** which OpenRouter models drive codex's (or a chosen CLI's)
  tool protocol acceptably?

---

## 4. Relationship to existing work

- **FU-38:** (a) Gemini = §2; (b) OpenRouter = §3; (c) the toolkit-dep call
  (D-pkg-12) is the gate for §3 Option A.
- **Benchmark (`DESIGN_benchmark_v1.md`):** both backends widen the model panel;
  telemetry (increment 2) already records tokens/cost/tier. Per-model attribution
  needs Q-or-model / Q-gem-model resolved.
- **Packaging (`DESIGN_packaging_v1.md`):** CLI backends keep i2c's
  single-runtime-dep cleanliness; the in-house OpenRouter harness would break it
  (hence internal-only extra).
- **Precedent:** the lowest-risk shape throughout is "reuse an existing agent" —
  Gemini *is* one; OpenRouter should *borrow* one (codex) before building one.

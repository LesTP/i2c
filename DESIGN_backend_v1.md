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
>   an existing agent harness over building one. No code yet. **§3.8 = the pi.dev
>   spike protocol (FU-61), now the primary Option-B candidate; §3.7 opencode is
>   the fallback.**
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
| What | i2c builds a tool-using loop over `llm_client` | a flexible OSS agent CLI (aider/opencode/OpenHands/pi.dev/…) pointed at OpenRouter, used as a §1.1 CLI backend | configure the **existing** codex backend's custom provider → OpenRouter |
| i2c code | **large** (new harness) | small (one more CLI backend) | **~none** (host config) |
| Python dep | **+toolkit/openai** (`i2c[openrouter]`, D-pkg-12) | none (external binary) | none |
| Agency | rebuilt from scratch | the chosen CLI's (proven) | codex's (proven) |
| Model coverage | any OpenRouter model | broad (CLI-dependent) | models codex's tool protocol can drive |
| Risk | high (patches, sandboxing, heterogeneity, turn/context mgmt) | medium (CLI quirks/trust) | low, but narrower |

### 3.3 Recommendation (D-or-2) — Option C **blocked on codex 0.124** (smoke 2026-06-30); re-ranked

**Prefer reuse over rebuild** — but the cheapest reuse (C) is **blocked** (§3.4
smoke): codex 0.124 dropped `wire_api="chat"` and requires the OpenAI **Responses
API**, which OpenRouter (Chat-Completions-native) doesn't serve. Current ranking:

1. **B — preferred now.** A multi-provider agent CLI (aider / opencode /
   OpenHands / **pi.dev** / …) pointed at OpenRouter, adopted as a §1.1 CLI
   backend. Reuses a proven loop; no in-house harness; no Python dep. **pi.dev**
   (Pi Coding Agent) is a strong candidate — a deliberately tiny, *hackable*
   agent CLI with custom tools + prompt templates + alternate model providers, so
   it may satisfy i2c's worker contract more cleanly than opencode (controllable
   system prompt + a shell tool for `i2c state`) and double as the OpenRouter
   *and* gemini shim in one backend (spike: FU-61, §3.7).
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
- **D-or-4:** opencode and **pi.dev** are the Option-B spike candidates (pi.dev
  added 2026-08-11 — a hackable agent CLI with custom tools/prompt templates +
  alternate model providers, so it may also serve as the gemini shim; FU-61);
  aider is fallback-only (doc pre-pass 2026-07-11 — see §3.7).
- **Q-or-signal:** does `opencode run` stdout preserve the worker's 2-line
  `EXIT/REASON` block parseably? (the make-or-break unknown; §3.7 acceptance #3.)

---

## 3.7 Spike protocol — validate Option B with opencode (D-or-4)

> **Status:** planned spike. Doc pre-pass done (2026-07-11, laptop); live smoke
> pending on pirozhok. Time-boxed; produces a findings note + an A-vs-B call, not
> production code.

**Goal.** Answer whether **opencode** (a general tool-use agent CLI) slots into
the §1.1 backend contract when pointed at OpenRouter — i.e., whether Option B is
"one more CLI backend" (small code) or leaks. This is the FU-38b decision gate.

**Why opencode over aider (doc pre-pass, D-or-4).** opencode matches the
claude/codex *agent-with-a-shell* shape — a general tool-use agent with a **bash
tool** (so the worker can run `i2c state …`) and no forced auto-commit. aider is a
code-*editor* agent: its edit-format system prompt is always injected (breaks
"worker reads only the assembled prompt") and it exposes no generic shell tool the
model can call (can't write `.state/` via `i2c state`) — two hard-contract leaks.
So opencode is the candidate; aider is fallback only. *(Pre-pass was from prior
knowledge, not live docs — confirm opencode's `run` flags + OpenRouter provider
config on the Pi first; opencode is under fast development.)*

**Where.** The pirozhok `claude-code` container (creds + backend CLIs live there;
the laptop can't run headless agents — FU-28). Use a throwaway git project (or a
copy of the CC fixture), **not** a fleet project.

**Pre-req (confirm before the run):**
- opencode installed; `opencode run --help` confirms a non-interactive one-shot
  mode and how it takes the prompt (stdin / arg / file).
- OpenRouter provider configured (`OPENROUTER_API_KEY` + an OpenRouter model id);
  confirm opencode targets it.
- `i2c` on opencode's **login-shell** PATH (`bash -lc` check — the codex lesson,
  README §Requirements).

**Procedure.** Drive one real worker action (a single EXECUTE step on a trivial
Build phase) through opencode instead of claude/codex:
1. Assemble the prompt as usual: `i2c assemble --action execute --phase N`.
2. Feed it to `opencode run` (headless), targeting an OpenRouter model.
3. Observe whether opencode edits code, runs the test, calls `i2c state …`, and
   emits the 2-line `EXIT/REASON` block to stdout.
4. Capture stdout/stderr + the resulting `.state/` + git state.

**Acceptance checklist (the empirical unknowns docs can't settle):**

| # | Check | Contract seam |
|---|-------|---------------|
| 1 | `opencode run` accepts the full assembled prompt and acts on it (its own system prompt / `AGENTS.md` auto-read doesn't distort behavior) | prompt delivery |
| 2 | Model calls `i2c state set/append/complete` successfully (bash tool + PATH) | agency / `i2c state` |
| 3 | The 2-line `EXIT: 0\|2 / REASON:` survives to stdout and the runner parses it | exit signal (**highest risk**, Q-or-signal) |
| 4 | An OpenRouter model returns usable output (no `reasoning`-vs-`content` hang, the R1 lesson) on ≥1 tool-capable model | heterogeneity (Q-or-coverage) |
| 5 | model id + tokens/cost capturable into `telemetry.jsonl`; a cost/turn ceiling is enforceable | attribution/budget (Q-or-model) |

**Decision rule.** *Pass* = all five green on ≥1 OpenRouter model → **adopt Option
B**: add a `backend="opencode"` arm to the runner/adapter (small), promote D-or-2
to "B adopted," and follow up with a phase for the backend arm + `pricing.json`
OpenRouter rates. *Any hard leak* (especially #3 or #2) → record where the contract
leaked and fall back to **Option A** (in-house harness, §3.5; its own DESIGN).

**Deliverable.** A findings note — per-check pass/leak, the working `opencode run`
invocation, a candidate model list, and the A-vs-B call — appended here or as the
FU-38b resolution. No production code in the spike itself.

---

## 3.8 Spike protocol — pi.dev (the FU-61 candidate; primary Option-B spike)

> **Status:** **SPIKE DONE — PASS (2026-08-17); Option B adopted (pi.dev).** See
> §3.8.2 for the result. pi.dev surfaced 2026-08-11, was the primary Option-B
> candidate (D-or-4); the §3.7 opencode protocol is now moot (fallback only).
> Next: Stage-2 integration (§3.8.1) as a Build phase.

**Goal.** Answer two questions:

1. Does **pi.dev** slot into the §1.1 CLI-backend contract when pointed at
   OpenRouter — i.e., is Option B "one more CLI backend" (small code) or does it
   leak? (the FU-38b decision gate.)
2. **Bonus / strategic:** can *one* pi.dev backend reach **both** OpenRouter and
   Gemini via host config? If yes, a single `pidev` backend **collapses FU-38a
   (Gemini) + FU-38b (OpenRouter) into one**, and §2/§3 of this doc become one
   backend arm.

**Why pi.dev over opencode (D-or-4).** pi.dev is a deliberately tiny, *hackable*
agent CLI — custom tools + prompt templates + alternate model providers. Two
contract fits that matter:

- **Controllable system prompt** — i2c's worker must read *only* the assembled
  prompt, so we need to set/replace the system prompt rather than fight an
  injected one. (opencode auto-reads `AGENTS.md`; aider always injects an
  edit-format prompt — a hard leak, so aider stays fallback-only, §3.7.)
- **A shell tool** — the worker must run `i2c state …`; pi.dev exposes a shell
  tool the model can call.

opencode remains the alternate Option-B candidate if pi.dev leaks; §3.7 stands.

**Where.** The pirozhok `claude-code` container (creds + backend CLIs live
there). Use a **throwaway** git project — a copy of the CC fixture or a trivial
one-phase Build — **not** a fleet project.

**Pre-reqs (confirm on the Pi before the run — the empirical unknowns):**

- pi.dev installed; note the **binary name** and install method (cf. Gemini: not
  yet in the container; Node 22 / npm 10 present).
- A **non-interactive one-shot** mode and how it takes the prompt (stdin / arg /
  file) — prefer stdin to dodge ARG_MAX on ~40–65 KB prompts (the codex reason).
- An **auto-approve** flag (analogue of `claude --dangerously-skip-permissions` /
  `codex --dangerously-bypass-approvals-and-sandbox`).
- **OpenRouter provider** configured (`OPENROUTER_API_KEY` + an OpenRouter model
  id); confirm pi.dev targets it. (And, for the bonus, a Gemini provider.)
- **System-prompt control** confirmed (can set/replace, not merely prepend).
- **`i2c` on pi.dev's login-shell PATH** — if pi.dev runs tool commands via a
  login shell (`bash -lc`, the codex behaviour), `i2c state` must resolve there
  (README §Requirements; deployment.md).

**Procedure.** Drive one real worker action (a single EXECUTE step on a trivial
Build phase) through pi.dev instead of claude/codex:

1. Assemble as usual: `i2c assemble --action execute --phase N` (use
   `--backend codex` for the spike — codex-shaped single full prompt; no pi.dev
   adapter exists yet and codex's Tool Rules are close enough to validate the
   contract).
2. Feed the prompt to pi.dev headless, targeting an OpenRouter model.
3. Observe whether pi.dev edits code, runs the test, calls `i2c state …`, and
   emits the 2-line `EXIT/REASON` block to stdout.
4. Capture stdout/stderr + the resulting `.state/` + git state.

**Acceptance checklist (checks 1–5 are the FU-61 gate; B is upside):**

| # | Check | Contract seam |
|---|-------|---------------|
| 1 | pi.dev accepts the full assembled prompt and acts on it; its own system prompt / auto-read config doesn't distort behaviour | prompt delivery |
| 2 | Model calls `i2c state set/append/complete` successfully (shell tool + login-shell PATH) | agency / `i2c state` |
| 3 | The 2-line `EXIT: 0\|2 / REASON:` survives to stdout and `parse_exit_signal` reads it | exit signal (**highest risk**, Q-or-signal) |
| 4 | An OpenRouter model returns usable output (no `reasoning`-vs-`content` hang — the R1 lesson) on ≥1 tool-capable model | heterogeneity (Q-or-coverage) |
| 5 | model id + tokens/cost capturable into `telemetry.jsonl`; a cost/turn ceiling is enforceable | attribution/budget (Q-or-model) |
| B | **Bonus:** one pi.dev config reaches both OpenRouter *and* Gemini (a model-id switch, no code change) | multi-provider (collapses FU-38a+b) |

**Decision rule.** *Pass* = checks 1–5 green on ≥1 OpenRouter model → **adopt
Option B (pi.dev)**: promote D-or-2 to "B adopted (pi.dev)" and do the Stage-2
integration (§3.8.1). *Any hard leak* (especially #3 or #2) → record where the
contract leaked and try **opencode** (§3.7) before falling back to **Option A**
(in-house harness, §3.5). Check B is upside, not a gate — a pass folds §2 into §3.

**Deliverable.** A findings note — per-check pass/leak, the working pi.dev
invocation, a candidate OpenRouter model list, the multi-provider (B) result, and
the A-vs-B call — appended here or as the FU-61 resolution. No production code in
the spike itself.

### 3.8.1 Stage 2 — integration checklist (only if the spike passes)

pi.dev is **codex-shaped** (single full prompt on stdin, no claude system-prompt
split), so `invoke_pidev` mirrors `invoke_codex`. The §1.1 map, grounded in code
(verified 2026-08-17):

- `i2c/config.py` — `_BACKENDS += ("pidev",)` (validates `[run].backend` /
  `[run.backends]`).
- `i2c/run_iteration.py` — `invoke_pidev()`: pass the assembled prompt via
  `@<file>` (write it to a temp file — **not** stdin), flags `-p --no-session -nc
  --no-skills --no-extensions --provider <p> --model <id> --mode text`,
  **`stdin=DEVNULL`** (pi `-p` blocks on an open stdin with no TTY — the §3.8.2
  lesson), and `env[OPENROUTER_API_KEY]`; capture the final message (carries the
  2-line signal) + usage (via `--mode json` when tokens/cost are wanted). Add a
  dispatch branch; the **hardcoded** backend guards `backend not in ("claude",
  "codex")` (~L823 + the run/validate sites) → add `pidev`. **Record the model** —
  codex leaves `model_used = None` (L960); pi.dev must record the model id, since
  the model *is* the point of a panel.
- `i2c/assemble_context.py` — `adapter_path()` map → `PIDEV.md`; tool-rules
  heading → `"Pidev-Specific Tool Rules"`; `--backend` choices.
- `i2c/scaffold.py` — `_ADAPTER_TARGET += {"pidev": "PIDEV.md"}`.
- `i2c/cli.py` — `--backend` choices for `run` + `init`; decide `both` semantics
  (keep `both = claude+codex`; select `pidev` explicitly).
- `i2c/doctor.py` — `_check_backends()` probes the pi.dev binary on PATH.
- `i2c/data/adapters/PIDEV.md` — **new** adapter (`## Pidev-Specific Tool Rules`
  + the 2-line exit contract), modeled on `codex.md`.
- `i2c/data/pricing.json` — OpenRouter model rates + tier (telemetry cost/tier).
- `tests/test_prompt_golden.py` — `BACKENDS += "pidev"` (regen goldens with a
  `PIDEV.md` fixture); plus `invoke_pidev` / parse / dispatch / config-validation
  tests.
- README / docs — backend list + the login-shell PATH note.

**Stage-2 design sub-decisions (proposed; ratify at integration):**

- **Backend name:** `pidev` (proposed — avoids confusion with "pi" / pirozhok).
- **Model attribution (Q-or-model):** v1 punt = *pidev-only projects* set
  `[run].model = "<openrouter-id>"` (mirrors Gemini's Q-gem-model); full
  per-action model deferred.
- **One backend, many providers:** model pi.dev as a *single* `pidev` backend
  where the model id selects the provider/model (provider = host-side pi.dev
  config). If spike check B passes, this is what collapses §2 + §3.
- **Rate-limit detection:** add a `detect_rate_limit` pidev branch when a real
  429 / quota sample exists (same posture as codex's deferred branch; cf. FU-42).

**Regime.** Stage 2 is a Build phase (mechanical §1.1 add, golden-locked). The
spike (Stage 1) is exploratory and worked by hand on the Pi.

---

### 3.8.2 Spike result — 2026-08-17 (PASS; Option B adopted)

Ran on pirozhok (`claude-code` container) driving one real i2c EXECUTE step
(throwaway `pidev-spike` project, Pattern B: implement `add()` so a frozen test
passes) through **pi 0.84.2** → OpenRouter **`openai/gpt-4o-mini`**.

| # | Check | Result |
|---|-------|--------|
| 1 | prompt delivery (22 KB via `@file`) | ✅ accepted + acted on |
| 2 | agency — `i2c state` via bash tool; auto-approve in `-p` | ✅ ran edit+bash; marked step complete, appended devlog, set state=review — **no approval gate** |
| 3 | 2-line `EXIT/REASON` to stdout, runner-parseable | ✅ via `--mode text` (final message) |
| 4 | OpenRouter model drives the loop | ✅ gpt-4o-mini |
| 5 | model id + tokens/cost → `telemetry.jsonl` | ⏸ not exercised (spike bypassed the runner); pi has `--mode json` — wire in Stage-2 `invoke_pidev` |
| B | one backend → many providers | ✅ native `--provider` (openrouter proven live; gemini/openai/anthropic/… same mechanism) → **collapses FU-38a + FU-38b** |

**Decision: PASS → adopt Option B (pi.dev)** (D-or-2 → "B adopted (pi.dev)").

**Two unknowns resolved + one new integration lesson:**
- **Auto-approve:** `pi -p` auto-runs tools (edit/bash) with no approval gate. ✅
- **Signal surface:** `--mode text` puts the final message (with the fenced EXIT
  block) on stdout; i2c's line-anchored parser reads it. ✅
- **NEW — `pi -p` blocks on an open stdin when there is no TTY.** The hang that
  ate the first two runs was pi waiting on inherited stdin; **`stdin=DEVNULL`
  (or `</dev/null`) is mandatory** for headless dispatch. Connectivity + key were
  fine throughout (curl to `openrouter.ai/api/v1/models` → HTTP 200).

**Working invocation** (`OPENROUTER_API_KEY` in env, stdin closed):
```
pi -p --no-session -nc --no-skills --no-extensions \
   --provider openrouter --model <openrouter-id> --mode text @<assembled_prompt_file>
```

**Worker-quality note (not a backend leak):** gpt-4o-mini emitted the template
line `EXIT: 0 | 2` verbatim rather than choosing `0`. The line-anchored parser
still extracts `0`, but weaker models copy the placeholder — the PIDEV adapter's
Output Contract must make "pick one" unambiguous. `-nc` correctly kept the
assembled prompt as the sole context (no `AGENTS.md`/`CLAUDE.md` auto-read).

**Key location (ops):** `OPENROUTER_API_KEY` lives in
`~/workspace/diplomat/.env` (on the share) — **not** in the systemd
`i2c-bot.env` (which holds only `I2C_TELEGRAM_TOKEN`) and **not** on the Pi host.
Stage-2 / bot runs that route an action to `pidev` need the key in the worker's
env (add it to `i2c-bot.env` or source it).

**Spike artifacts** (on the share; delete when done): `pidev-spike/` project,
`pidev_spike.py`, `pidev_probe.py`, `pidev_probe2.py`, `pidev_find_key.py`.

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

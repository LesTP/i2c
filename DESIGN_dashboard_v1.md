# DESIGN — Portfolio Dashboard (read-only web view) v1

> **Status:** Draft / proposed (spec only; no code). A **read-only**, browser-
> viewable view over the i2c portfolio, built as a thin adapter over
> `i2c.control` + `.state/*.json` + `telemetry.jsonl` + `i2c doctor`. Confirmed
> scope (operator, 2026-07-01): read-only; static-first; LAN-sufficient now, but
> engineered so a **future remote user** is an additive extension. Waymark is
> deferred indefinitely (this subsumes its read-only scope). Decisions: D-dash-*.

---

## 1. Purpose & scope

A visual, portfolio-wide, cross-platform (any browser) surface answering:
*what projects exist, what state each is in, what's driving what, what it's
costing, and is my setup healthy.* It doubles as a **conceptual aid** — a
picture of "what's happening" for the operator today and a hypothetical future
user later.

**In scope:** read-only views of portfolio state, per-project detail, telemetry,
health, and deployment topology.

**Explicitly NOT in scope (v1):**
- **Control** (run/batch/reconcile) — stays with the Telegram bot / CLI (the
  one-poller-safe drivers). The web view is never a loop driver.
- **Config editing** — a future *write* surface, deliberately deferred.
- **Onboarding prose** (install / organize / what-runs-where narrative) — that's
  docs (`README`, `deployment.md`) + `i2c doctor`. The dashboard *renders* health
  and topology; it does not host the setup tutorial.

---

## 2. The guiding split (why this stays safe as it scales)

**Read = web (portable, remote-capable). Control = local/trusted (CLI, Telegram,
or a future VS Code plugin).**

This split is the backbone: because the web view *only reads*, it can safely aim
at "any user, from anywhere" without ever inheriting the risk of driving loops or
mutating config. Control never has to be portable — it lives on trusted surfaces
that already exist (CLI/Telegram) or on the operator's own machine (a future VS
Code plugin). D-dash-1.

---

## 3. Architecture — thin adapter, one JSON layer

Everything renders from the **same JSON**, produced once and consumed by every
front end:

```
i2c.control / portfolio --json  ─┐
.state/*.json                    ├─►  dashboard JSON model  ─►  { static HTML v0, live server v1, ... }
.state/telemetry.jsonl           │
i2c doctor --json                ─┘
```

- **One JSON-shaping layer** (e.g. `i2c dashboard --json`, or reuse
  `portfolio --json` + a small telemetry aggregator). The static generator and
  the future server consume the *same* shape → static → server → remote is
  **purely additive**, no rework. D-dash-2.
- No parallel state; the dashboard never writes. It's another adapter over
  `i2c.control`, preserving the "one source of truth" invariant.

---

## 4. Future-proofing decisions to make NOW (the load-bearing part)

Get these right at v0 (when it's local and low-stakes) so a future remote user
is safe **by construction**, not by later rework:

- **D-dash-3 — No-secrets surface allowlist.** The dashboard may read/render
  **only**: `.state/*` (project/phases/steps/decisions/devlog), `telemetry.jsonl`,
  the `[run]` table of `i2c.toml`, and `doctor` output. It must **never** surface
  env secrets, provider API keys, bot tokens, or `[telegram]` admin IDs. Enforce
  as an explicit allowlist in the JSON-shaping layer (deny-by-default), so remote
  exposure later changes the *deployment* threat model, not the *data* one.
- **D-dash-4 — Single auth choke point.** The (future) server routes all access
  through one middleware seam. v0/v1 bind localhost/LAN with no auth; the code
  carries **no scattered "trust everything" assumptions**, so adding auth =
  wrapping one layer. No auth is *built* now — it's just not *precluded*.
- **D-dash-5 — Read/write strictly separated.** No write path exists in the
  dashboard codebase. A future control surface is a *separate* adapter over
  `i2c.control`; it does not grow out of the read server.

---

## 5. Stages (additive)

- **v0 — static generator.** `i2c dashboard` emits a self-contained HTML snapshot
  (portfolio + per-project + telemetry charts + health + topology). It's a
  *file* — open in any browser, sync via the shared disk, **no server, no auth,
  no network exposure**. Regenerate on demand / post-run. Nails cross-platform +
  portable at near-zero risk. **This is the first build.**
- **v1 — local read-only server.** Optional extra `i2c[web]`; Python server over
  the JSON layer + a static client, **LAN-first** (served from pirozhok where the
  portfolio + bots live). Live refresh. Still no auth (LAN/localhost).
- **v2 — remote (future).** Bind to the operator's existing **WireGuard**
  interface (already available) or a future user's tunnel, + enable the auth
  choke point (D-dash-4). No code rearchitecture — deployment + the pre-designed
  auth seam only.
- **v3 — control surface (future, separate).** A VS Code plugin (operator's
  preferred path for control/config) or a web *write* adapter — over
  `i2c.control`, honoring the one-poller rule (delegate to the driver; never a
  second poller). Not part of this doc's build.

---

## 6. Panels (all read-only)

- **Portfolio** — every project: name, i2c-vs-e2e, phase/state, last activity,
  open-decision count, health flag.
- **Project detail** — phase/steps/decisions/devlog drill-down from `.state/`.
- **Telemetry** — cost, success/exit outcomes, throughput, tokens, tier/backend
  mix, drift flags — aggregated from `telemetry.jsonl` (telemetry increments 1–2).
- **Health** — `doctor --json`: PATH, deps, schemas, backends per host.
- **Topology** — the `deployment.md` "what runs where" model: which bot drives
  which project, i2c vs e2e, what's live. This is the "conceptual aid."

---

## 7. Packaging & platform

- **Optional extra `i2c[web]`** (like `i2c[telegram]`) — never core; preserves
  i2c's single-runtime-dep (jsonschema) cleanliness and the greenfield-adoption
  packaging stance. The v0 static generator may even avoid a web framework
  entirely (stdlib + a templating string), keeping deps minimal.
- **Cross-platform for free:** "Python process + browser" runs identically on
  Windows, Linux, and macOS. (macOS is Unix/BSD/POSIX, not Linux — irrelevant
  as long as we avoid OS-specific bits.)

---

## 8. Relationship to existing work

- **Waymark (FUTURE_waymark):** **deferred indefinitely.** This subsumes its
  read-only Scope A. Its distinct value (in-editor control) re-enters only as the
  v3 VS Code control surface, if ever.
- **Telegram bot:** unchanged — remains the remote *control* driver; the
  dashboard is the *read* complement.
- **Telemetry (increments 1–2):** the dashboard is a primary consumer of
  `telemetry.jsonl` — and a motivation to keep that data clean.
- **`i2c doctor`:** the health panel's data source.
- Supersedes the TODO "Dashboard (web?)" idea with a concrete, scoped plan.

---

## 9. Open questions

- **Q-dash-1:** v0 static generator — a new `i2c dashboard` subcommand, or a
  standalone script consuming `portfolio --json`? (Lean: subcommand, so it ships
  with the fleet.)
- **Q-dash-2:** telemetry aggregation — compute in the JSON layer, or a separate
  `i2c telemetry --json` summary the dashboard reuses? (Lean: a reusable summary,
  useful to the benchmark too.)
- **Q-dash-3:** where does the static HTML live / how is it refreshed — post-run
  hook, cron, or manual `i2c dashboard`? (Lean: manual + optional post-run.)
- **Q-dash-4:** topology source — parse `deployment.md`, or a small declared
  `topology.toml`? (Lean: a tiny declared file; prose docs rot.)

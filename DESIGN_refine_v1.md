# DESIGN — Formalizing Refine / ad-hoc work v1

> **Status:** Draft / proposed (spec only; no code). Formalizes the *opportunistic
> refine* work — cleanups, prose passes, dead-surface removal, and the endless
> doc/status reconciliation — that i2c does constantly but never structured. The
> Build regime got dogfooded on the consumer repos; the Refine work got done
> *here*, ad-hoc and un-tracked. Decisions tagged D-refine-*; open questions
> Q-refine-*.
>
> **One-line goal:** give ad-hoc/refine work the same treatment i2c gave Build —
> structured state instead of drift-prone prose, and a low-ceremony loop instead
> of improvisation — **without** forcing it through the heavyweight phase
> lifecycle it doesn't fit.
>
> **Interface (pinned 2026-07-02):** no new interface — reuses the existing
> CLI-over-shared-state model: `i2c fu` (backlog; mirrors `i2c state`) + `/refine`
> on the i2c bot (mirrors `/run`). Per-project `.state/followups.json`; capture is
> an operator-session step; dispatch is loop *or* orch; sessions learn the
> protocol from a thin `.llms` rule (§10). See §4.4; decisions D-refine-3..6.

---

## 1. Motivation

i2c's thesis is *don't keep state in prose you have to hand-reconcile; put it in
schema-validated `.state/` written through a small CLI.* We applied that to the
project lifecycle (phases/steps/decisions) and it worked. We never applied it to
the framework's **own refine backlog and status** — which is exactly the prose
that keeps drifting.

The evidence is this repo's own history. Across the full 84-commit history (to
`2db5d97`, 2026-07-02), sorting by *nature* rather than commit prefix:

| Kind of commit | ~count | share |
|---|---:|---:|
| **Docs / tracker maintenance** (status refresh, de-stale, reconcile, consolidate, add/close FU) | ~22 | 26% |
| **Refine** (improve existing, no new feature) | ~16 | 19% |
| Feature / build | ~20 | 24% |
| Design / spec | ~9 | 11% |
| Release / packaging + the D-state refactor stack | ~17 | 20% |

**Refine + tracker-maintenance (~38 of 84, ~45%) is the single largest slice of
i2c's development — and the only mode i2c never formalized.** Two facts fall out:

1. **The recurring failure class is drift, and it's on-thesis.** ~10 commits
   exist *only* to un-drift hand-maintained prose (`refresh cold-start` ×4,
   `de-stale`, `fix README phase-count drift`, `consolidate DEVPLAN→FOLLOWUPS`,
   `merge TODO/reconcile`). That last one is *this session*. The FU tables and the
   "recently shipped" log are precisely the prose-state-that-drifts that i2c was
   built to eliminate — one level up, applied to the framework itself.

2. **Ad-hoc refine is not chaos — it has a taxonomy.** The refine commits cluster
   into a small, enumerable set of *kinds*, each with its own recognizable trigger
   (§2). That is what makes it formalizable.

**Non-goals.** This does *not* replace the existing Refine *regime* (a planned,
time-budgeted phase — see §3). It does *not* add ceremony to trivial fixes. And
the backlog **gates nothing** — it stays a backlog, not a blocker list (the rule
`FOLLOWUPS.md` already lives by).

---

## 2. The empirical taxonomy (from the corpus)

Every refine commit in the history maps to one of these kinds. Each has a
characteristic **trigger** (when it fires) and a characteristic **shape** (how the
work is done). This taxonomy is the raw material for both the `kind` enum in §4
and the per-kind checklists in §6.

| Kind | Trigger | Shape | Corpus examples |
|---|---|---|---|
| **prose-refinement** | worker-facing text drifts toward threat-framing / verbosity / stale wording | rewrite in place; one commit per category; assembler golden may regen | FU-36 A/B/C/D (5 commits), `reason-first decision rule`, `PLAN cross-product guidance` |
| **dead-surface removal** | invisible structure (unused flag, schema field, section, env var) suspected non-load-bearing | mine consumers across code + CC corpus → evidence → cut-or-keep → one focused commit | FU-37, FU-7 (exit-signal trim), `drop STOP_BEFORE_REVIEW`, `drop in_progress` |
| **doc/status reconciliation** | canonical docs lag reality (status, counts, command names, cross-refs) | verify against ground truth (code/`.state/`) → correct → review diff for integrity | `refresh cold-start` ×4, `fix README phase-count drift`, `merge TODO`, `record CC migrated` |
| **cli-ergonomics** | a real workflow keeps hitting a rough edge in the worker/operator surface | add the minimal subcommand/flag; schema-validated; sibling to existing ops | FU-2/13 (`append-record`/`update-record`), FU-19 (bare-filename resolve), FU-21 (`--from-file`) |
| **test-hardening** | an invariant is asserted in prose but nothing checks it | add a targeted test + a floor/guard against regression | FU-11 (instruction-example validation), FU-17 (arg rejection) |
| **structural-refactor** | duplicated/overloaded machinery has accreted; a cleaner single form exists | design memo + decisions + a coordinated multi-commit stack | FU-39 (single projection layer), the 7-state lifecycle redesign (D-state-1..7, 5 commits), `retire predecessor-dialect` |

Note the last row is the bridge to §3: **structural-refactor is refine done at
phase scale** — it already gets a memo + decisions + a commit stack. The other
five kinds are *sub-phase* and today get nothing but an FU-table row and a commit.

---

## 3. The design tension (and the existing Refine regime)

Ad-hoc refine is **reactive and opportunistic** by nature — "the practice is the
audit" (FU-37). You cannot PLAN it as a phase up front; the trigger is *noticing*,
mid-other-work. So any formalization must be **low-ceremony**: forcing
PLAN→EXECUTE→REVIEW→CLOSE onto a two-line prose fix would kill the thing we're
trying to capture.

i2c already has a **Refine *regime***: a phase whose PLAN sets a *time budget*
instead of steps (`build`/`refine`/`explore`; see README "The four worker
actions" + `plan.md`). That is the right tool for **large, scoped** refine —
exactly the structural-refactor row above (the D-state redesign is the exemplar).

So we end up with **two tiers of refine**, chosen by blast radius — this is the
core decision:

- **Refine-as-phase (exists)** — a planned, time-budgeted phase in the lifecycle.
  For substantial, coordinated refactors that deserve a memo + decisions.
- **Refine-ad-hoc (new, this doc)** — sub-phase, backlog-driven, no `phases.json`
  record, no lifecycle-state change. For the prose / dead-surface / reconciliation
  / ergonomics / hardening classes. **— D-refine-1.**

The three proposals below (A/B/C) all serve the *ad-hoc* tier.

---

## 4. Proposal A — Structured refine backlog (the on-thesis core)

**Move the FOLLOWUPS FU-tables and "recently shipped" log out of hand-edited
markdown and into `.state/`-shaped records written through a tiny CLI, with the
prose views *rendered* — the same move `i2c.control` made for operator status.**
This is the smallest change and it directly kills the drift class (§1.1), because
the tracker stops being a thing you hand-edit and start being a thing you query.

### 4.1 State shape

Two files, both git-tracked, both written only via the CLI (atomic +
schema-validated, reusing the `i2c state` machinery):

`followups.json` — array of records; the schema is *already latent* in the current
FU-table columns (ID / Title / Status / Context / Trigger) plus the "Closed"
resolution note:

```json
{
  "id": "FU-41",
  "title": "…",
  "kind": "prose | dead-surface | doc-reconciliation | cli-ergonomics | test-hardening | structural-refactor | other",
  "status": "open | accepted | partially-closed | closed | wontfix",
  "context": "…",
  "trigger": "…",
  "resolution": "…",            // set when status → closed/wontfix
  "refs": ["D-arch-13", "9d39390"],   // optional: decisions / commits / other FUs
  "files": ["instructions/plan.md"],  // optional: hint for the §5 refine loop
  "opened": "2026-07-02",
  "closed": "2026-07-02"        // optional
}
```

`refinelog.jsonl` — append-only outcome log for refine work (a devlog sibling,
scoped to the ad-hoc tier): `{ts, fu, kind, summary, commit, files_touched}`. This
becomes the source for a *rendered* "Recently shipped (refine)" view, replacing
the hand-maintained log.

### 4.2 Surface

- `i2c fu add|show|close|reopen` — writes, schema-validated (mirrors
  `i2c state append-record` / `update-record`; `--from-file` for `$`-laden text).
- `i2c fu list [--status open] [--kind …] [--json]` — the query the FU-table
  never had (closes the spirit of FU-14 for this dataset).
- `i2c fu render` — regenerates the markdown FU-tables / recently-shipped view
  from state, single-sourced and formatted at the CLI (**the drift killer —
  D-refine-2**). `FOLLOWUPS.md`'s tables become generated output; the narrative
  (cold-start orientation, Active Roadmap) stays authored prose.

### 4.3 Why this first

Mechanical migration (the columns already map 1:1), no phase-machine changes, and
it lets i2c finally **dogfood its own Refine tier** by running its own backlog
through i2c-shaped state. The next time docs "drift," the fix is `i2c fu render`,
not a hand-reconciliation commit.

### 4.4 Interface & actors — who writes, dispatches, implements

A needs **no new interface**: the refine tier reuses the CLI-over-shared-state
architecture already in place for Build. Because the workspace is one shared disk
(`p:\shared` on the laptop = `/home/claude/workspace` on the pi), a deterministic
CLI over `.state/followups.json` is callable *identically* from every surface —
this VS Code session on the laptop, a Claude Code orch session on the pi, and a
loop worker on the pi. `i2c fu` is to the refine backlog what `i2c state` is to
`.state/`; `/refine` is to a refine item what `/run` is to a phase.

**State location — per-project (D-refine-3).** `<proj>/.state/followups.json` +
`refinelog.jsonl`. Slots into the bot's per-project `/setdir` + cwd model with
zero new plumbing, and — being independent of `phases.json` — lets any repo (even
non-migrated ones like codexbot / e2e, and i2c itself) adopt the refine tier
**without** running the full lifecycle. i2c's own `FOLLOWUPS.md` is the first
migration.

**Roles → surfaces:**

| Step | Who / where | Interface |
|---|---|---|
| Capture / formalize | operator/agent session (this one, or Claude Code orch on pi) | assistant runs `i2c fu add` (may also write a DESIGN doc the FU `refs`) |
| Store | the share: `<proj>/.state/followups.json` + `refinelog.jsonl` | atomic, schema-validated writes via `i2c fu` |
| Read / orient | any session + the i2c bot | `i2c fu list` / `i2c fu render`; bot read facet (`/fu` or `/audit fu`) |
| Dispatch | operator, via either driver | bot `/refine <fu-id>` (deterministic) or "do FU-N" in a session |
| Implement | refine loop on pi **or** orch session | `i2c refine <fu-id>` worker (§5), or the session edits directly |
| Close / log | the implementer | `i2c fu close` + `refinelog` append; runner commits (loop) or assistant commits (session) |

**Capture is a session step (D-refine-4).** Turning an observation into a
well-specified FU is judgment work — it belongs in an operator/agent session, not
the deterministic loop. In practice the assistant calls `i2c fu add` when we
decide to formalize (same ergonomics as editing files today, but atomic,
schema-validated, and immediately dispatchable). This is the one step that stays
human/agent-only; everything downstream can be deterministic.

**Dispatch is loop *or* orch, by blast radius (D-refine-5).** Mirrors the
Human/Agent driver split (WORKFLOW §7). If the FU is specified tightly enough that
a worker needs *no new judgment* (a prose pass, a dead-surface removal with a
known target, an `i2c fu render` doc fix) → `/refine <fu-id>` on the i2c bot
(unattended on the pi; emits refine telemetry). If it needs exploration or
cross-cutting reasoning → an orch session does it directly. The FU's `kind` +
`files` fields are the routing signal.

---

## 5. Proposal B — A lightweight Refine loop

**Formalize the ad-hoc session shape — Discuss → targeted change → log → commit —
as a thin, sub-phase loop, reusing the assembler and runner but bypassing the
state machine.** This brings i2c's signature move ("worker gets fully
pre-assembled context, writes a structured outcome") to refine.

`i2c refine <fu-id> [--mode supervised|autonomous]`:

1. The **assembler** builds a *refine prompt*: WORKER_SPEC (refine framing) +
   adapter Tool Rules + the `followups.json` record (title/context/trigger/kind) +
   the declared/heuristic `files` — no `instructions/{plan,execute,…}`, no phase
   context.
2. The **worker** makes the change, appends a `refinelog.jsonl` row, and sets the
   FU `status` via `i2c fu close` — **no `phases.json`/`project.state` write**
   (this is the sub-phase property; D-refine-1).
3. The **runner** commits (runner-owned per FU-40; message body from the
   refinelog `summary`), scoped so operator WIP is untouched.

Relationship to existing pieces: it reuses the `--emit system/user` split (FU-35),
the invariants harness (a refine-specific post-check: FU closed + refinelog row
appended), and — if we want refine measured too — emits a telemetry row with a
`refine` action_type, feeding the benchmark's refine bucket (Q-refine-3).

Deliberately **not** included: multi-step planning, review/close gates. If a
refine job is big enough to want those, it's a Refine-*regime phase* (§3), not
ad-hoc.

---

## 6. Proposal C — Refine kinds codified

Turn the §2 taxonomy into first-class, repeatable procedure. Each `kind` gets a
one-paragraph **trigger + mini-checklist** (optionally an
`instructions/refine_<kind>.md` snippet the assembler can fold into the §5
prompt). Two worked examples the corpus already proves out:

- **dead-surface removal** (from FU-37's own method): *mine the codebase + CC
  corpus for live consumers → surface the evidence → decide cut-or-keep → execute
  as one focused commit. Avoid big-bang audits; the practice is the audit.*
- **doc/status reconciliation** (from this session's method): *verify each claim
  against ground truth (code / `.state/` / live command surface) → correct →
  review the diff for integrity before committing (CRLF-safe, no content loss).*

C is the lowest priority: codify a kind's checklist **when it recurs a third
time**, not speculatively (the FU-37 discipline applied to this doc itself).

---

## 7. Sequencing & recommendation

1. **A first** — structured backlog + `i2c fu render`. Smallest change, kills the
   drift class outright, mechanical migration, dogfoods the Refine tier. Do the
   `FOLLOWUPS.md` migration as the first (and reference) consumer.
2. **B next** — the refine loop, once A gives it structured items to pull from. It
   reuses the assembler/runner/telemetry already shipped, so it's mostly wiring.
3. **C opportunistically** — codify a kind's checklist on its third recurrence.

This also composes with the benchmark thread (Active Roadmap §7): once refine is a
dispatched action with telemetry, "cheapest model that still succeeds" extends
from Build steps to refine work too.

---

## 8. Open questions (Q-refine-*)

- **Q-refine-1 — RESOLVED (D-refine-3, 2026-07-02): per-project `.state/followups.json`.**
  Every project (migrated or not) can carry a refine backlog; i2c's own
  `FOLLOWUPS.md` is the first migration. Independent of `phases.json`, so a repo
  adopts the refine tier without the full lifecycle.
- **Q-refine-2 — RESOLVED (2026-07-02): render the data, author the narrative.**
  Migrate the FU-tables + recently-shipped log to state (rendered via `i2c fu
  render` / from `refinelog`); leave the cold-start orientation + Active Roadmap
  as authored prose. Revisit only if the roadmap itself starts drifting.
- **Q-refine-3 — RESOLVED (2026-07-02): yes.** Ad-hoc refine emits telemetry
  rows with a `refine` action_type so it feeds the benchmark's refine bucket;
  additive and cheap (the envelope schema is already nullable).
- **Q-refine-4 — RESOLVED (2026-07-02): `i2c fu` = backlog surface
  (add/show/close/reopen/list/render), `i2c refine <fu-id>` = the loop.** Distinct
  verbs, no collision.
- **Q-refine-5 — RESOLVED (2026-07-02): runner stamps a machine-parseable
  format.** The §5 runner-owned commit uses `refine(<kind>): <fu-id> <summary>`
  so `git log` is mineable by kind/FU and cross-references `refinelog` + telemetry
  — readability is a non-goal (the operator never reads titles); *analyzability*
  is the point. Extends the FU-8/FU-40 committer-format direction to the refine
  tier. Session/hand commits are encouraged to match but not enforced.

---

## 9. Relationship to existing surfaces

- **`i2c.control` / render** — A's `fu render` is the same single-projection
  pattern (state is truth, prose is formatted output). Reuse `i2c/render.py`.
- **Refine regime + FU-9** — B's refinelog is the ad-hoc-tier sibling of the
  devlog; FU-9's "which iteration" question for Refine-regime devlog entries maps
  onto refinelog's per-row identity.
- **Telemetry (`DESIGN_telemetry_v1.md`)** — Q-refine-3 adds a `refine`
  action_type to the existing envelope; no new mechanism.
- **FU-40** — B's runner-owned refine commit is the same committer-centralization
  direction, extended to the refine tier.
- **DECISIONS.md** — D-refine-* land there on ratification; this memo is the
  authoritative rationale until then.

---

## 10. Session awareness — the `.llms` rule (D-refine-6)

For any of this to work, **operator / orch / Devmate sessions must know the
protocol exists** — that an idea gets captured with `i2c fu add`, read with
`i2c fu list/render`, and dispatched via `/refine` or directly, rather than
hand-edited into `FOLLOWUPS.md`. That awareness is a real component of A, delivered
as a thin auto-loaded `.llms` rule (sibling to `deployment.md`).

**Keep the rule thin and pointer-based — or it becomes the next thing that
drifts** (the exact failure this whole design fights). The rule teaches the
*protocol and verbs* and points to the live, authoritative surfaces; it does
**not** re-encode the schema, the FU list, or the CLI flags:

- the two-tier model (ad-hoc refine vs the Refine *regime* phase — §3) and when
  each applies;
- the verbs: capture `i2c fu add`, read `i2c fu list/render`, dispatch `/refine`
  or a session, close `i2c fu close`;
- the behavioral rule: **when the operator surfaces something worth tracking,
  formalize it via `i2c fu add` in the relevant project — don't hand-edit the
  markdown**;
- pointers to authority: `i2c fu --help` and `DESIGN_refine_v1.md` (this doc).

**Location (Q-refine-6 — RESOLVED 2026-07-02).** The refine protocol is **one
section of a new, broader always-loaded `i2c` usage rule** — not a standalone
`refine.md`, not appended to `deployment.md`. The session context set becomes
three orthogonal, thin, pointer-based rules, each owning one concern (the
single-source discipline applied to the rules themselves):

| Rule (global `~/.llms/rules/`) | Owns |
|---|---|
| `CLAUDE.md` *(exists)* | operator identity, OS, tooling prefs, general working rules |
| `deployment.md` *(exists)* | *where/how things run* — host/disk topology, the driving bots + control surfaces, one-poller/FU-28 constraints, ops/restart, migration status + project map |
| **`i2c.md` (new)** | *what i2c is & how to use it* — the state/lifecycle mental model, Build (loop/regimes) vs the Refine tier, how to invoke (CLI verbs + which bot commands), the refine capture/read/dispatch protocol, and the operator meta-workflow (idea → explore → formalize as DESIGN/FU → implement); points to `README.md` / `DESIGN_*` as authority |

Global (`~/.llms/`) is the reliable Devmate surface (FU-20). Keep each rule
thin and pointer-based so they neither overlap nor drift into one another.

> The operator meta-workflow (*idea → explore in a session → formalize as a
> DESIGN/FUTURE doc or an FU → implement in whole or parts*) is included in the
> new `i2c.md` rule as framing context; the refine protocol is its concrete,
> tooling-backed slice.

# DESIGN — Formalizing Refine / ad-hoc work v1

> **Status:** Proposal A **shipped** (the `i2c fu` backlog). Proposal B **core
> shipped** (the `i2c refine <fu-id>` single-shot loop — this doc §12): the
> assembler `refine` recipe + `instructions/refine.md`, the `run_refine.py` driver,
> the sub-phase invariant, the `devlog`/`telemetry` refine support (D-refine-8), and
> the `i2c refine` CLI. **Deferred:** the `/refine` Telegram command and
> followups-only-repo (i2c self-dogfooding) support. Formalizes the *opportunistic
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
> protocol from a thin `.llms` rule (§10). See §4.4; decisions D-refine-3..8
> (D-refine-7/8 — the tracker/log unification — live in §11–§12, not a separate section).

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
| **experiment-log** | a run/experiment produced a result or finding worth recording | append the finding to a log/notes, often with a numbered writeup; no code change | *(diplomat)* `Run 21 …`, `Run 18 writeup`, `TUNING_LOG Run 15 findings`, `RESEARCH_NOTES Note 1`; i2c's own benchmark thread (§7) will generate these |
| **bugfix** | a Build iteration fails with a `code`-class defect `reconcile` can't touch (from recovery's `diagnose(code)`) | single-shot repair on the diagnosed target: read the diagnosis → minimal fix → run tests → **don't** touch phase state (the Build loop re-attempts the failed step) | *(recovery)* the automated `fix` — D-recovery-7; see `FUTURE_recovery.md` |

Note **structural-refactor is the bridge to §3: refine done at phase scale** —
it already gets a memo + decisions + a commit stack. The other kinds are
*sub-phase* and today get nothing but an FU-table row and a commit.

**Validated against a second corpus (2026-07-02).** A skim of diplomat's recent
250 of 533 commits — a research/negotiation project, not tooling — found every
sub-phase kind recurring, including a near-identical prose pass (its `U-36
reason-first lens` mirrors i2c's FU-36). It independently reproduced the **drift
class** (recurring `NEXT_STEPS` / `TUNING_LOG` cleanup / reorg / sync commits — the
exact motivation for Proposal A) and surfaced **`experiment-log`** as a
generalizable seventh kind (added above; i2c's own benchmark thread §7 will produce
these). Watch-items: `bugfix` was **subsequently adopted** as the automated
code-repair kind (= recovery's `fix`, D-recovery-7; see §9 + `FUTURE_recovery.md`)
— *not* folded into test-hardening, since "repair a diagnosed defect" and "add a
guard test" are different triggers. Not adopted: viz / output-polish
(diplomat-specific presentation work).

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
  "kind": "prose | dead-surface | doc-reconciliation | cli-ergonomics | test-hardening | structural-refactor | experiment-log | bugfix | other",
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

**Outcome logging — reuse `devlog.jsonl` (D-refine-8).** Refine runs get **no**
separate log; outcomes append to the existing `devlog.jsonl` (discriminated by
`action`/`kind`, with `phase`/`step` null) plus `telemetry.jsonl`
(`action_type=refine`). The rendered "recently shipped (refine)" view reads those.
Needs `devlog_entry.schema.json` to allow null `phase`/`step` and carry
`action`/`kind` for refine rows — additive (devlog already tolerates `step: null`).
The **same schema change also adds an optional `iteration: int`** so a null-`step`
row (Refine-regime or refine-tier) can name which iteration it belongs to — this
**folds in FU-9** (closed as superseded; telemetry.jsonl already covers the
quantitative per-iteration analytics).

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

**State location — per-project (D-refine-3).** `<proj>/.state/followups.json`
(refine outcomes go to the shared `devlog.jsonl`, D-refine-8). Slots into the bot's per-project `/setdir` + cwd model with
zero new plumbing, and — being independent of `phases.json` — lets any repo (even
non-migrated ones like codexbot / e2e, and i2c itself) adopt the refine tier
**without** running the full lifecycle. i2c's own `FOLLOWUPS.md` is the first
migration.

**Roles → surfaces:**

| Step | Who / where | Interface |
|---|---|---|
| Capture / formalize | operator/agent session (this one, or Claude Code orch on pi) | assistant runs `i2c fu add` (may also write a DESIGN doc the FU `refs`) |
| Store | the share: `<proj>/.state/followups.json` (outcomes → shared `devlog.jsonl`, D-refine-8) | atomic, schema-validated writes via `i2c fu` |
| Read / orient | any session + the i2c bot | `i2c fu list` / `i2c fu render`; bot read facet (`/fu` or `/audit fu`) |
| Dispatch | operator, via either driver | bot `/refine <fu-id>` (deterministic) or "do FU-N" in a session |
| Implement | refine loop on pi **or** orch session | `i2c refine <fu-id>` worker (§5), or the session edits directly |
| Close / log | the implementer | `i2c fu close` + a `devlog.jsonl` refine row; runner commits (loop) or assistant commits (session) |

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
2. The **worker** makes the change, appends a refine row to `devlog.jsonl`, and sets the
   FU `status` via `i2c fu close` — **no `phases.json`/`project.state` write**
   (this is the sub-phase property; D-refine-1).
3. The **runner** commits (runner-owned per FU-40; message body from the
   devlog `summary`), scoped so operator WIP is untouched.

Relationship to existing pieces: it reuses the `--emit system/user` split (FU-35),
the invariants harness (a refine-specific post-check: FU closed + a `devlog`
refine row appended), and — if we want refine measured too — emits a telemetry row with a
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
  render` / from `devlog` refine rows); leave the cold-start orientation + Active Roadmap
  as authored prose. Revisit only if the roadmap itself starts drifting.
- **Q-refine-3 — RESOLVED (2026-07-02): yes.** Ad-hoc refine emits telemetry
  rows with a `refine` action_type so it feeds the benchmark's refine bucket;
  additive and cheap (the envelope schema is already nullable).
- **Q-refine-4 — RESOLVED (2026-07-02): `i2c fu` = backlog surface
  (add/show/close/reopen/list/render), `i2c refine <fu-id>` = the loop.** Distinct
  verbs, no collision.
- **Q-refine-5 — RESOLVED (2026-07-02): runner stamps a machine-parseable
  format.** The §5 runner-owned commit uses `refine(<kind>): <fu-id> <summary>`
  so `git log` is mineable by kind/FU and cross-references the `devlog` refine rows + telemetry
  — readability is a non-goal (the operator never reads titles); *analyzability*
  is the point. Extends the FU-8/FU-40 committer-format direction to the refine
  tier. Session/hand commits are encouraged to match but not enforced.

---

## 9. Relationship to existing surfaces

- **`i2c.control` / render** — A's `fu render` is the same single-projection
  pattern (state is truth, prose is formatted output). Reuse `i2c/render.py`.
- **Refine regime + FU-9** — refine outcomes reuse `devlog.jsonl` (D-refine-8), not
  a separate log. **FU-9 is folded into D-refine-8:** the same `devlog_entry` change
  adds an optional `iteration: int`, so a null-`step` entry (Refine-regime or
  refine-tier) can name its iteration. FU-9 is therefore closed as superseded.
- **Telemetry (`archive/DESIGN_telemetry_v1.md`)** — Q-refine-3 adds a `refine`
  action_type to the existing envelope; no new mechanism.
- **FU-40** — B's runner-owned refine commit is the same committer-centralization
  direction, extended to the refine tier.
- **Recovery (`reconcile` / `fix`)** — the refine loop (§12) is the same
  *out-of-band, targeted, single-shot dispatch* family as `i2c run --action
  reconcile --target N`; the loop should **reuse that dispatch**, not reinvent it.
  `reconcile` stays separate — its executor is *deterministic* state-repair (no
  worker). **Decided (D-recovery-7): `fix` *is* the `bugfix` refine kind** — a
  single-shot LLM worker on a targeted item. `diagnose(code)` files a `bugfix`
  FU (the diagnosis as its `context` / `refs`); `i2c refine <fu-id>` repairs it,
  human-gated. Recovery keeps only the code-class *capture* + gate/scope policy;
  the *executor* is refine's. The parallel `diagnoses.json` / `fix.md` /
  separate-dispatch stack is dropped.
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

---

## 11. Implementation scope (A) — schema + `i2c fu` CLI

Buildable unit for Proposal A. Scope = the **schema + the `i2c fu` backlog
surface** (the loop `i2c refine`, its `devlog`/telemetry logging, and bot commands
are Proposal B, deferred). Grounded against the current codebase; the key lever:
**registering `followups.json` in `SCHEMA_BY_FILENAME` unlocks generic `i2c state`
writes + bare-name resolution for free**, so write verbs are thin wrappers and
only the read/render side is new code.

### 11.1 `followups.schema.json` (array file; mirrors `decisions.schema.json`)

Envelope: draft-2020-12, `type: array`, `items.additionalProperties: false`.

| field | type | notes |
|---|---|---|
| `id` | string | `^FU-\d+$`, **required** |
| `title` | string | **required** |
| `kind` | enum | `prose · dead-surface · doc-reconciliation · cli-ergonomics · test-hardening · structural-refactor · experiment-log · bugfix · other`, **required** |
| `status` | enum | `open · accepted · partially-closed · closed · wontfix`, **required** |
| `context`, `trigger`, `resolution` | string | optional |
| `refs`, `files` | array[string] | optional |
| `opened`, `closed` | string (date) | set by `fu add` / `fu close` |

Register one line in `validate.py` `SCHEMA_BY_FILENAME`.

### 11.2 `i2c fu` surface

| verb | behavior | mechanism |
|---|---|---|
| `fu add --kind --title [--context --trigger --files --refs]` | next `FU-N`, `status=open`, `opened=today`, validate, append | wrapper → `state.cmd_append_record` |
| `fu close FU-N [--resolution …] [--status closed\|wontfix]` | status + resolution + `closed=today` | wrapper → `state.cmd_update_record --match id=FU-N` |
| `fu reopen FU-N` | status→open, clear `closed` | wrapper → `cmd_update_record` |
| `fu show FU-N [--json]` | one record | `control.followups()` filtered |
| `fu list [--status] [--kind] [--json]` | filtered list | `control.followups()` |
| `fu render` | regenerate FU markdown tables (drift-killer, D-refine-2) | `render._render_followups_tables` |

Only `list/show/render` add new code (`control.followups()` + `FollowupView` +
render); `add/close/reopen` reuse the atomic/validate machinery via constructed
Namespaces (the `control._apply_proposal` pattern).

### 11.3 Steps (commit-sized, dependency order)

1. **Schema + register** — `followups.schema.json` + `validate.py` entry. Tests:
   schema loads; generic `state append-record followups.json` round-trip +
   schema-rejection + bare-name resolution.
2. **`control.followups()`** — `FollowupView` + `_read_followups` (optional-file,
   independent of `load_state`'s required-5, per D-refine-3) + filtering. Tests:
   `TestFollowups` (filter by status/kind; empty when absent).
3. **Render** — `_fmt_followup`, `_render_followups` (list),
   `_render_followups_tables` (grouped markdown). Tests: render shape.
4. **`fu` CLI group** — nested subparser; write-wrappers (id auto-increment +
   defaults) + read verbs. Tests: `TestFuCli` (`list --json`, `add`/`close`
   round-trip, `render`).
5. **Migrate `FOLLOWUPS.md` → `.state/followups.json`** — import all FUs (open
   **and** closed/decided; normalize free-form statuses → the enum). Creates i2c's
   first `.state/` file (git-tracked; independent of the phase machine). Wire
   `fu render` into a delimited region. *(Session job — the status normalization
   is judgment, not a loop.)*
6. **Flip `i2c.md` rule** — `i2c fu` verbs `planned → live` (`/refine` stays
   planned — Proposal B).

### 11.4 Deferred to Proposal B

the `refine` telemetry action_type; `devlog_entry.schema.json` refine-row support
(D-refine-8); the `i2c refine <fu-id>` loop; bot `/refine` + `/fu` facets.

### 11.5 Scoping decisions

- **Q-A1 — `fu render` target.** stdout in steps 3–4; inject into a
  `<!-- fu:begin/end -->` region of `FOLLOWUPS.md` in step 5. *(Lean: yes.)*
- **Q-A2 — ID source.** `fu add` next-id = max numeric id in `followups.json`;
  post-migration starts at FU-41, never reused. *(Lean: yes.)*
- **Q-A3 — migration is a session job** (status enum normalization = judgment).
- **Q-A4 — `fu close` in A = status only;** outcome logging (`devlog`) + the loop are B. *(Lean: yes.)*

---

## 12. Sketch (B) — the `i2c refine` loop internals

> **Implemented (core, this section).** The `i2c refine <fu-id>` loop shipped as
> specified below, with two deliberate deltas from the sketch: (1) the sub-phase
> invariant runs as a **guard before** close/commit (a lifecycle-violating or
> unlogged run is surfaced and never committed), and (2) scope is projects with a
> standard `.state/` (project.json present) — followups-only-repo support and the
> `/refine` bot command are deferred. `control.resolve_followup` / `close_followup`
> back the loop; `invariants.check_post_refine` enforces the sub-phase property.

Proposal B, sketched (fuller than a mention, lighter than the §11 scope). Depends
entirely on A (`followups.json` + `control.followups()`). Core idea: **`i2c refine
<fu-id>` is a single-shot, sub-phase dispatch** — it reuses the assembler +
backend-invoke + telemetry + commit machinery but **skips the state machine**
(there's no lifecycle to advance; the action is fixed as "refine this FU").

### 12.1 Flow

```
i2c refine FU-41 [--backend …]
  1. Resolve FU-41 from .state/followups.json (control.followups) — error if missing/closed.
  2. Assemble a refine prompt (assemble --action refine --fu FU-41):
       WORKER_SPEC (refine framing) + adapter Tool Rules
       + instructions/refine.md (+ per-kind guidance, Proposal C)
       + the FU record (title/kind/context/trigger) + the declared `files`
       + Output Contract (EXIT: 0|2 / REASON)     — NO phase/steps/decisions context
       (reuses the --emit system/user cache split, FU-35)
  3. Invoke backend (claude -p / codex exec) — backend from [run.backends].refine.
  4. Worker acts: edits files → appends a refine row to devlog.jsonl → does NOT touch
     phases.json / steps.json / project.json.
  5. Runner post-processing (runner-owned, per FU-40):
       parse EXIT; on EXIT:0 → i2c fu close FU-41 --resolution "<REASON>"
       commit `refine(<kind>): FU-41 <summary>` (code + .state, scoped)
       append telemetry.jsonl row (action_type=refine, fu, kind, model, tokens, cost, git-delta, outcome)
       run refine invariant → halt-and-surface on failure
```

One invocation — no `plan→execute→review→close`, no `audit_boundary`. That is what
"sub-phase" means.

### 12.2 Components & insertion points

| Piece | What | Reuses / new |
|---|---|---|
| `run_refine.py` (or a `refine` path in `run_iteration.py`) | single-shot driver | **reuses** `invoke_claude`/`invoke_codex`, `assemble_prompt(emit=…)`, telemetry capture, the FU-40 commit helper |
| assembler `--action refine` | new recipe: WORKER_SPEC + adapter + `instructions/refine.md` + FU-record provider (reads `followups.json` for the id) + `files` | **new** recipe + one context provider; reuses section machinery |
| `instructions/refine.md` | refine worker procedure (read FU → minimal change → run tests if present → append a `devlog` refine row → don't touch phase state → EXIT) | **new**, thin |
| extend `devlog_entry.schema.json` (D-refine-8) | refine rows in the shared `devlog.jsonl` — null `phase`/`step` + `action`/`kind` + optional `iteration: int` (folds in FU-9) | **additive** schema change (no new log file) |
| `config._RUN_ACTIONS += "refine"` | enables `[run.backends].refine` routing | **one-line** (list already carries diagnose/reconcile) |
| telemetry `action_type=refine` | refine rows feed the benchmark bucket (Q-refine-3) | **additive**; schema already nullable |
| `invariants` refine check | assert FU closed + a `devlog` refine row appended + **phase files unchanged** | **new** small check (mirrors post-CLOSE invariant, FU-22) |
| bot `/refine <fu-id>` | admin-gated; shells `i2c refine` in project cwd on a worker thread | **new** entry in `telegram_core.MUTATING_COMMANDS` + dispatch (mirrors `/run`) |

### 12.3 Design choices (Q-B*)

- **Q-B1 — who closes the FU?** *Lean: the runner, on `EXIT:0`*, using `REASON` as
  the resolution (deterministic; `i2c fu reopen` if wrong). Worker-self-close is
  self-grading — which the benchmark thread distrusts.
- **Q-B2 — sub-phase enforcement.** The refine invariant hard-asserts
  `phases.json`/`steps.json`/`project.json` are unchanged after a run — this is
  what structurally keeps refine off the lifecycle, not just convention.
- **Q-B3 — single vs batch.** v1 = one FU per call (no state machine needed). A
  `--batch --kind prose` sweep is a later Policy-driver extension.
- **Q-B4 — is there an oracle?** Refine has no frozen acceptance suite (that's the
  Build-only `tests` action). Success = `EXIT:0` + optional `[telemetry].test_cmd`
  pass. So refine telemetry is a weaker benchmark signal than Build-with-tests —
  noted, not blocking.
- **Q-B5 — commit ownership.** Runner-owned `refine(<kind>): FU-N …` (extends the
  FU-40 direction), scoped so operator WIP is untouched.
- **Q-B6 — survey/propose mode.** Beyond *executing* a known FU, the loop grows a
  mirror facet that *files* FUs: a refine call that scans and emits candidates.
  First user is the **dead-surface audit (FU-37)** — a report-only detector
  (vulture + grep) that emits `dead-surface` FUs for normal, human-gated dispatch.
  **Distinct from `diagnose`** (recovery: reactive, iteration-targeted, reads
  `.state/` + loop logs, feeds `reconcile` deterministically); survey is proactive,
  whole-tree, source-reading, and feeds the `fu` backlog by judgment. Per D-refine-7
  they stay **separate lifecycles** but may share the finding/proposal reporting
  shape. Later facet — execute mode is v1.
- **Q-B7 — backend / tier per item. Resolved (D-recovery-7).** No per-kind
  backend config. Refine is single-shot (one FU per call), so the per-call
  `--backend` / `--model` flags (mirroring `i2c run`) already give per-item tier
  control; `[run.backends].refine` is the coarse default for unattended dispatch,
  and an orchestrator / bot may choose the backend by the FU's `kind` (e.g.
  `bugfix` → codex / a stronger model). Subsumes FUTURE_recovery's
  `[run.backends].fix = codex` — there is no separate `fix` action.

### 12.4 Build loop vs refine loop

| | Build iteration | Refine dispatch |
|---|---|---|
| Driver | `run_iteration` + **state machine** picks the action | `run_refine` — **no state machine**; action fixed |
| Unit | one action of a phase | one FU |
| State written | `phases`/`steps`/`project`/`devlog`/`decisions` | `followups.json` + shared `devlog`/`telemetry`/`decisions` |
| Lifecycle | advances `project.state` → `audit_boundary` | none — one shot |
| Gate | human at phase close | none (reopen if wrong) |
| Backend | `[run.backends].<action>` | `[run.backends].refine` |

**What merges vs stays (D-refine-7).** The dispatchers/lifecycles above stay
distinct; what unifies is the *substrate* — one `.state/` + CLI, one shared
`devlog`/`telemetry`/`decisions` log-and-decision store (D-refine-8), and one
reporting surface (`i2c status` / render shows the current phase **and** open FUs).
Tracking merges; implementing doesn't.

### 12.5 Net

Mostly wiring: reuses the assembler, the backend-invoke + `--emit` cache split,
telemetry, and the FU-40 commit helper. Genuinely new surface is small — a refine
recipe + `instructions/refine.md`, the `devlog` refine-row support, a single-shot
driver, a refine invariant, and the `/refine` bot command. B is gated on A.

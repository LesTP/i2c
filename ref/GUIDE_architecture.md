# Architecture — Guided Process

## Purpose

Decompose the system defined in `PROJECT.md` into buildable parts with
clear boundaries, interfaces, and implementation order. Produce
`ARCHITECTURE.md` and one `ARCH_<module>.md` per module so that PLAN
can transcribe them into structured state and the autonomous loop can
drive phases end-to-end with human review only at phase boundaries.

This is structural decision-making — you are defining the shape of the
system, not implementing it. These decisions are expensive to reverse
later. ARCH files authored here become the contract the worker
adheres to for every action.

**Companion spec:** `ref/SPEC_architecture.md` carries the canonical
templates and section taxonomy. This guide walks the process; the spec
defines the artifact.

**Exit criterion.** Pass the stability check (final section below).

---

## The Two-Layer Model

i2c separates the **prose contract** (what the worker reads) from the
**structured state** (what the state machine reads). Architecture
authoring sits firmly on the prose side, but the prose needs to
support the structured side cleanly.

```
PROSE CONTRACT                          STRUCTURED STATE
─────────────                           ────────────────
PROJECT.md                       ─┐
ARCHITECTURE.md                   ├─→  read by worker via assembler
ARCH_<module>.md                 ─┘    (loaded into LLM context)
        │
        │  PLAN reads `## Phasing in This Pilot`
        │  PLAN reads `## Escalation Triggers`
        ▼
.state/phases.json               ─┐
.state/steps.json                 ├─→  read by state machine,
.state/decisions.json             │    invariants, EXECUTE/REVIEW/CLOSE
.state/devlog.jsonl              ─┘
```

What this means for authoring:

- **PROJECT.md and ARCHITECTURE.md** are loaded for PLAN (and
  ARCHITECTURE for REVIEW). Keep them tight — every line is in the
  worker's context for every plan iteration.
- **`ARCH_<module>.md`** is loaded for every action targeting that
  module's phase. Worker reads it as the contract. Authoring matters
  most here.
- **Structured state** is the source of truth for "what phase, what
  step, what status, what decisions, what history." Don't duplicate it
  in prose — at most mirror it (the `## Implementation Sequence` table
  in ARCHITECTURE.md is a human-readable mirror of
  `.state/phases.json`).
- **PLAN is the bridge.** PLAN reads the ARCH's `## Phasing` section
  and authors `.state/steps.json` records from it. So the ARCH file
  needs to be precise enough for that transcription to be mechanical,
  not creative.

---

## Process

Architecture begins with `PROJECT.md` loaded in context. Work through
the topics below roughly in order, but expect iteration — defining
interfaces often reveals that module boundaries are wrong.

**Single-module projects** (identified by the Size Estimate in
PROJECT.md): produce one combined spec covering architecture and
module contract together. Skip the multi-module decomposition and
follow the Combined Spec Template at the end of `ref/SPEC_architecture.md`.

**Multi-module projects:** produce `ARCHITECTURE.md` (compact summary,
always in context for PLAN/REVIEW) and one `ARCH_<module>.md` per
module (detail, loaded on demand for the targeting phase).

**Parallel concern: run governance.** If the project will spend money
on APIs, perform external I/O against rate-limited services, or
accumulate valuable artifacts, work through cost / abort-condition /
data-classification decisions alongside architecture. Some belong in
`ARCHITECTURE.md`'s Key Decisions; some belong in module-specific
sections.

---

### 1. Component Identification

Start from PROJECT.md's core scope. For each capability, ask: what
must exist for this to work?

List candidate components with a one-sentence responsibility for each.
Then challenge the boundaries:

- Could two of these be one? (If responsibilities overlap or they
  always change together, merge them.)
- Is one of these secretly two? (If it has two reasons to change,
  split it.)
- Does every component have a single, clear owner of its data and
  behavior?

Don't aim for many small modules. Aim for boundaries that are *real* —
places where you'd genuinely want to change one side without touching
the other.

### 2. Data Flow Model

Identify the core objects that move through the system. For each:

- What is its shape? (fields, types — sketch level, not full schema)
- Who creates it?
- Who transforms it?
- Who consumes it?
- Where does state live? (in memory, on disk, in `.state/`, in a
  database, in a service)

Draw the flow: which components pass data to which, and in what form.
This is where hidden coupling becomes visible. If two components both
need to understand the internal structure of the same object, they're
coupled — decide whether that coupling is acceptable or whether you
need an interface between them.

### 3. Interface Contracts

For each boundary between components, define:

- What crosses the boundary (function calls, messages, events, shared
  data)
- In what direction
- With what types and guarantees

**The test:** could a developer build module B using only the ARCH
file for module A, without reading A's source code? If not, the
contract isn't complete enough.

Mark any contracts you're uncertain about as **provisional**. They go
into `## Provisional Contracts` in either ARCHITECTURE.md or the
relevant ARCH file. These get extra scrutiny during the first
implementation sessions that use them.

### 4. Interaction Model

Define the high-level interaction architecture — not detailed UI
design, but the structural decisions that affect module boundaries.

- **Interaction vocabulary:** what can a user do? List the primary
  actions.
- **Major UI states:** what are the distinct modes the system can be
  in? (idle, loading, editing, playing back.) These often map to
  different active components.
- **Layout zones:** if visual, what are the major areas? (main canvas,
  controls panel, sidebar.) These often correspond to module
  boundaries.

Skip this section if the project has no user-facing interface
(library, CLI tool, backend service, autonomous agent).

### 5. Implementation Sequence

Determine build order. Start from the dependency graph — which modules
depend on nothing (leaves), which depend on others.

Beyond pure dependency order, consider:

- **Uncertainty:** build the riskiest or least-understood module
  early. If it forces architectural changes, better to discover that
  before you've built everything else.
- **Integration risk:** identify where two modules connecting is
  likely to surface contract problems. Plan to integrate those early.
- **Vertical slices:** prefer an order that produces a working (if
  minimal) end-to-end path early, rather than building all
  infrastructure before any user-visible functionality.

For each module, write one sentence on why it's in this position in
the sequence.

**Mirror this into `.state/phases.json`** when bootstrapping the
project. Each phase record carries:

```json
{
  "id": 1,
  "module": "module_name",
  "title": "Human-readable phase title",
  "regime": "build",
  "dependencies": ["other_module"],
  "status": "pending"
}
```

The `## Implementation Sequence` table in ARCHITECTURE.md is a
human-readable mirror of phases.json. CLOSE updates it (per
`instructions/close.md` step 7) when a phase completes.

### 6. Coupling and Extension

Review the component map against PROJECT.md's extension points. For
each anticipated direction of growth:

- Which modules would be affected?
- Is the change additive (new module, existing interfaces) or
  structural (boundaries move, contracts change)?
- Have you designed sufficient flexibility at the likely growth
  points?

Note which modules are **loosely coupled** (easy to change
independently) and which are **tightly coupled** (changes cascade).
This makes future impact assessment fast.

Don't over-engineer for hypothetical extensions. The goal is
awareness, not premature abstraction.

### 7. Key Decisions

Record architectural choices that had real alternatives. For each:

- What you chose
- What you considered and rejected
- Why
- What would cause you to revisit

Use the standard format:

```
D-N: [Title]
Date: YYYY-MM-DD | Status: Open | Closed
Decision: [What was chosen]
Rationale: [Why]
Revisit if: [Condition]
```

These go into ARCHITECTURE.md's `## Key Decisions` section. They also
get a parallel record in `.state/decisions.json` once the project is
running phases. Bootstrap rule: seed `decisions.json` with the
architectural decisions before Phase 1 PLAN runs so D-IDs don't
collide.

---

## Authoring the Per-Module ARCH File

After ARCHITECTURE.md is stable, author one `ARCH_<module>.md` per
module. The template is in `ref/SPEC_architecture.md`. Two sections
need explicit guidance because they're novel to i2c:

### Authoring `## Phasing in This Pilot`

This is the section PLAN reads and transcribes into `.state/steps.json`
records. Be precise enough that the transcription is mechanical.

**Format depends on whether the module spans one phase or multiple:**

*Single-phase module* (most modules — one phase, N steps):

```markdown
## Phasing in This Pilot

- **Step P.1** ships [scope]. ~N tests in `tests/test_X.py`.
- **Step P.2** extends [prior step] to cover [scope]. ~N tests.
- **Step P.3** [...]. ~N tests.
```

Each bullet maps to one `.state/steps.json` record. PLAN reads the
bullet and writes:

```json
{
  "phase": P,
  "step": N,
  "title": "[ships X / extends Y to cover Z]",
  "status": "pending",
  "notes": "[scope detail from the bullet, ~N tests in tests/test_X.py]"
}
```

*Multi-phase module* (module spans 2+ phases, often subset → full):

```markdown
## Phasing in This Pilot

- **Phase P implements:** [strict subset — what ships in P]. No [excluded scope].
- **Phase P+1 implements:** [remainder — what completes the contract].
- Both phases target this same ARCH_[module].md contract; Phase P
  delivers a strict subset; Phase P+1 completes it.
```

For multi-phase modules, each phase's PLAN action will read this
section and translate the *current phase's* bullet into step records
for that phase.

**Authoring discipline:**

- One step = one commit, roughly. Steps that ship 0 tests or 30 tests
  are suspicious — re-decompose.
- Step titles match the imperative form ("ships X", "extends Y to
  cover Z"). PLAN's transcription preserves this.
- Test counts are rough estimates, not contracts. If a step ends up
  with 6 tests instead of the estimated 8, that's fine. If it ends up
  with 0 or 25, the step was wrong.
- Don't list internal sub-tasks ("write the helper, then the caller,
  then the test"). Steps are at the level of user-visible (or
  consumer-visible) capability, not implementation tactics.

### Authoring `## Escalation Triggers`

This is genuinely new — no existing ARCH files have it. The section
lists **module-specific conditions** under which PLAN or EXECUTE halts
to `state=audit_escalation` and emits `EXIT 2`. Project-general
triggers (three-strikes, cross-module breakage during EXECUTE,
contract drift affecting a built module) come from `WORKER_SPEC.md` —
do not repeat them here.

**Format:**

```markdown
## Escalation Triggers

- **[Trigger name]** — [PLAN | EXECUTE] halts if [precondition specific
  to this module]. Recovery: [what the operator does to clear it].
- **[Trigger name]** — [...].
```

**Examples by module shape:**

*Engine module (deterministic, rule-bound):*

```markdown
- **Source vs. ARCH contract drift** — PLAN halts if the canonical
  source (e.g., game rules doc v9 §6) contradicts the rule mapping in
  `## Validation Rules Covered` below. Recovery: operator reconciles
  the source and the ARCH; commits the corrected ARCH; clears the
  gate.
- **Resolver-boundary test fails** — EXECUTE halts if any step's
  validated+normalized package fails when passed to `resolve_turn`.
  Recovery: investigate whether the violation is a validator bug or a
  resolver contract change; resolve before continuing.
```

*LLM-wrapper module:*

```markdown
- **Provider schema drift** — EXECUTE halts if the provider returns a
  response shape inconsistent with the `## Schema` section. Recovery:
  inspect the response, decide whether to update the schema (provider
  changed) or fix the request (we miswired it).
- **Token budget exceeded mid-step** — EXECUTE halts if a single LLM
  call exceeds `per_call_max_usd`. Recovery: operator decides whether
  to raise the budget or split the call.
```

*Data-access module:*

```markdown
- **Backend capability missing** — PLAN halts if the dep probe shows
  the configured storage backend lacks a capability the `## Public API`
  promises (e.g., we require atomic batch writes, the backend is
  best-effort). Recovery: pick a different backend or downgrade the
  API contract.
```

**Authoring discipline:**

- One trigger per row. If you find yourself ANDing conditions, split.
- Each trigger names which worker action (PLAN, EXECUTE, or both) it
  applies to. Some triggers fire at plan time (dep probe surprises);
  others fire at runtime (a test fails in a way that says the
  architecture is wrong).
- Each trigger has a recovery clause. If the recovery is "redesign the
  module," the trigger is too coarse — it should be a Provisional
  Contract instead.
- Module-specific only. Generic loop-discipline failures (three
  strikes, scope expansion) live in WORKER_SPEC; don't restate.

If you can't think of any module-specific triggers, that's a
yellow flag — either the module is genuinely well-bounded (rare) or
you haven't surfaced its risks yet. Push on it for 5 minutes before
shipping an empty section.

### Authoring `## Inputs the [Module] Does Not Handle`

Explicit non-scope. Reduces the ambiguity that gets re-discovered
every time a new consumer integrates.

Pattern: list every capability adjacent to this module that lives
elsewhere. Be specific about *who* owns it.

```markdown
## Inputs the Validator Does Not Handle

- **Default-package construction** (rules 4.10, 4.11) — the harness
  constructs the default; the validator only checks whatever it's
  given.
- **Tactical reasonableness** — whether an order is *wise* is the
  planner's concern. The validator only checks §6.
- **Cross-package consistency** — the validator validates one package
  at a time; multi-player order interactions are the resolver's
  concern.
```

This section often surfaces design problems: if you can't cleanly say
who owns a thing, the boundary isn't real. Fix the boundary before
shipping the ARCH.

### Required, Recommended, Optional

The per-module template (in `ref/SPEC_architecture.md`) tags every
section. Quick summary:

**Required** (every ARCH file must have these):
Purpose, Public API, Inputs, Outputs, State, Usage Example,
**Phasing in This Pilot**, **Escalation Triggers**,
**Inputs the [Module] Does Not Handle**.

**Recommended** (include when applicable):
Testing Strategy, Provisional Contracts, Dependencies.

**Optional / free-form** (add as many as the module warrants):
Domain-specific lookup tables, error hierarchies, ledger formats,
schema definitions, activation types, behavior modes. Existing ARCH
files derive much of their value from this content — the template
explicitly permits unlimited domain sections.

---

## Variant Patterns

### Combined Spec (single-file project)

For projects where internal module boundaries aren't real, ship a
single combined document instead of `ARCHITECTURE.md` + per-module
ARCH files. Template in `ref/SPEC_architecture.md`.

Use when:
- The whole project is small enough to fit one file (~one screen at
  the architecture level + one ARCH-file's worth of detail).
- Decomposing into modules feels arbitrary — there's really only one
  thing being built.

Don't use this just to save filing effort. If there are two real
modules, ship two files.

### MVP / Full Split

For modules delivered across multiple phases as strict subset → full
contract. Phosphene's `ARCH_orchestrator.md` + `ARCH_orchestrator_mvp.md`
exemplifies. Spec in `ref/SPEC_architecture.md`.

Use when:
- The full contract is too large to land in one phase at a reasonable
  step budget.
- The MVP is genuinely useful before the full module ships.
- The forward-compatibility commitment is real and verifiable.

The MVP file's `## Relationship to ARCH_<module>.md` section is the
load-bearing piece — it carries the strict-subset contract that
downstream consumers depend on.

---

## Stability Check

Before exiting Architecture:

- [ ] Every module has a clear, single responsibility
- [ ] Data flow is mapped — you know what objects exist, who owns them, how they move
- [ ] Interface contracts are defined for every module boundary
- [ ] Provisional contracts are marked and have a plan for resolution
- [ ] Implementation sequence is dependency-valid and risk-aware
- [ ] Coupling is visible — you can quickly assess the impact of a new feature or change
- [ ] Key decisions are recorded with revisit conditions
- [ ] PROJECT.md's extension points are reflected in architectural choices
- [ ] Every module to be built has an ARCH file with all Required sections
- [ ] `## Phasing in This Pilot` in each ARCH is precise enough for PLAN to transcribe mechanically (one bullet → one step record)
- [ ] `## Escalation Triggers` in each ARCH lists module-specific conditions, not generic loop discipline (which lives in WORKER_SPEC)
- [ ] `.state/phases.json` mirrors the Implementation Sequence — same order, same module names, same dependencies arrays

If any item is incomplete, the autonomous loop will either fail clean
(if Δ5 / PLAN precondition check is shipped) or fail messy (if it's
not). Better to land the discipline up front.

---

## When in Doubt

Worked examples drawn from the 13 ARCH files surveyed during the
template's authoring:

- **Multi-phase engine module:** `clankercourts/ARCH_resolver.md`
- **Single-phase non-leaf engine module:** `clankercourts/ARCH_validator.md`
- **Bootstrap module:** `clankercourts/ARCH_bootstrap.md`
- **Composing orchestrator with deferred behavior:** `phosphene/ARCH_orchestrator.md`
- **MVP / full split pattern:** `phosphene/ARCH_orchestrator_mvp.md`
- **Stateful storage module:** `phosphene/ARCH_memory_store.md`
- **LLM-wrapper module:** `phosphene/ARCH_generator.md`
- **Minimal leaf library:** `toolkit/ARCH_embedding.md`
- **Stateful library with explicit error hierarchy:** `toolkit/ARCH_cost_accountant.md`
- **LLM-as-judge classifier:** `toolkit/ARCH_edit_classifier.md`
- **Feedback / signal-processing module:** `toolkit/ARCH_feedback_collector.md`

These predate the Required / Recommended / Optional taxonomy but
otherwise represent the shape this guide codifies. Use them as
reference for what good content in each section looks like; expect to
back-fill the missing sections (Phasing, Escalation Triggers, Inputs
Does Not Handle) when their phases come up under the new template.

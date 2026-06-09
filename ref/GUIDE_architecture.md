# Architecture — Guided Process

## Purpose

Decompose the system defined in `PROJECT.md` into buildable parts with
clear boundaries, interfaces, and implementation order. Produce
`ARCHITECTURE.md` (always) and per-module `ARCH_<module>.md` files
(when Pattern A; see below) so that PLAN can transcribe them into
structured state and the autonomous loop can drive phases end-to-end
with human review only at phase boundaries.

This is structural decision-making — you are defining the shape of the
system, not implementing it. These decisions are expensive to reverse
later. The prose you produce here becomes the contract the worker
adheres to for every action.

**Companion spec:** `ref/SPEC_architecture.md` carries the canonical
templates and section taxonomy. This guide walks the process; the spec
defines the artifact.

**Exit criterion.** Pass the stability check (final section).

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
ARCH_<module>.md (Pattern A only)─┘    (loaded into LLM context)
        │
        │  PLAN reads the phase decomposition
        │  PLAN reads escalation triggers
        ▼
.state/phases.json               ─┐
.state/steps.json                 ├─→  read by state machine,
.state/decisions.json             │    invariants, EXECUTE/REVIEW/CLOSE
.state/devlog.jsonl              ─┘
```

Implications:

- **PROJECT.md and ARCHITECTURE.md** are loaded for PLAN (and
  ARCHITECTURE for REVIEW). Keep them tight — every line is in the
  worker's context for every plan iteration.
- **`ARCH_<module>.md`** (Pattern A only) is loaded for every action
  targeting that module's phase. Worker reads it as the contract.
- **Structured state** is the source of truth for "what phase, what
  step, what status, what decisions, what history." Don't duplicate it
  in prose — at most mirror it (the `## Implementation Sequence` table
  in ARCHITECTURE.md is a human-readable mirror of `phases.json`).
- **PLAN is the bridge.** PLAN reads the phase decomposition from the
  prose contract and authors `.state/steps.json` records from it. The
  prose needs to be precise enough for the transcription to be
  mechanical, not creative.

---

## Picking your pattern

Architecture authoring under i2c takes one of two shapes. The choice
is binary and roughly maps to "do the modules deserve separate files?"

| Pattern | Spec lives in | Use when |
|---|---|---|
| **A: Per-module ARCH files** | Separate `ARCH_<module>.md` per module + compact ARCHITECTURE.md | Modules have clean interfaces; project has ~3+ distinct modules; each is independently understandable |
| **B: Single-document architecture** | One ARCHITECTURE.md (optionally with Layer Contracts subsection) | Modules share state extensively; project is small; or you want vertical phase slices (one phase delivers multiple components together) |

Full template details + worked examples are in
`ref/SPEC_architecture.md`. Quick read for picking:

**Lean Pattern A when:**
- Modules pass typed data across boundaries (CC: resolver ↔ validator
  via dataclasses)
- Each module could be implemented by someone who hasn't read the
  others (toolkit's embedding, clustering, etc.)
- The project has > ~3 distinct modules

**Lean Pattern B when:**
- Modules share extensive global/runtime state (PoP_port's 344
  globals; noise-machine's render-pipeline buffers)
- The project is small enough that separate files feel like ceremony
  (lyonel's 5 Python files)
- Phases are vertical slices — a phase delivers a group of related
  components together (noise-machine's "Phase 2: Color Engine"
  delivering SpectralShaper + ParameterSmoother + GainSafety as one
  unit)

If you can't decide, **start with B**. Promotion to A later is cheap
(extract content into a new `ARCH_<module>.md`, set
`phases.json[].module`). The reverse is also cheap.

The choice doesn't change the i2c machinery — schema, assembler, and
runner are pattern-agnostic.

---

## Process

Architecture begins with `PROJECT.md` loaded in context. Work through
the topics below roughly in order, but expect iteration — defining
interfaces often reveals that module boundaries are wrong.

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

This step also feeds the pattern choice: if you keep merging components
because they share state too tightly, you're heading toward Pattern B.
If components stay clearly separated, you're heading toward Pattern A.

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

**The test:** could a developer build component B using only the
contract for component A, without reading A's source code? If not, the
contract isn't complete enough.

Mark any contracts you're uncertain about as **provisional**. They go
into `## Provisional Contracts` in either ARCHITECTURE.md or the
relevant ARCH file / Layer Contract. These get extra scrutiny during
the first implementation sessions that use them.

In Pattern A, contracts get spilled into per-module ARCH files. In
Pattern B, contracts live either in a `## Public API` section of
ARCHITECTURE.md (for flat single-doc projects) or in `## Layer
Contracts` subsections (when one big doc benefits from named contract
sections).

### 4. Interaction Model

Define the high-level interaction architecture — not detailed UI
design, but the structural decisions that affect module boundaries.

- **Interaction vocabulary:** what can a user do? List the primary
  actions.
- **Major UI states:** what are the distinct modes the system can be
  in?
- **Layout zones:** if visual, what are the major areas?

Skip if the project has no user-facing interface (library, CLI tool,
backend service, autonomous agent).

### 5. Implementation Sequence

Determine build order. Start from the dependency graph — which modules
depend on nothing (leaves), which depend on others.

Beyond pure dependency order, consider:

- **Uncertainty:** build the riskiest or least-understood module
  early. If it forces architectural changes, better to discover that
  before everything else is built.
- **Integration risk:** identify where two modules connecting is
  likely to surface contract problems. Plan to integrate those early.
- **Vertical slices:** prefer an order that produces a working (if
  minimal) end-to-end path early, rather than building all
  infrastructure before any user-visible functionality.

For each phase, write one sentence on why it's in this position in the
sequence.

**This step is pattern-sensitive:**

- **Pattern A**: The Implementation Sequence in ARCHITECTURE.md is a
  summary. Each phase's detailed step decomposition lives in the
  corresponding `ARCH_<module>.md`'s `## Phasing in This Pilot`
  section.
- **Pattern B**: The Implementation Sequence in ARCHITECTURE.md *is*
  the authoritative phase decomposition. PLAN reads its row for the
  current phase as the primary source. Make the table richer —
  include columns for Description, Regime, Depends on, and Status.

**Mirror this into `.state/phases.json`** when bootstrapping the
project. Each phase record carries:

```json
{
  "id": 1,
  "module": "module_name",    // omit in Pattern B
  "title": "Human-readable phase title",
  "regime": "build",
  "dependencies": ["other_module"],
  "status": "pending"
}
```

`module` is omitted for Pattern B phases. The assembler then omits
the Module Contract section entirely and the worker uses ARCHITECTURE.md
as the spec source.

CLOSE updates the Implementation Sequence table in ARCHITECTURE.md
when a phase completes (per `instructions/close.md` step 7).

### 6. Coupling and Extension

Review the component map against PROJECT.md's extension points. For
each anticipated direction of growth:

- Which components would be affected?
- Is the change additive (new module, existing interfaces) or
  structural (boundaries move, contracts change)?
- Have you designed sufficient flexibility at the likely growth
  points?

Note which components are **loosely coupled** (easy to change
independently) and which are **tightly coupled** (changes cascade).
This makes future impact assessment fast — and informs the pattern
choice (tight coupling pushes toward Pattern B).

Don't over-engineer for hypothetical extensions. The goal is
awareness, not premature abstraction.

### 7. Key Decisions

Record architectural choices that had real alternatives. For each:

- What you chose
- What you considered and rejected
- Why
- What would cause you to revisit

Format:

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

**Record the pattern choice as a decision.** Operators reading the
architecture later need to know why the project is structured the way
it is. Lyonel's D-4 ("Single-module / 5 files: ... Separate ARCH files
would add ceremony without benefit") is the model.

---

## Authoring under Pattern A

Each module gets its own `ARCH_<module>.md`. The template is in
`ref/SPEC_architecture.md`; two sections need explicit guidance
because they're new to i2c.

### Authoring `## Phasing in This Pilot`

This is the section PLAN reads and transcribes into `.state/steps.json`
records. Be precise enough that the transcription is mechanical.

**Format depends on whether the module spans one phase or multiple:**

*Single-phase module* (one phase, N steps):

```markdown
## Phasing in This Pilot

- **Step P.1** ships [scope]. ~N tests in `tests/test_X.py`.
- **Step P.2** extends [prior step] to cover [scope]. ~N tests.
- **Step P.3** [...]. ~N tests.
```

Each bullet maps to one `.state/steps.json` record. PLAN reads and
writes:

```json
{
  "phase": P,
  "step": N,
  "title": "[ships X / extends Y to cover Z]",
  "status": "pending",
  "notes": "[scope detail, ~N tests in tests/test_X.py]"
}
```

*Multi-phase module* (subset → full):

```markdown
## Phasing in This Pilot

- **Phase P implements:** [strict subset]. No [excluded scope].
- **Phase P+1 implements:** [remainder].
- Both phases target this same ARCH_[module].md contract.
```

For multi-phase modules, each phase's PLAN action reads this section
and translates the *current phase's* bullet into step records for that
phase.

**Authoring discipline:**

- One step = one commit, roughly. Steps that ship 0 tests or 30 tests
  are suspicious — re-decompose.
- Step titles in imperative form ("ships X", "extends Y to cover Z").
- Test counts are rough estimates, not contracts. If a step ends up
  with 6 tests instead of 8, fine. If 0 or 25, the step was wrong.
- Don't list internal sub-tasks. Steps are at the level of
  user/consumer-visible capability.

### Authoring `## Escalation Triggers`

This was a new addition to the i2c template, though the pattern exists
organically in some real ARCH files (PoP_port's Layer 1 has explicit
"Escalation triggers:" with three module-specific triggers). The
section lists **module-specific conditions** under which PLAN or
EXECUTE halts to `state=audit_escalation` and emits `EXIT 2`.

Project-general triggers (three-strikes, cross-module breakage during
EXECUTE, contract drift affecting a built module) come from
`WORKER_SPEC.md` and `instructions/plan.md` step 2.5 — do not repeat
them here.

**Format:**

```markdown
## Escalation Triggers

- **[Trigger name]** — [PLAN | EXECUTE] halts if [precondition specific
  to this module]. Recovery: [what unblocks].
- **[Trigger name]** — [...].
```

**Examples by module shape:**

*Engine module (deterministic, rule-bound):*

```markdown
- **Source vs. ARCH contract drift** — PLAN halts if the canonical
  source (e.g., game rules doc v9 §6) contradicts the rule mapping in
  `## Validation Rules Covered` below. Recovery: operator reconciles
  the source and the ARCH; commits corrected ARCH; clears the gate.
- **Resolver-boundary test fails** — EXECUTE halts if any step's
  validated+normalized package fails `resolve_turn`. Recovery:
  investigate whether the violation is a validator bug or a resolver
  contract change; resolve before continuing.
```

*LLM-wrapper module:*

```markdown
- **Provider schema drift** — EXECUTE halts if the provider returns a
  response shape inconsistent with the `## Schema` section. Recovery:
  inspect; decide whether to update the schema (provider changed) or
  fix the request (we miswired it).
- **Token budget exceeded mid-step** — EXECUTE halts if a single LLM
  call exceeds `per_call_max_usd`. Recovery: operator decides to raise
  the budget or split the call.
```

*Translation / porting module* (PoP_port's Layer 1 verbatim):

```markdown
- State trace divergence after two different fix attempts
- Ambiguous C semantics (undefined behavior, platform-specific)
- Undiscovered dependency on Layer 2/3/4
```

**Authoring discipline:**

- One trigger per row. If you AND conditions, split.
- Name which worker action (PLAN, EXECUTE, both) it applies to. Some
  fire at plan time (dep probe surprises); others at runtime (a test
  fails in a way that says the architecture is wrong).
- Each trigger has a recovery clause. If the recovery is "redesign the
  module," the trigger is too coarse — it should be a Provisional
  Contract instead.
- Module-specific only. Generic loop-discipline failures live in
  WORKER_SPEC.

If you can't think of any module-specific triggers, that's a yellow
flag — either the module is well-bounded (rare) or you haven't
surfaced its risks yet. Push for 5 minutes before shipping an empty
section.

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

### Required, Recommended, Optional (Pattern A)

**Required:** Purpose, Public API, Inputs, Outputs, State, Usage
Example, **Phasing in This Pilot**, **Escalation Triggers**,
**Inputs the [Module] Does Not Handle**.

**Recommended:** Testing Strategy, Provisional Contracts, Dependencies.

**Optional / free-form:** Any number of domain-specific sections
(lookup tables, error hierarchies, ledger formats, schema definitions,
activation types, behavior modes). Existing ARCH files derive much of
their value from this content.

---

## Authoring under Pattern B

No per-module ARCH files. Everything lives in `ARCHITECTURE.md`. The
authoring discipline is different: instead of per-module spilling-out,
you compose one navigable document.

### How PLAN reads ARCHITECTURE.md

The assembler always loads ARCHITECTURE.md for PLAN. PLAN's primary
data source is the `## Implementation Sequence` table — specifically
the row where `# == project.json.phase`.

For each phase that has a corresponding `## Layer Contracts` subsection
(when used), PLAN also reads that subsection for the detailed
Purpose / Provides / Consumes / Contract / Escalation Triggers / Source
files. PLAN transcribes the step decomposition into `.state/steps.json`
records using:

- **Implementation Sequence row** — high-level goal, regime,
  dependencies.
- **Layer Contract for the row's module/layer** (if present) — detailed
  scope, source files, contract, triggers.
- **Per-phase scoping subsection** (if present, optional) — additional
  per-phase context the table can't carry.

### Implementation Sequence as authoritative

For Pattern B, the table format should carry enough information for
PLAN's transcription without ambiguity:

```markdown
## Implementation Sequence

| # | Module / Phase | Description | Regime | Depends on | Status |
|---|---------------|-------------|--------|------------|--------|
| 1 | Bootstrap | Project scaffolding + test harness | Build | — | Complete |
| 2 | Core engine | DSP pipeline + parameter smoothing | Build | Bootstrap | Complete |
| 3 | Productization | Timer, fade, settings, persistence | Build | Core engine | In progress |
```

Column rules:

- **#** — phase number; matches `.state/phases.json[].id`
- **Module / Phase** — the phase name; matches phases.json title (and,
  in some Pattern B styles, the layer's name in Layer Contracts)
- **Description** — one to three sentences naming the phase's deliverable
- **Regime** — Build | Refine | Explore
- **Depends on** — prior phases or external dependencies; matches
  phases.json `dependencies`
- **Status** — Not started | In progress | Complete (CLOSE updates)

If the Description column ends up needing more than ~3 sentences, that's
the signal to either (a) split the phase, or (b) author a per-phase
scoping subsection (see below) with the extra detail.

### Optional: Layer Contracts

For Pattern B projects whose modules are coupled enough to keep in one
doc *but* are distinct enough to benefit from named contract sections,
use `## Layer Contracts`. Each Layer Contract is essentially what a
per-module ARCH file would be in Pattern A, kept inline.

```markdown
## Layer Contracts

### Layer 1 — Game Logic

**Port strategy:** Translate C→Kotlin (autonomous, validated by replay oracle)

**Provides:**
- ~180 functions across 5 files (see proto.h: seg002, seg004, seg005, seg006, seg007)
- All character movement, collision, combat, AI, trap, and trigger logic

**Consumes:**
- State model (reads and writes globals directly)
- Control input variables: `control_x` (-1/0/+1), `control_y` (-1/0/+1), `control_shift` (0/1)

**Contract:**
- Zero platform calls. No SDL, no Android, no I/O.
- Given identical state + identical input → produces identical output state (deterministic)
- Validated by: replay oracle (compare `state_trace.bin` frame-by-frame)

**Escalation Triggers:**
- State trace divergence after two different fix attempts
- Ambiguous C semantics (undefined behavior, platform-specific)
- Undiscovered dependency on Layer 2/3/4

**Source files and translation order:**
| Order | File | Lines | Role | Depends on |
|-------|------|------:|------|------------|
| 1 | seg006.c | ~1,800 | Character physics | State model |
| ... |
```

(This example is PoP_port's Layer 1 verbatim. See its full
ARCHITECTURE.md for the canonical pattern.)

Within each Layer Contract:

- **Required:** Port strategy / Purpose, Provides, Consumes, Contract
- **Recommended:** Escalation Triggers, Source files, Validation
- **Optional:** Anything else (tiered validation strategies,
  performance budgets, etc.)

### Per-phase scoping subsections (optional)

When the Implementation Sequence row + Layer Contract aren't enough to
fully scope a phase — for instance when a phase has sub-phases (PoP_port's
Module 16 has 16a, 16b, 16c, 16d), or when one phase has its own
escalation triggers distinct from the Layer's — add a `### Phase N:
<Title>` subsection elsewhere in ARCHITECTURE.md:

```markdown
## Phase N: <Title>

[Scope detail beyond the Implementation Sequence row.]

**Phase-specific escalation triggers** (in addition to project-wide
and Layer Contract triggers):

- [Trigger] — [recovery]

**Sub-phases:**
- N.a: [...]
- N.b: [...]
```

PLAN reads this subsection alongside the table row and Layer Contract.

### Where Escalation Triggers go in Pattern B

Three valid placements, used in combination:

1. **Project-wide** — a top-level `## Escalation Triggers` section in
   ARCHITECTURE.md. Lists conditions that apply to any phase.
2. **Per-layer** — inside each Layer Contract's `**Escalation Triggers:**`
   subsection. PoP_port's exemplar.
3. **Per-phase** — inside a `### Phase N: <Title>` subsection. For
   phase-specific risks.

Most Pattern B projects use a project-wide list + (when Layer Contracts
are used) per-layer triggers. Per-phase triggers are rare; add them
when a phase has risks the layer's general triggers don't cover.

### Required, Recommended, Optional (Pattern B)

In `ARCHITECTURE.md`:

**Required:**
- Overview, Component Map, Data Flow, Implementation Sequence, Key Decisions
- **Public API** — the operative interfaces consumers see. Can live
  directly in ARCHITECTURE.md or distributed across Layer Contracts.
- **State** — where the project's runtime state lives.
- **Inputs the [Project] Does Not Handle** — project-level non-scope.

**Recommended:**
- Escalation Triggers (project-wide, or distributed)
- Coupling Notes
- Provisional Contracts
- Testing Strategy

**Optional:**
- Layer Contracts (with required/recommended fields per layer when used)
- Per-phase scoping subsections
- Extension Points
- Domain-specific sections

---

## Variant: MVP / Full Split (Pattern A sub-variant)

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

Not applicable in Pattern B; the same scope-split can be achieved with
two `### Phase N` subsections in ARCHITECTURE.md instead.

---

## Switching patterns later

Projects can start in one pattern and migrate without structural cost:

**B → A (extract a module):**
1. Create a new `ARCH_<module>.md` carrying the extracted content
   (copy from the relevant Layer Contract or flat-spec section)
2. Update the relevant `.state/phases.json` record to set
   `module: "<module>"`
3. Trim the source content from ARCHITECTURE.md, or leave it as a
   stub summary that points to the new file

Useful when one component grows enough to deserve its own document and
independent navigation.

**A → B (collapse modules):**
1. Consolidate the per-module ARCH file content into ARCHITECTURE.md
   (either flat or as a Layer Contract)
2. Remove the `module` field from the relevant phases.json records
3. Delete the per-module file

Useful when modules turn out to be more coupled than the original
split anticipated, or when the project shrinks (e.g., features get
folded into a single layer).

---

## Stability Check

Before exiting Architecture:

- [ ] Every component has a clear, single responsibility
- [ ] Data flow is mapped — you know what objects exist, who owns them, how they move
- [ ] Interface contracts are defined for every boundary that crosses one
- [ ] Provisional contracts are marked and have a plan for resolution
- [ ] Implementation sequence is dependency-valid and risk-aware
- [ ] Coupling is visible — you can quickly assess the impact of a new feature or change
- [ ] Key decisions are recorded with revisit conditions, including the **pattern choice** itself
- [ ] PROJECT.md's extension points are reflected in architectural choices
- [ ] **`.state/phases.json` mirrors the Implementation Sequence** — same order, same titles, same dependencies arrays, `module` set per pattern

For Pattern A specifically:
- [ ] Every module to be built has an ARCH file with all Required sections
- [ ] `## Phasing in This Pilot` in each ARCH is precise enough for PLAN to transcribe mechanically (one bullet → one step record)
- [ ] `## Escalation Triggers` in each ARCH lists module-specific conditions, not generic loop discipline

For Pattern B specifically:
- [ ] Implementation Sequence carries Description / Regime / Depends on / Status columns
- [ ] Each Description is precise enough for PLAN to author step records from it (with the Layer Contract, when present, for detail)
- [ ] Project-wide Escalation Triggers exist; per-layer or per-phase triggers exist where the layer / phase has module-specific risks
- [ ] If Layer Contracts are used, each has the Required fields (Port strategy/Purpose, Provides, Consumes, Contract)

If any item is incomplete, the autonomous loop will either fail clean
(if Δ5 / PLAN precondition check is shipped) or fail messy (if it's
not). Better to land the discipline up front.

---

## When in Doubt

Worked examples covering the expressive range:

**Pattern A:**
- `clankercourts/ARCH_resolver.md` — multi-phase non-leaf module (Phase 2 + Phase 3 subset → full)
- `clankercourts/ARCH_validator.md` — single-phase non-leaf module
- `clankercourts/ARCH_bootstrap.md` — bootstrap module
- `phosphene/ARCH_orchestrator.md` — composing orchestrator with deferred behavior
- `phosphene/ARCH_orchestrator_mvp.md` — MVP / full split exemplar
- `phosphene/ARCH_memory_store.md` — stateful storage module
- `phosphene/ARCH_generator.md` — LLM-wrapper module
- `toolkit/ARCH_embedding.md` — minimal leaf library
- `toolkit/ARCH_cost_accountant.md` — stateful library with explicit error hierarchy
- `toolkit/ARCH_edit_classifier.md` — LLM-as-judge classifier
- `toolkit/ARCH_feedback_collector.md` — feedback / signal-processing module

**Pattern B:**
- **lyonel/workbench/ARCHITECTURE.md** — flat single-doc Pattern B. 3-phase Implementation Sequence with `| Phase | Files | Status |`; Public API + Core Objects + State directly in the doc; no Layer Contracts. D-4 captures the choice rationale.
- **noise-machine/ARCHITECTURE.md** — Pattern B with rich Component Map (12 components in 6 vertical phases); per-phase status carried in DEVPLAN equivalent rather than in ARCHITECTURE.md itself.
- **PoP_port/ARCHITECTURE.md** — Pattern B with full Layer Contracts. 6 layers each with inline Purpose / Provides / Consumes / Escalation Triggers / Source files; 20-module Implementation Sequence across 3 Tracks; sub-phases (16a/b/c/d) tracked in `## Status` prose.

These predate the Pattern A/B taxonomy but otherwise represent the
shapes this guide codifies. Use them as reference for what good
content looks like in each section; expect to back-fill any missing
sections (Escalation Triggers, Inputs Does Not Handle) when their
phases come up under the new template.

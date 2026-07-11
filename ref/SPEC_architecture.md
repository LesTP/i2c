# ARCHITECTURE.md — Output Spec

Defines the required sections for `ARCHITECTURE.md` and (when applicable)
per-module `ARCH_<module>.md` files in i2c projects. Produce them through
whatever process works — structured decomposition, freeform analysis,
research doc synthesis. See `ref/GUIDE_architecture.md` for the guided
process.

**Audience.** Operator + assistant during collaborative architecture
sessions. This file is *not* read by the assembler or included in worker
prompts — it's a human-facing reference for authoring ARCH content that
the framework will then assemble verbatim.

**Exit criterion (stability check).** Before architecture is done:

- [ ] Every component has a clear, single responsibility
- [ ] Data flow is mapped — you know what objects exist, who owns them, how they move
- [ ] Interface contracts are defined for every boundary that crosses one
- [ ] Provisional contracts are marked and have a plan for resolution
- [ ] Implementation sequence is dependency-valid and risk-aware
- [ ] Coupling is visible — you can quickly assess the impact of a change
- [ ] Key decisions are recorded with revisit conditions
- [ ] PROJECT.md's extension points are reflected in architectural choices
- [ ] Pattern (A or B; see below) is chosen explicitly
- [ ] Every module to be implemented under i2c has either a per-module ARCH file (Pattern A) or a Layer Contract section / Implementation Sequence row (Pattern B) with the Required content

---

## How this fits the i2c loop

i2c is **two-layer**: prose contract on one side, structured state on
the other. PLAN bridges them.

- **Prose contract** — `PROJECT.md`, `ARCHITECTURE.md`, and (in Pattern A
  only) `ARCH_<module>.md`. Loaded into the worker's prompt by the
  assembler. Read by the LLM worker as authoritative spec.
- **Structured state** — `.state/project.json`, `phases.json`,
  `steps.json`, `decisions.json`, `devlog.jsonl`. Read by the state
  machine, assembler, invariant checks. Written by the worker via
  `tools/state.py`. Source of truth for "what phase are we on, what
  step is next, what's complete."
- **PLAN action** reads the relevant prose contract for the current
  phase and transcribes its phase-level decomposition into
  `.state/steps.json` records.

ARCHITECTURE.md is loaded for PLAN and REVIEW for every project.
Per-module ARCH files (Pattern A only) are loaded whenever
`phases.json[].module` is set to point at one. Pattern B projects declare
`project.json.pattern = "B"`, leave `module` absent, and PLAN works from
ARCHITECTURE.md alone.

Progress tracking is in `.state/phases.json` (each record carries
`status: pending | complete`). ARCHITECTURE.md's Implementation
Sequence table is a human-readable mirror, kept in sync by
`instructions/close.md` step 7. Both exist; the structured form is
authoritative.

---

## Picking your pattern

i2c supports two architecture-authoring patterns. The choice is binary
and depends on whether your modules are decoupled enough to warrant
separate files.

### Pattern A: Per-module ARCH files

- One `ARCH_<module>.md` per architecturally significant module.
- `ARCHITECTURE.md` is a compact project-level summary.
- `phases.json[].module` points to the file for the targeted module.

**Use when:**
- Modules have clean interfaces between them (typed signatures, schemas,
  RPC contracts)
- Each module is independently understandable and could plausibly be
  implemented by someone who hasn't read the others
- The total project has at least ~3 distinct modules

**Typical shape:** a Pattern A project carries on the order of 3–15
per-module ARCH files — for example a project split into `resolver`,
`validator`, and `bootstrap` modules, each with its own contract.

### Pattern B: Single-document architecture

- Only `ARCHITECTURE.md`. No `ARCH_<module>.md` files.
- `phases.json[].module` is absent for all phases.
- Content that would have lived in per-module files lives in
  `ARCHITECTURE.md` instead — either as a single flat spec, or as
  optional **Layer Contracts** subsections per architectural layer.

**Use when:**
- Modules share state extensively or are too coupled to factor cleanly
- The project is small enough that separate files would be ceremony
- You want vertical phase slices (a phase delivers a related group of
  components together) rather than per-module slices

**The Pattern B spectrum (archetypes):**
- **Flat example** — a small tool (~5 files, no internal module
  boundaries; an architecture decision explicitly chose a combined spec).
  The flat-spec end of Pattern B.
- **Multi-phase example** — a medium project (~12 architectural components
  grouped into 6 phases; one ARCHITECTURE.md, no per-module files). The
  medium-size end, with a Component Map driving vertical phase slices.
- **Layered example** — a larger project (~20 modules across 3 tracks; one
  ARCHITECTURE.md with a `## Layer Contracts` section where each Layer
  carries inline Purpose / Provides / Consumes / Escalation Triggers /
  Source files). The richest end of Pattern B.

### Decision shortcut

Ask: *if a new contributor joins the project, would they find it easier to navigate one big architecture document or many small ones?*

- One big doc → Pattern B (you can still factor it into Layer Contracts internally)
- Many small docs → Pattern A

When in doubt, start with B; promote to A later if specific modules grow
enough to deserve their own files. Going B → A is cheap (extract the
relevant section into a new `ARCH_<module>.md`, set `phases.json[].module`).
Going A → B (collapse separate ARCH files into one) is also cheap but
rarely needed.

---

## ARCHITECTURE.md template (used by both patterns)

The compact project-level summary. Always loaded into the PLAN and
REVIEW worker prompts. Keep it readable end-to-end; the operator and
the worker both consult it.

```markdown
# [Project Name] — Architecture

## Overview                                                 [Required]
[One paragraph — what this project is, what shape its architecture
takes, whether it's Pattern A or B.]

## Component Map                                            [Required]
| Component | Responsibility | Dependencies |
|-----------|---------------|--------------|
| [Name] | [One sentence] | [List or "none"] |

(For tiny single-file Pattern B projects, this can be a 1-3 row file
list instead of a richer component map.)

## Data Flow                                                [Required]
### Core Objects
- **[ObjectName]** — [shape sketch: key fields and types]

### Flow
[How objects move through components. Text, diagram, or arrow notation.]

## Interaction Model                                        [Optional]
### User Actions
### UI States
### Layout Zones

(Omit for non-interactive projects — libraries, agents, backends.)

## Implementation Sequence                                  [Required]
| Order | Module / Phase | Rationale | Status |
|-------|---------------|-----------|--------|
| 1 | [Name] | [Why first] | Not started |

(Human-readable mirror of `.state/phases.json`. Updated at CLOSE per
`instructions/close.md` step 7. Status values: Not started | In progress
| Complete.)

(In Pattern B this table IS the authoritative phase decomposition that
PLAN reads. In Pattern A it's a summary; the per-module ARCH file's
`## Phasing in This Pilot` section carries the per-step detail.)

## Coupling Notes                                           [Recommended]
- [Component A] ↔ [Component B]: [tight/loose], [why, what to watch]

## Key Decisions                                            [Required]
D-1: [Title]
Date: YYYY-MM-DD | Status: Closed
Decision: [What was chosen]
Rationale: [Why]
Revisit if: [Condition]

## Provisional Contracts                                    [Recommended]
- [Contract between X and Y] — uncertain because [reason]. Resolve during [phase / module].

## Layer Contracts                                          [Pattern B optional]
[See "Pattern B: Layer Contracts subsection" below for the template
when this section is used. Pattern A projects skip it — per-module
ARCH files carry equivalent content.]

## Public API                                               [Pattern B required when no Layer Contracts]
[For flat Pattern B projects. Document the operative public interface
here. Per-method signatures, parameters, returns, errors.]

## Extension Points                                         [Optional]
- [Structural observation about likely growth]
```

---

## Pattern A: Per-module ARCH files

When the project has modules with clean interfaces, ship one
`ARCH_<module>.md` per module. Loaded by the assembler into every worker
prompt for the phase targeting that module. A downstream module should
be able to integrate using only this file.

**Exclude from ARCH files:** implementation details, internal types,
design rationale (those belong in implementation docs, not contracts).

### Per-module ARCH template

Every section is tagged **Required**, **Recommended**, or **Optional**.

```markdown
# ARCH: [Module Name]

## Purpose                                                  [Required]
[One paragraph — what this module does, what it's responsible for,
what it deliberately is not.]

## Public API                                               [Required]

### [function/method/class name]
- **Signature:** [typed signature]
- **Parameters:** [name, type, valid ranges]
- **Returns:** [type, guarantees]
- **Errors:** [what can fail, how failures surface]

(Repeat for each public surface element. Types may be inlined or
pulled into a separate `## Types` / `## Core Objects` section — both
patterns are common.)

## Inputs                                                   [Required]
[What this module accepts — formats, valid ranges, edge cases. Often
overlaps with Public API parameters; restate at the data-flow level.]

## Outputs                                                  [Required]
[What this module produces — formats, guarantees, edge cases.]

## State                                                    [Required]
[What state this module owns. Where it lives, who can modify it.
Write "None. [Module] is a pure function over typed inputs." if true.]

## Usage Example                                            [Required]
[Minimal working integration — enough to wire this module into a
consumer. Real code, not pseudocode.]

## Phasing in This Pilot                                    [Required]
[Per-step or per-phase bullet list, ordered. This is what PLAN reads
to author `.state/steps.json` records autonomously.]

[Single-phase module — one phase, N steps:]
- **Step P.1** ships [scope]. ~N tests in `tests/test_X.py`.
- **Step P.2** extends [prior step] to cover [scope]. ~N tests.

[Multi-phase module — delivered across phases as subset → full:]
- **Phase P implements:** [strict subset]. No [excluded scope].
- **Phase P+1 implements:** [remainder].
- Both phases target this same ARCH_[module].md contract.

## Escalation Triggers                                      [Required]
[Module-specific conditions under which PLAN or EXECUTE halts to
`state=audit_escalation`. Project-general triggers (three-strikes,
cross-module breakage, contract drift affecting a built module) come
from WORKER_SPEC — do not repeat. List only what is module-specific.]

- **[Trigger name]** — [PLAN | EXECUTE] halts if [precondition]. Recovery: [what unblocks].

## Inputs the [Module Name] Does Not Handle                 [Required]
[Explicit non-scope. What this module deliberately does not do; what
the caller / harness owns.]

- **[Capability]** — owned by [caller / other module]. [Module] assumes input is already [validated / normalized / etc.].

## Testing Strategy                                         [Recommended]
[Informs PLAN's per-step test counts and shape.]
- **[Test category]** — [coverage description]. [Where].
- **Property tests** — [invariants the module promises].

## Provisional Contracts                                    [Recommended]
- **[Contract surface]** — [sketched here but may change]. Will firm up when [trigger condition].

## Dependencies                                             [Recommended]
[Mirror of `.state/phases.json[id=P].dependencies` for this module's
phase(s), plus rationale.]

- **[Other module]** — [what surface we depend on].
- **External:** [third-party libraries, services, schemas].

## [Domain-specific section]                                [Optional]
[Free-form. Add any number of domain-specific sections. Examples seen
in existing ARCH files: lookup tables (Defense Bonus, Casualty
Allocation), error hierarchies, ledger formats, schema definitions,
activation types, behavior modes.]
```

### Variant: MVP / Full split (Pattern A sub-variant)

For modules delivered across multiple phases as strict subset → full
contract. Two ARCH files:

- `ARCH_<module>_mvp.md` — strict subset; adds a `## Relationship to
  ARCH_<module>.md` section that explicitly carries the
  forward-compatibility commitment plus a "Deferred to the full module"
  list.
- `ARCH_<module>.md` — the full contract.

`.state/phases.json` records target the MVP file first (`module:
"<module>_mvp"`), then switch to the full file (`module: "<module>"`)
when the full module ships. Both files reference each other.

Use when the full contract is too large for one phase AND the MVP is
genuinely useful before the full module ships. The worked example is an
`ARCH_orchestrator.md` + `ARCH_orchestrator_mvp.md` pair: the MVP file
ships first, the full file supersedes it.

### When in doubt

Shapes to model an ARCH file on:
- a **single-phase non-leaf module** — built in one phase, consumed by others
- a **multi-phase module** — a Phase-N subset ships first, a later phase completes it
- a **minimal leaf module** — no dependencies; the smallest viable contract
- an **MVP / full split** — an `_mvp` file ships first, the full file supersedes it

---

## Pattern B: Single-document architecture

When modules are coupled enough that one doc reads better, or the
project is small, everything lives in `ARCHITECTURE.md`. The assembler
loads ARCHITECTURE.md for PLAN and REVIEW; PLAN reads the
Implementation Sequence row for the current phase and (if present) the
relevant Layer Contract subsection.

### How phasing works under Pattern B

`ARCHITECTURE.md`'s `## Implementation Sequence` table IS the
authoritative phase decomposition. Format:

```markdown
| # | Module / Phase | Description | Regime | Depends on | Status |
|---|---------------|-------------|--------|------------|--------|
| 1 | Bootstrap | Project scaffolding + test harness | Build | — | Complete |
| 2 | Core engine | DSP pipeline + parameter smoothing | Build | Bootstrap | Complete |
| 3 | Productization | Timer, fade, settings, persistence | Build | Core engine | In progress |
| ... |
```

PLAN's job: find the row where `# == project.json.phase`, read its
Description + Regime + Depends on, transcribe steps from that row's
scope (using the Description as the high-level goal and the Layer
Contract — if present — as the detail source).

For projects that want **per-phase scoping detail** beyond what the
table carries (additional triggers, deferred items, sub-decomposition),
add an optional `### Phase N: <Title>` subsection elsewhere in
ARCHITECTURE.md with the extra scoping.

### Layer Contracts subsection (optional)

For Pattern B projects whose modules are coupled enough to keep in one
doc *but* distinct enough to benefit from named contract sections, add
a `## Layer Contracts` section. Each Layer Contract is structurally
what a per-module ARCH file would be in Pattern A, kept inline.

```markdown
## Layer Contracts

### [Layer / Module Name]                                   [each Layer Contract]

**Port strategy / Purpose:** [Required]
[Translate? Refactor? Rewrite? What this layer does.]

**Provides:**                                               [Required]
[What this layer exposes to others.]

**Consumes:**                                               [Required]
[What this layer needs from others — state, APIs, types.]

**Contract:**                                               [Required]
[Invariants, constraints, performance budgets, determinism guarantees.]

**Escalation Triggers:**                                    [Recommended]
- [Trigger] — [when it fires; how to recover]

**Source files:**                                           [Recommended]
[Files this layer maps to, with line counts where useful.]

**Validation:**                                             [Recommended]
[How this layer's correctness gets checked — tests, oracles, manual.]
```

A layered project's `## Layer Contracts` section is the worked exemplar
(see the Guide for a full one).

### Pattern B section taxonomy

The `## Pattern B: Single-document architecture` section adds the
following requirements to the base ARCHITECTURE.md template above:

**Required in addition (because no per-module files spill out into):**
- `## Public API` — operative interfaces consumers see. Can live in
  Layer Contracts when used; otherwise as a top-level section.
- `## State` — where the project's state lives (analogous to per-module
  ARCH's `## State`).
- `## Inputs the [Project] Does Not Handle` — explicit non-scope at the
  project level.

**Recommended:**
- `## Escalation Triggers` — project-wide list (or distribute across
  Layer Contracts when those are used).
- `## Testing Strategy` — project-level test posture.

**Optional:**
- `## Layer Contracts` — see above.
- `### Phase N: <Title>` subsections — per-phase scoping detail.
- `## Extension Points` — likely growth directions.
- Any domain-specific sections (lookup tables, error hierarchies, etc.).

### When in doubt

Shapes to model an ARCHITECTURE.md on:
- **Flat** — flat single-doc Pattern B (a 3-phase Implementation
  Sequence, no Layer Contracts, Public API directly in the doc; an
  architecture decision captures the flat-spec rationale).
- **Multi-phase** — Pattern B with a rich Component Map (~12 components
  in 6 vertical phases) and per-phase status tracked alongside.
- **Layered** — Pattern B with Layer Contracts (~6 layers each with
  inline Purpose / Provides / Consumes / Escalation Triggers / Source
  files; a 20-module Implementation Sequence across 3 tracks).

---

## Section naming convention

Use Title Case for all section headings (`## Phasing in This Pilot`,
not `## phasing in this pilot`). Match the templates above verbatim
where applicable. Domain-specific sections use Title Case per project
convention.

This matches `ARCH_assembler.md` §4.1's "Title Case section names
throughout" rule for sections the assembler reads. ARCH content is
assembled verbatim, so the section names you author here appear
verbatim in the worker's prompt.

---

## Pattern choice and i2c machinery

The pattern is recorded explicitly in `.state/project.json` as
`pattern` (`"A"` or `"B"`; **absent ⇒ `"A"`** for back-compat). `i2c init
--pattern A|B` stamps it; set it later with `i2c state set project.json
pattern=B`. This flag is the authoritative signal the assembler keys off:

| Aspect | Pattern A | Pattern B |
|---|---|---|
| `project.json.pattern` | `"A"` (or absent) | `"B"` |
| `.state/phases.json[].module` | Set per phase | Omitted |
| Assembler loads `ARCH_<module>.md` | When `module` set | Never (even if a stray `module` is present) |
| Assembler omits Module Contract section | Only when `module` absent | Always |
| Assembler loads ARCHITECTURE.md | For PLAN + REVIEW | For PLAN + REVIEW |
| PLAN reads phase decomposition from | `ARCH_<module>.md` `## Phasing in This Pilot` | `ARCHITECTURE.md` `## Implementation Sequence` |
| `## Escalation Triggers` lives in | Per-module ARCH | ARCHITECTURE.md (or per-Layer Contract) |

Under Pattern B the assembler ignores any `module` on a phase record — so a
`module` PLAN wrote by mistake no longer wedges TESTS/EXECUTE/CLOSE at prompt
assembly (FU-48). PLAN, in turn, must **not** set `module` under Pattern B
(`instructions/plan.md` step 4). Under Pattern A a `module` set with a missing
`ARCH_<module>.md` remains a hard error (it catches typos).

The `pattern` field is the only schema/assembler surface the distinction adds;
everything else is *where the content lives* and *how PLAN finds it*.

> **D-pattern-1 (Active, FU-48).** `project.json.pattern` (`"A"` | `"B"`,
> absent ⇒ `"A"`) is the authoritative A/B signal. The assembler omits the
> per-module contract whenever `pattern == "B"` — ignoring a stray `module`
> rather than hard-failing — so a single-document project cannot be wedged by an
> erroneous `module` write; under Pattern A a `module` with a missing
> `ARCH_<module>.md` stays a hard error. Supersedes the earlier "no schema
> changes needed for either pattern" claim (the additive `pattern` field +
> `schema_version` 2→3 no-op migration).

---

## Switching patterns later

Projects can start in one pattern and migrate:

- **B → A (extract a module):** set `project.json.pattern=A` (or remove it),
  create a new `ARCH_<module>.md` carrying the extracted content; update the
  relevant phases.json record to set `module: "<module>"`; trim the source
  content from ARCHITECTURE.md (or leave it as a stub summary). Useful when one
  component grows enough to deserve its own document.
- **A → B (collapse modules):** set `project.json.pattern=B`; consolidate the
  per-module ARCH file content into ARCHITECTURE.md (either flat or as a Layer
  Contract); the `module` field on phase records becomes inert (the assembler
  ignores it under Pattern B) — stop setting it on new phases; delete the
  per-module file. Useful when modules turn out to be more coupled than the
  original split anticipated.

Neither migration is structural. The cost is the doc edit; nothing in
the assembler, runner, or state machine changes.

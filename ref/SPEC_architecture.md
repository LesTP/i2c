# ARCHITECTURE.md — Output Spec

Defines the required sections for `ARCHITECTURE.md` and per-module
`ARCH_<module>.md` files in i2c projects. Produce them through whatever
process works — structured decomposition, freeform analysis, research
doc synthesis. See `ref/GUIDE_architecture.md` for the guided process.

**Audience.** Operator + assistant during collaborative architecture
sessions. This file is *not* read by the assembler or included in
worker prompts — it's a human-facing reference for authoring ARCH
content that the framework will then assemble verbatim.

**Exit criterion (stability check).** Before architecture is done:

- [ ] Every module has a clear, single responsibility
- [ ] Data flow is mapped — you know what objects exist, who owns them, how they move
- [ ] Interface contracts are defined for every module boundary
- [ ] Provisional contracts are marked and have a plan for resolution
- [ ] Implementation sequence is dependency-valid and risk-aware
- [ ] Coupling is visible — you can quickly assess the impact of a change
- [ ] Key decisions are recorded with revisit conditions
- [ ] PROJECT.md's extension points are reflected in architectural choices
- [ ] Every module to be implemented under i2c has an ARCH file with the **Required** sections complete (see per-module template below)

---

## How this fits the i2c loop

i2c is **two-layer**: prose contract on one side, structured state on
the other. PLAN bridges them.

- **Prose contract** — `PROJECT.md`, `ARCHITECTURE.md`, `ARCH_<module>.md`.
  Loaded into the worker's prompt by the assembler. Read by the LLM
  worker as authoritative spec. Authored in collaborative sessions
  before any phase runs.
- **Structured state** — `.state/project.json`, `phases.json`,
  `steps.json`, `decisions.json`, `devlog.jsonl`. Read by the state
  machine, assembler, invariant checks. Written by the worker via
  `tools/state.py`. Source of truth for "what phase are we on, what
  step is next, what's complete."
- **PLAN action** reads the ARCH file (specifically `## Phasing`) and
  transcribes it into `steps.json` records. EXECUTE then works against
  those structured records, not the prose.

So the ARCH file is the **input to PLAN** and the **contract the worker
adheres to**. Its job is to be precise enough that PLAN can transcribe
mechanically and EXECUTE can implement without re-deciding architecture.

Progress tracking is in `.state/phases.json` (each record carries
`status: pending | complete`). `ARCHITECTURE.md`'s Implementation
Sequence table is a human-readable mirror, kept in sync by
`instructions/close.md` step 7. Both exist; the structured form is
authoritative.

---

## ARCHITECTURE.md Template (project-level)

Compact summary, 1-2 pages. Loaded by the assembler for PLAN and REVIEW
actions; visible to operators always.

```markdown
# [Project Name] — Architecture

## Component Map
| Component | Responsibility | Dependencies |
|-----------|---------------|--------------|
| [Name] | [One sentence] | [List or "none"] |

## Data Flow

### Core Objects
- **[ObjectName]** — [shape sketch: key fields and types]

### Flow
[How objects move through components. Text, diagram, or arrow notation.]

## Interaction Model
### User Actions
- [Primary action]
### UI States
- [State] — [which components are active, what's visible]
### Layout Zones
- [Zone] — [what it contains, which component owns it]

(Omit this section for non-interactive projects)

## Implementation Sequence
| Order | Module | Rationale | Status |
|-------|--------|-----------|--------|
| 1 | [Name] | [Why first — leaf, highest risk, etc.] | Not started |

(Human-readable mirror of `.state/phases.json`. Updated at CLOSE per
`instructions/close.md` step 7. Status values: Not started | In progress | Complete.)

## Coupling Notes
- [Component A] ↔ [Component B]: [tight/loose], [why, what to watch]
- [Extension point] → affects [components], change is [additive/structural]

## Key Decisions
D-1: [Title]
Date: YYYY-MM-DD | Status: Closed
Decision: [What was chosen]
Rationale: [Why]
Revisit if: [Condition]

## Provisional Contracts
- [Contract between X and Y] — uncertain because [reason]. Resolve during [module].
```

---

## ARCH_[module].md Template (per-module)

One per module. Loaded by the assembler into every worker prompt for
the phase that targets this module. A downstream module should be able
to integrate using only this file.

**Exclude:** implementation details, internal types, design rationale.

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

(Repeat for each public surface element. Types may be inlined here as
code blocks or pulled into a separate `## Types` / `## Core Objects`
section — both patterns are common in existing ARCH files. Pick what
reads clearer for this module.)

## Inputs                                                   [Required]
[What this module accepts from outside — formats, valid ranges, edge
cases. Often overlaps with Public API parameters; restate at the
data-flow level here.]

## Outputs                                                  [Required]
[What this module produces — formats, guarantees, edge cases.]

## State                                                    [Required]
[What state this module owns, if any. Where it lives, who can modify
it. Write "None. [Module] is a pure function over typed inputs." if
that's accurate.]

## Usage Example                                            [Required]
[Minimal working integration — enough to wire this module into a
consumer. Real code, not pseudocode.]

## Phasing in This Pilot                                    [Required]
[Per-step or per-phase bullet list, ordered. This is what PLAN reads
to author `.state/steps.json` records autonomously. Two valid shapes:]

[Single-phase module (one phase, N steps):]
- **Step P.1** ships [scope]. ~N tests in `tests/test_X.py`.
- **Step P.2** extends [prior step] to cover [scope]. ~N tests.
- ...

[Multi-phase module (delivered across N phases as subset → full):]
- **Phase P implements:** [strict subset — what ships]. No [excluded scope].
- **Phase P+1 implements:** [remainder — what completes the contract].
- Both phases target this same ARCH_[module].md contract; Phase P
  delivers a strict subset; Phase P+1 completes it.

## Escalation Triggers                                      [Required]
[Module-specific conditions under which PLAN or EXECUTE halts to
`state=audit_escalation` and emits `EXIT 2`. Project-general triggers
(three-strikes, cross-module breakage, contract drift affecting a
built module) come from WORKER_SPEC — do not repeat. List only what is
specific to this module.]

- **[Trigger name]** — PLAN halts if [precondition that's specific to
  this module]. Recovery: [what the operator does to clear it].
- **[Trigger name]** — EXECUTE halts if [runtime condition specific to
  this module]. Recovery: [what the operator does].

[Examples for engine modules: "the canonical source (game rules doc,
v9 §X) contradicts the rule mapping below." For LLM wrappers: "the
provider's response shape differs from the schema." For data-access
modules: "the storage backend lacks a capability the contract assumes."]

## Inputs the [Module Name] Does Not Handle                 [Required]
[Explicit non-scope. What this module deliberately does not do; what
the caller / harness owns. Reduces the ambiguity that gets
re-discovered every time a new consumer integrates.]

- **[Capability]** — owned by [caller / other module]. [Module] assumes
  input is already [validated / normalized / etc.].
- **[Capability]** — handled by [downstream consumer]. Out of scope.

## Testing Strategy                                         [Recommended]
[Informs PLAN's per-step test counts and shape. What tests do we
expect this module to carry, and at what stages?]

- **[Test category]** — [coverage description]. [Where: `tests/test_X.py`].
- **Property tests** — [invariants the module promises].
- **Phasing alignment** — [which test categories ship in which step].

## Provisional Contracts                                    [Recommended]
[Things this ARCH commits to but expects to evolve. Marks where
downstream consumers should not over-couple.]

- **[Contract surface]** — [sketched here but may change]. Will firm up
  when [trigger condition]. Until then, [consumer guidance].

## Dependencies                                             [Recommended]
[Module-level coupling declaration. Mirror of
`.state/phases.json[id=P].dependencies` for this module's phase(s),
plus rationale.]

- **[Other module]** — [what we import from it / what surface we depend on].
- **External:** [third-party libraries, services, schemas this module needs].

## [Domain-specific section]                                [Optional]
[Free-form. Add as many domain-specific sections as the module
warrants. Examples seen in existing ARCH files: lookup tables
(Defense Bonus, Casualty Allocation, Importance Mapping), error
hierarchies, ledger formats, schema definitions, activation types,
behavior modes. The template explicitly permits these — existing ARCH
files derive much of their value from this content.]
```

---

## Combined Spec Template (single-file project)

For projects where internal boundaries aren't needed — one combined
document covers architecture and module contract together.

```markdown
# [Project Name] — Spec

## Overview                                                 [Required]
[One paragraph — what this is and what it does]

## Core Objects                                             [Required]
- **[ObjectName]** — [shape sketch]

## Interaction Model
(Omit for non-interactive projects)

## Public API / Interface                                   [Required]
### [function/method name]
- **Signature:** [typed signature]
- **Parameters:** [name, type, valid ranges]
- **Returns:** [type, guarantees]
- **Errors:** [what can fail, how failures surface]

## State                                                    [Required]
[What state exists, where it lives]

## Phasing in This Pilot                                    [Required]
[Same shape as the per-module template's Phasing section. Single-file
projects still need this — PLAN reads from here.]

## Escalation Triggers                                      [Required]
[Project-specific halt conditions for PLAN / EXECUTE.]

## Inputs the [Project] Does Not Handle                     [Required]
[Explicit non-scope.]

## Key Decisions                                            [Required]
D-1: [Title]
Decision: [What was chosen]
Rationale: [Why]
Revisit if: [Condition]

## Extension Points                                         [Recommended]
- [Structural observation about likely growth]

## Provisional Contracts                                    [Recommended]
- [Anything uncertain that implementation will resolve]

## Testing Strategy                                         [Recommended]
[Project-level test posture.]
```

---

## Variant Pattern: MVP / Full Split

For modules delivered in two stages — a strict forward-compatible
subset first, the full contract later. Phosphene's
`ARCH_orchestrator.md` + `ARCH_orchestrator_mvp.md` exemplifies.

Two files:

- `ARCH_<module>_mvp.md` — strict subset. All sections per the
  per-module template. Adds one section near the top:

  ```markdown
  ## Relationship to ARCH_<module>.md                       [Required]
  This spec is a strict subset of `ARCH_<module>.md`. Every type,
  method, and behavior defined here is forward-compatible with the
  full contract. When the full module ships, this MVP implementation
  either extends in place or gets replaced — no downstream modules
  change.

  **Deferred to the full module:** [explicit list of full-contract
  features excluded from MVP].
  ```

- `ARCH_<module>.md` — full contract. Standard per-module template.

Both reference each other. `.state/phases.json` records which file the
current phase targets via the `module` field (`module: "orchestrator_mvp"`
vs `module: "orchestrator"`).

Use when:
- The full contract is large enough that delivering it as one phase
  doesn't fit a reasonable step budget.
- The MVP is genuinely useful before the full module ships (i.e., the
  subset is releasable, not a half-built thing).
- The forward-compatibility commitment is real and can be verified by
  the MVP's Phasing section explicitly excluding anything that would
  break the contract later.

Skip this pattern unless all three apply.

---

## Section Naming Convention

Use Title Case for all section headings (`## Phasing in This Pilot`,
not `## phasing in this pilot`). Match the templates above verbatim
where applicable. Domain-specific sections use Title Case per project
convention.

This matches `ARCH_assembler.md` §4.1's "Title Case section names
throughout" rule for sections the assembler reads. ARCH content is
assembled verbatim, so the section names you author here appear
verbatim in the worker's prompt.

---

## When in Doubt

- Read `clankercourts/ARCH_validator.md` for a single-phase non-leaf
  module with explicit Phasing per step.
- Read `clankercourts/ARCH_resolver.md` for a multi-phase module with
  Phasing per phase (subset → full).
- Read `phosphene/ARCH_orchestrator_mvp.md` for the MVP / full split
  pattern.
- Read `toolkit/ARCH_embedding.md` for a minimal leaf module ARCH —
  what the Required sections look like when there's no domain content
  to embellish with.

These were the seed examples for this template; they predate the
Required / Recommended / Optional taxonomy but otherwise represent
the shape this spec codifies.

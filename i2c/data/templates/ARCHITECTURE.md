# [Project Name] — Architecture

> Project architecture document. `i2c init` scaffolded this from the packaged
> template. The assembler includes it verbatim in the PLAN and REVIEW prompts
> (`## Architecture`), and the CLOSE action keeps the Implementation Sequence
> table's `Status` column current. Author per-module contracts in
> `ARCH_<module>.md` (see `ref/SPEC_architecture.md`).

## Component Map

<!-- One row per module: name, responsibility, and the modules it depends on.
Leaf modules depend on nothing inside this project. -->

| Module | Responsibility | Dependencies |
|--------|----------------|--------------|
| <!-- e.g. event_store --> | <!-- one line --> | <!-- (none) or names --> |

## Implementation Sequence

<!-- The phase order. CLOSE flips a phase's Status to `Complete`. -->

| id | module | regime | Status |
|----|--------|--------|--------|
| 1  | <!-- module --> | build | Pending |

## Coupling Notes

<!-- Non-obvious couplings between modules, and why they exist. -->

## Key Decisions

<!-- One-line summaries of project-wide decisions. Full records live in
`.state/decisions.json`. -->

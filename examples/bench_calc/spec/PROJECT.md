# bench-calc

> Project scope document. The assembler includes this verbatim in the PLAN
> action's prompt (`## Project Scope`).

## Scope
A small pure-Python calculator module, `calc`, specified exactly in
`ARCHITECTURE.md`. `add` is already implemented; the one build phase adds a
typed error, `subtract`, `multiply`, `divide`, and a one-operator string
evaluator. Out of scope: anything beyond the `calc` contract (no CLI,
packaging, or extra modules).

## Constraints
- Python 3.10+, standard library only. No new dependencies, network, or file I/O.
- All code lives in `calc.py`; tests use `pytest` and live under `tests/`.
- Every worker action runs autonomously; nobody steps in during an action.

## Success Criteria
- `python -m pytest -q` passes: the existing `tests/test_calc.py` and the
  phase's frozen acceptance suite under `tests/acceptance/phase_1/`.
- Every action ends with a valid exit signal and passes the runner's action
  contract.

## Risks
- The contract is exact on purpose; read the number grammar and the sign rule
  before writing tests or code.

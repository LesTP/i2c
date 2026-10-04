# bench-calc - Architecture

A small, fixed task for comparing worker models and backends on the full i2c
loop (`plan -> tests -> execute -> review -> close`). One Python module, one
build phase, an exact contract, and a hidden reference suite that grades the
result independently of the tests the worker writes for itself.

## Overview

`calc.py` starts with `add(a, b)` already implemented and tested
(`tests/test_calc.py`). Phase 1 extends it with a typed error, the other three
arithmetic operations, and a one-operator expression evaluator.

## Module: calc (contract)

`calc.py` is a single pure-Python module (standard library only; no I/O, no
global state). It exposes:

- `add(a, b)` - return the arithmetic sum `a + b`. Already implemented; must
  stay unchanged.
- `class CalcError(ValueError)` - the one error type the module raises for
  invalid input. Callers can catch it as `ValueError`.
- `subtract(a, b)` - return `a - b`.
- `multiply(a, b)` - return `a * b`.
- `divide(a, b)` - return the true quotient `a / b` (always a `float`).
  Raises `CalcError` when `b == 0`; never raises `ZeroDivisionError`.
- `add` / `subtract` / `multiply` / `divide` do no input-type validation of
  their own (behavior for non-numeric arguments is unspecified); only
  `evaluate` validates its input.
- `evaluate(expr)` - evaluate a one-operator string expression
  `"<number> <op> <number>"`:
  - `<op>` is one of `+ - * /`, dispatched to `add` / `subtract` / `multiply` /
    `divide` respectively. Any other operator (e.g. `^`, `%`, `**`, `//`) is
    unknown.
  - `<number>` is an optional leading `-`, then one or more ASCII digits
    `0-9`, then optionally a `.` followed by one or more ASCII digits. Nothing
    else is a number: `.5`, `5.`, `+3`, `1e3`, `0x10`, `1_000` and `1.2.3` are
    all malformed. Integer literals parse to `int`, decimal literals to
    `float`, so `evaluate("2 + 3") == 5` (an `int`) and
    `evaluate("1 / 4") == 0.25`.
  - A `-` is an operand's sign when it is the first non-whitespace character of
    the expression, or the first non-whitespace character after the operator;
    any other `-` is the subtraction operator. A sign must be directly followed
    by a digit. So `evaluate("2 - -3") == 5`, `evaluate("-2-3") == -5`,
    `evaluate("2 -3") == -1`, and `"- 3 + 1"` is malformed.
  - Whitespace (any `str.isspace()` characters) around the operator and at
    either end is optional (`"2+3"`, `" 2  *  3 "` are valid).
  - Raises `CalcError` for: a non-`str` argument, an empty or whitespace-only
    string, an unknown operator, a missing or extra operand, more than one
    operator, or a malformed number. Division by zero (including `-0`) raises
    `CalcError` (via `divide`).
  - Must not use `eval`, `exec`, or `ast.literal_eval` on the input.

Acceptance:
- `python -m pytest tests/test_calc.py -q` keeps passing.
- The phase's frozen acceptance suite under `tests/acceptance/phase_1/` passes;
  it covers every guarantee above, including each `CalcError` case.

## Implementation Sequence

1. **calc-ops** (build) - extend `calc.py` with `CalcError`, `subtract`,
   `multiply`, `divide`, and `evaluate` per the contract above, leaving `add`
   unchanged. Out of scope: operator precedence, parentheses, more than one
   operator, variables, and any CLI.

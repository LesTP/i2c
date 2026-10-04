"""Hidden reference suite for the bench-calc fixture (grades `calc.py`).

Not copied into instantiated projects: the worker writes its own acceptance
suite in the TESTS action; this one grades the result independently (see
../README.md, Provenance). Run it with grade.py, not inside a project.
"""

import ast
import inspect

import pytest

import calc
from calc import (
    CalcError,
    add,
    divide,
    evaluate,
    multiply,
    subtract,
)


# ---------------------------------------------------------------------------
# CalcError is the module's typed error, catchable as ValueError
# ---------------------------------------------------------------------------

def test_calc_error_is_value_error_subclass():
    assert issubclass(CalcError, ValueError)


def test_calc_error_is_catchable_as_value_error():
    with pytest.raises(ValueError):
        evaluate("2 +")


# ---------------------------------------------------------------------------
# add is unchanged from phase 1
# ---------------------------------------------------------------------------

def test_add_still_works():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


# ---------------------------------------------------------------------------
# subtract / multiply / divide success behavior
# ---------------------------------------------------------------------------

def test_subtract_returns_difference():
    assert subtract(7, 3) == 4
    assert subtract(3, 7) == -4


def test_multiply_returns_product():
    assert multiply(6, 7) == 42
    assert multiply(-2, 3) == -6


def test_divide_returns_true_quotient_as_float():
    assert divide(1, 4) == 0.25
    assert isinstance(divide(1, 4), float)


def test_divide_integral_result_is_float():
    result = divide(4, 2)
    assert result == 2.0
    assert isinstance(result, float)


def test_divide_by_zero_raises_calc_error_not_zero_division():
    with pytest.raises(CalcError):
        divide(1, 0)
    # It must be a CalcError/ValueError, never a ZeroDivisionError.
    with pytest.raises(ValueError):
        divide(1, 0)


# ---------------------------------------------------------------------------
# evaluate: success behavior for every operator
# ---------------------------------------------------------------------------

def test_evaluate_add():
    result = evaluate("2 + 3")
    assert result == 5
    assert isinstance(result, int)


def test_evaluate_subtract():
    assert evaluate("7 - 3") == 4


def test_evaluate_multiply():
    assert evaluate("6 * 7") == 42


def test_evaluate_divide():
    assert evaluate("1 / 4") == 0.25


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("2 + 3", 5),
        ("2 - 3", -1),
        ("2 * 3", 6),
        ("2 / 4", 0.5),
    ],
)
def test_evaluate_dispatches_each_operator(expr, expected):
    assert evaluate(expr) == expected


# ---------------------------------------------------------------------------
# evaluate: number literal parsing -> int vs float
# ---------------------------------------------------------------------------

def test_evaluate_integer_literal_yields_int():
    result = evaluate("4 + 4")
    assert result == 8
    assert isinstance(result, int)


def test_evaluate_decimal_literal_yields_float():
    result = evaluate("1.5 + 2.5")
    assert result == 4.0
    assert isinstance(result, float)


def test_evaluate_integer_and_decimal_mix():
    # int + float -> float
    assert evaluate("2 + 0.5") == 2.5


# ---------------------------------------------------------------------------
# evaluate: whitespace is optional anywhere around operands/operator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr, expected",
    [
        ("2+3", 5),
        (" 2  *  3 ", 6),
        ("\t2\t-\t3\t", -1),
        ("\n2\n/\n4\n", 0.5),
    ],
)
def test_evaluate_whitespace_variants(expr, expected):
    assert evaluate(expr) == expected


# ---------------------------------------------------------------------------
# evaluate: sign disambiguation ("-" as operand sign vs subtraction operator)
# ---------------------------------------------------------------------------

def test_evaluate_leading_negative_operand():
    assert evaluate("-2 + 3") == 1


@pytest.mark.parametrize(
    "expr, expected",
    [
        ("2 - -3", 5),
        ("-2-3", -5),
        ("2 -3", -1),
        ("-2 - -0.5", -1.5),
        ("-2 * -3", 6),
        ("2 --3", 5),
    ],
)
def test_evaluate_sign_disambiguation(expr, expected):
    assert evaluate(expr) == expected


# ---------------------------------------------------------------------------
# evaluate: cross-product interactions (sign + decimal + operator)
# ---------------------------------------------------------------------------

def test_evaluate_negative_decimal_times_negative():
    assert evaluate("-2.5 * -2") == 5.0


def test_evaluate_negative_decimal_division():
    assert evaluate("-1.5 / 3") == -0.5


# ---------------------------------------------------------------------------
# evaluate: every malformed input raises CalcError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr",
    [
        5,
        None,
        3.14,
        ["2", "+", "3"],
    ],
)
def test_evaluate_non_string_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "   ",
        "\t\n ",
    ],
)
def test_evaluate_empty_or_whitespace_only_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "2 ^ 3",
        "2 % 3",
        "2 ** 3",
        "2 // 3",
    ],
)
def test_evaluate_unknown_operator_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "2 +",
        "+ 3",
        "- 3",
        "2",
        "2 + -",
        "2 + - 3",
    ],
)
def test_evaluate_missing_operand_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


@pytest.mark.parametrize(
    "expr",
    [
        "2 + 3 4",
        "2 3 + 4",
        "2 + 3 + 4",
        "2 * 3 / 4",
        "1 + 2 - 3",
    ],
)
def test_evaluate_extra_operand_or_multiple_operators_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


@pytest.mark.parametrize(
    "expr",
    [
        ".5 + 1",
        "5. + 1",
        "+3 + 1",
        "1e3 + 1",
        "1E3 + 1",
        "2 + 0x10",
        "2 + 1_000",
        "1.2.3 + 4",
    ],
)
def test_evaluate_malformed_number_raises(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


def test_evaluate_division_by_zero_raises_calc_error():
    with pytest.raises(CalcError):
        evaluate("1 / 0")


def test_evaluate_division_by_negative_zero_raises_calc_error():
    with pytest.raises(CalcError):
        evaluate("1 / -0")


# ---------------------------------------------------------------------------
# Contract guarantee: no eval / exec / ast.literal_eval on the input
# ---------------------------------------------------------------------------

def test_no_host_evaluation_primitives_used():
    tree = ast.parse(inspect.getsource(calc))
    forbidden_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in {"eval", "exec"}:
                forbidden_names.add(func.id)
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "literal_eval"
            ):
                forbidden_names.add("literal_eval")
    assert not forbidden_names, (
        "calc must not use eval/exec/ast.literal_eval, found: "
        f"{sorted(forbidden_names)}"
    )

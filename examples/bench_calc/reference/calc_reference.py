"""Reference implementation of the bench-calc contract (spec/ARCHITECTURE.md).

Exists only to prove the hidden reference suite is consistent with the spec:
i2c's tests run test_reference.py against this file. Never copied into an
instantiated project.
"""

_DIGITS = "0123456789"


class CalcError(ValueError):
    pass


def add(a, b):
    return a + b


def subtract(a, b):
    return a - b


def multiply(a, b):
    return a * b


def divide(a, b):
    if b == 0:
        raise CalcError("division by zero")
    return a / b


_OPS = {"+": add, "-": subtract, "*": multiply, "/": divide}


def _skip_space(s, i):
    while i < len(s) and s[i].isspace():
        i += 1
    return i


def _number(s, i):
    start = i
    if i < len(s) and s[i] == "-":
        i += 1
    digits = i
    while i < len(s) and s[i] in _DIGITS:
        i += 1
    if i == digits:
        raise CalcError(f"expected a number at position {start}")
    if i < len(s) and s[i] == ".":
        i += 1
        frac = i
        while i < len(s) and s[i] in _DIGITS:
            i += 1
        if i == frac:
            raise CalcError(f"expected digits after '.' at position {frac}")
    text = s[start:i]
    return (float(text) if "." in text else int(text)), i


def evaluate(expr):
    if not isinstance(expr, str):
        raise CalcError("expression must be a string")
    s = expr.strip()
    if not s:
        raise CalcError("empty expression")
    left, i = _number(s, 0)
    i = _skip_space(s, i)
    if i >= len(s) or s[i] not in _OPS:
        raise CalcError("expected one of + - * /")
    op = s[i]
    i = _skip_space(s, i + 1)
    right, i = _number(s, i)
    if _skip_space(s, i) != len(s):
        raise CalcError("unexpected trailing input")
    return _OPS[op](left, right)

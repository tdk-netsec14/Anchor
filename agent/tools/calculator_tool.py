"""Safe arithmetic tool.

The model supplies a string like ``"(450 * 0.15) + 20"``. That string is
untrusted input arriving from a language model, so it is *parsed* into an AST
and walked against a whitelist - never handed to ``eval``/``exec``.

The whitelist covers arithmetic only. Attribute access, subscripting, imports,
lambdas, comprehensions, f-strings, name binding and every statement form are
rejected by omission, because anything not explicitly permitted raises.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any

from pydantic import BaseModel, Field

from agent.tools.registry import Tool, ToolError

#: Longest expression accepted. Bounds the parse cost and log noise.
MAX_EXPRESSION_LENGTH = 200
#: Ceiling on any intermediate result, so ``9**9**9`` fails fast instead of
#: burning CPU on a bignum.
MAX_ABS_RESULT = 1e15
#: Largest permitted exponent, which also bounds the bignum blow-up.
MAX_EXPONENT = 1000

_BINARY_OPS: dict[type[ast.operator], Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

def _safe_round(value: float, ndigits: float = 0) -> float:
    """``round`` requires an int for ndigits, but every literal here is a float."""
    return round(value, int(ndigits))


_FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "min": min,
    "max": max,
    "round": _safe_round,
    "floor": math.floor,
    "ceil": math.ceil,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "pow": math.pow,
}


class CalculatorArgs(BaseModel):
    expression: str = Field(
        max_length=MAX_EXPRESSION_LENGTH,
        description="Arithmetic expression, e.g. '(1250 * 0.15) + 40'.",
    )


def _evaluate(node: ast.AST) -> float:
    """Recursively evaluate a whitelisted arithmetic AST."""
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)

    if isinstance(node, ast.Constant):
        # bool is a subclass of int; `True + True` is not a calculation.
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolError("Only numeric literals are allowed.")
        return float(node.value)

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BINARY_OPS:
            raise ToolError(f"Operator {op_type.__name__} is not allowed.")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if op_type is ast.Pow and abs(right) > MAX_EXPONENT:
            raise ToolError(f"Exponent magnitude is limited to {MAX_EXPONENT}.")
        try:
            result = _BINARY_OPS[op_type](left, right)
        except ZeroDivisionError as exc:
            raise ToolError("Division by zero.") from exc
        except (OverflowError, ValueError) as exc:
            raise ToolError("That calculation is out of range.") from exc
        _check_bounds(result)
        return result

    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ToolError(f"Unary operator {op_type.__name__} is not allowed.")
        return float(_UNARY_OPS[op_type](_evaluate(node.operand)))

    if isinstance(node, ast.Call):
        # A bare Name callee only; `().__class__` never gets this far.
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCTIONS:
            raise ToolError("Only basic math functions are allowed.")
        if node.keywords:
            raise ToolError("Keyword arguments are not supported.")
        args = [_evaluate(a) for a in node.args]
        try:
            result = float(_FUNCTIONS[node.func.id](*args))
        except TypeError as exc:
            raise ToolError("Wrong number of arguments for that math function.") from exc
        except ValueError as exc:
            raise ToolError(f"Math error: {exc}") from exc
        _check_bounds(result)
        return result

    raise ToolError(
        f"Expression element '{type(node).__name__}' is not allowed. "
        "Only numbers, + - * / // % **, parentheses and basic math functions "
        "(abs, min, max, round, floor, ceil, sqrt, log, log10, exp, pow) are permitted."
    )


def _check_bounds(value: float) -> None:
    if math.isnan(value) or math.isinf(value):
        raise ToolError("Result is not a finite number.")
    if abs(value) > MAX_ABS_RESULT:
        raise ToolError(f"Result magnitude is limited to {MAX_ABS_RESULT:g}.")


def safe_calculate(expression: str) -> float:
    """Evaluate an arithmetic expression, or raise :class:`ToolError`."""
    if not isinstance(expression, str) or not expression.strip():
        raise ToolError("Expression must be a non-empty string.")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise ToolError(f"Expression is limited to {MAX_EXPRESSION_LENGTH} characters.")
    if "\x00" in expression:
        raise ToolError("Expression contains an illegal character.")

    try:
        # Parse the stripped form: leading whitespace is an IndentationError
        # in eval mode, and a model may well send "  2 + 2".
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise ToolError(f"Could not parse the expression: {exc.msg}.") from exc

    return _evaluate(tree)


class CalculatorTool(Tool):
    name = "calculator"
    description = (
        "Evaluate an arithmetic expression exactly. Use this for any "
        "calculation - currency, percentages, totals, differences - instead of "
        "computing it yourself. Only numbers, + - * / // % **, parentheses and "
        "the functions abs, min, max, round, floor, ceil, sqrt, log, log10, exp "
        "and pow are supported."
    )
    args_model = CalculatorArgs

    def run(self, **kwargs: Any) -> str:
        value = safe_calculate(kwargs["expression"])
        # Render integers without a trailing ".0" so 2*21 reads as "42".
        rendered = str(int(value)) if value.is_integer() and abs(value) <= MAX_ABS_RESULT else repr(value)
        return f"{rendered} (from: {kwargs['expression']})"

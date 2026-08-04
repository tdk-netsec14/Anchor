"""Adversarial tests for the calculator tool.

The security property under test is simple and absolute: a string produced by a
language model can never cause code execution, filesystem access or an import.
"""

from __future__ import annotations

import pytest

from agent.tools.calculator_tool import CalculatorTool, safe_calculate
from agent.tools.registry import UNSCOPED, ToolError

pytestmark = pytest.mark.security


# --------------------------------------------------------------------------
# It computes
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1 + 1", 2.0),
        ("2 * 21", 42.0),
        ("(1250 * 0.15) + 40", 227.5),
        ("100 / 4", 25.0),
        ("7 // 2", 3.0),
        ("7 % 3", 1.0),
        ("2 ** 10", 1024.0),
        ("-5 + 3", -2.0),
        ("sqrt(144)", 12.0),
        ("max(3, 9, 4)", 9.0),
        ("round(3.14159, 2)", 3.14),
        ("  42  ", 42.0),
    ],
)
def test_valid_arithmetic(expression: str, expected: float) -> None:
    assert safe_calculate(expression) == pytest.approx(expected)


# --------------------------------------------------------------------------
# It must never execute anything
# --------------------------------------------------------------------------
HOSTILE_EXPRESSIONS = [
    "__import__('os').system('echo pwned')",
    "__import__('subprocess').run(['whoami'])",
    "open('/etc/passwd').read()",
    "open('C:/Windows/System32/config/SAM', 'r').read()",
    "().__class__.__bases__[0].__subclasses__()",
    "eval('1+1')",
    "exec('import os')",
    "compile('x=1', '<s>', 'exec')",
    "globals()",
    "locals()",
    "vars()",
    "[x for x in range(10)]",
    "(lambda: 1)()",
    "lambda x: x",
    "print('hello')",
    "import os",
    "os.system('dir')",
    "1; import os",
    "1 if True else __import__('os')",
    "[].append(1)",
    "'abc'.upper()",
    "1 .__class__",
    "f'{1}'",
    "{'a': 1}['a']",
    "await something()",
    "yield 5",
]

@pytest.mark.parametrize("expression", HOSTILE_EXPRESSIONS)
def test_hostile_expressions_are_rejected(expression: str) -> None:
    with pytest.raises(ToolError):
        safe_calculate(expression)


@pytest.mark.parametrize(
    "expression",
    ["1/0", "1 % 0", "sqrt(-1)", "log(0)", "10 ** 5000", "2 ** 999999999"],
)
def test_hostile_or_undefined_math_is_rejected_cleanly(expression: str) -> None:
    """Must raise ToolError, never ZeroDivisionError/OverflowError/blow up CPU."""
    with pytest.raises(ToolError):
        safe_calculate(expression)


def test_non_string_and_empty_expressions_are_rejected() -> None:
    for value in ["", "   ", None, 123, [1, 2]]:
        with pytest.raises(ToolError):
            safe_calculate(value)  # type: ignore[arg-type]


def test_oversized_expression_is_rejected() -> None:
    with pytest.raises(ToolError):
        safe_calculate("1+" * 500 + "1")


def test_tool_returns_a_readable_result() -> None:
    # Called directly, outside a request, so there is no workspace scope.
    result = CalculatorTool().run(UNSCOPED, expression="2 * 21")
    assert result.startswith("42")


# --------------------------------------------------------------------------
# Argument validation at the registry boundary
# --------------------------------------------------------------------------
def test_tool_rejects_arguments_that_are_not_an_object() -> None:
    tool = CalculatorTool()
    with pytest.raises(ToolError):
        tool.parse_arguments("[1, 2, 3]")
    with pytest.raises(ToolError):
        tool.parse_arguments("not json at all")


def test_tool_accepts_json_string_and_object_arguments() -> None:
    tool = CalculatorTool()
    assert tool.parse_arguments('{"expression": "1+1"}') == {"expression": "1+1"}
    assert tool.parse_arguments({"expression": "1+1"}) == {"expression": "1+1"}


def test_tool_rejects_missing_and_oversized_arguments() -> None:
    tool = CalculatorTool()
    with pytest.raises(ToolError):
        tool.parse_arguments({})
    with pytest.raises(ToolError):
        tool.parse_arguments({"expression": "x" * 5000})

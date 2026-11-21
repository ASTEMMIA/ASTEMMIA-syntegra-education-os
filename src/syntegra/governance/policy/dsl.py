"""The policy rule DSL (CEDA §29.1).

A safe, sandboxed expression language over named facts, for example::

    learner.attendance_rate_6w < min_rate and learner.age >= 6

* Dotted names (``learner.age``) are *facts* supplied at evaluation time.
* Bare names (``min_rate``) are *parameters* of the rule version.
* Only comparisons, boolean logic, arithmetic, literals, ``in`` over literal lists and
  ``abs``/``min``/``max`` are allowed. There is no attribute access on objects, no
  subscripts, no lambdas, no comprehensions and no arbitrary calls.

Expressions are parsed with ``ast``, checked against a whitelist and compiled into a tree of
Python closures. Nothing is ever passed to ``eval`` or ``exec``.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from syntegra.core.errors import ValidationFailed

MAX_LENGTH = 2000
MAX_DEPTH = 32

Facts = Mapping[str, Any]
Params = Mapping[str, Any]
Evaluator = Callable[[Facts, Params], Any]


class PolicyEvaluationError(Exception):
    """A fact was missing or had the wrong type: the rule result is 'unknown'."""


_CMP: dict[type[ast.cmpop], Callable[[Any, Any], bool]] = {
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}
_BIN: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_FUNCS: dict[str, Callable[..., Any]] = {"abs": abs, "min": min, "max": max}


@dataclass(frozen=True, slots=True)
class CompiledRule:
    source: str
    facts: frozenset[str]
    params: frozenset[str]
    _fn: Evaluator

    def evaluate(self, facts: Facts, params: Params | None = None) -> bool:
        try:
            result = self._fn(facts, params or {})
        except PolicyEvaluationError:
            raise
        except (TypeError, ZeroDivisionError) as exc:
            raise PolicyEvaluationError(str(exc)) from exc
        if not isinstance(result, bool):
            raise PolicyEvaluationError("rule must evaluate to true or false")
        return result


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


class _Compiler:
    def __init__(self) -> None:
        self.facts: set[str] = set()
        self.params: set[str] = set()

    def fail(self, node: ast.AST, msg: str) -> ValidationFailed:
        col = getattr(node, "col_offset", 0)
        return ValidationFailed(
            f"{msg} (at column {col + 1})",
            next_action="Use comparisons, and/or/not, arithmetic and named facts only.",
        )

    def compile(self, node: ast.AST, depth: int = 0) -> Evaluator:
        if depth > MAX_DEPTH:
            raise self.fail(node, "expression is nested too deeply")
        d = depth + 1

        if isinstance(node, ast.Expression):
            return self.compile(node.body, d)

        if isinstance(node, ast.BoolOp):
            parts = [self.compile(v, d) for v in node.values]
            if isinstance(node.op, ast.And):
                return lambda f, p: all(bool(x(f, p)) for x in parts)
            return lambda f, p: any(bool(x(f, p)) for x in parts)

        if isinstance(node, ast.UnaryOp):
            inner = self.compile(node.operand, d)
            if isinstance(node.op, ast.Not):
                return lambda f, p: not inner(f, p)
            if isinstance(node.op, ast.USub):
                return lambda f, p: -inner(f, p)
            raise self.fail(node, "unsupported unary operator")

        if isinstance(node, ast.BinOp):
            op = _BIN.get(type(node.op))
            if op is None:
                raise self.fail(node, "unsupported arithmetic operator")
            left, right = self.compile(node.left, d), self.compile(node.right, d)
            return lambda f, p: op(left(f, p), right(f, p))

        if isinstance(node, ast.Compare):
            left = self.compile(node.left, d)
            ops: list[Callable[[Any, Any], bool]] = []
            for o in node.ops:
                fn = _CMP.get(type(o))
                if fn is None:
                    raise self.fail(node, "unsupported comparison")
                ops.append(fn)
            rights = [self.compile(c, d) for c in node.comparators]

            def compare(f: Facts, p: Params) -> bool:
                a = left(f, p)
                for fn, r in zip(ops, rights, strict=True):
                    b = r(f, p)
                    if not fn(a, b):
                        return False
                    a = b
                return True

            return compare

        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool)) and node.value is not None:
                raise self.fail(node, "unsupported literal")
            value = node.value
            return lambda f, p: value

        if isinstance(node, (ast.List, ast.Tuple)):
            items = [self.compile(e, d) for e in node.elts]
            if not all(isinstance(e, ast.Constant) for e in node.elts):
                raise self.fail(node, "lists may contain literals only")
            return lambda f, p: tuple(i(f, p) for i in items)

        if isinstance(node, ast.Attribute):
            name = _dotted(node)
            if name is None or any(part.startswith("_") for part in name.split(".")):
                raise self.fail(node, "invalid fact name")
            self.facts.add(name)

            def fact(f: Facts, p: Params, _n: str = name) -> Any:
                if _n not in f:
                    raise PolicyEvaluationError(f"missing fact {_n}")
                return f[_n]

            return fact

        if isinstance(node, ast.Name):
            if node.id.startswith("_") or node.id in _FUNCS:
                raise self.fail(node, "invalid parameter name")
            if node.id in {"true", "false"}:  # author-friendly boolean literals
                literal = node.id == "true"
                return lambda f, p: literal
            self.params.add(node.id)

            def param(f: Facts, p: Params, _n: str = node.id) -> Any:
                if _n not in p:
                    raise PolicyEvaluationError(f"missing parameter {_n}")
                return p[_n]

            return param

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                raise self.fail(node, "only abs(), min() and max() may be called")
            fn = _FUNCS[node.func.id]
            args = [self.compile(a, d) for a in node.args]
            return lambda f, p: fn(*(a(f, p) for a in args))

        raise self.fail(node, f"{type(node).__name__} is not allowed in policy rules")


def compile_rule(source: str) -> CompiledRule:
    text = source.strip()
    if not text:
        raise ValidationFailed("rule expression is empty")
    if len(text) > MAX_LENGTH:
        raise ValidationFailed(
            "rule expression is too long", next_action="Split it into smaller rules."
        )
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ValidationFailed(f"rule expression is not valid: {exc.msg}") from exc
    compiler = _Compiler()
    fn = compiler.compile(tree)
    return CompiledRule(text, frozenset(compiler.facts), frozenset(compiler.params), fn)

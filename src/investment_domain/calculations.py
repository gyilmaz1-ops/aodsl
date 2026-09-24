"""Deterministic Calculation domain semantics — CEL v1."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    ROUND_HALF_EVEN,
    localcontext,
)
from typing import Iterable

from .nodes import Calculation


class CalculationEvaluationError(ValueError):
    """Deterministic calculation evaluation failure."""


class CalculationUnitError(CalculationEvaluationError):
    """Calculation unit-algebra violation."""


@dataclass(frozen=True)
class _Expr:
    op: str
    args: tuple["_Expr", ...] = ()
    ref_index: int | None = None
    const_value: Decimal | None = None


_IDENT_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_INTEGER_RE = re.compile(r"0|[1-9][0-9]*")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")

_ALLOWED_OPERATORS = {
    "ADD",
    "SUB",
    "MUL",
    "DIV",
    "NEG",
    "ABS",
    "MIN",
    "MAX",
    "SUM",
    "AVG",
}

_CANONICAL_INPUT_ID_RE = re.compile(
    r"(?:metric|calculation):[0-9a-f]{64}"
)


class _Parser:
    def __init__(self, source: str):
        self.source = source
        self.pos = 0

    def parse(self) -> _Expr:
        if not isinstance(self.source, str) or not self.source:
            raise ValueError("Calculation formula must be a non-empty string")

        expr = self._parse_expr()

        if self.pos != len(self.source):
            raise ValueError(
                f"Unexpected formula content at position {self.pos}"
            )

        return expr

    def _peek(self) -> str | None:
        if self.pos >= len(self.source):
            return None
        return self.source[self.pos]

    def _consume(self, expected: str) -> None:
        if not self.source.startswith(expected, self.pos):
            raise ValueError(
                f"Expected {expected!r} at position {self.pos}"
            )
        self.pos += len(expected)

    def _match(self, regex: re.Pattern[str]) -> str:
        match = regex.match(self.source, self.pos)
        if match is None:
            raise ValueError(
                f"Invalid token at position {self.pos}"
            )
        self.pos = match.end()
        return match.group(0)

    def _parse_expr(self) -> _Expr:
        op = self._match(_IDENT_RE)
        self._consume("(")

        if op == "REF":
            raw = self._match(_INTEGER_RE)
            self._consume(")")
            return _Expr(op="REF", ref_index=int(raw))

        if op == "CONST":
            raw = self._match(_DECIMAL_RE)
            self._consume(")")
            return _Expr(op="CONST", const_value=Decimal(raw))

        if op not in _ALLOWED_OPERATORS:
            raise ValueError(f"Unsupported CEL v1 operator: {op}")

        args = [self._parse_expr()]

        while self._peek() == ",":
            self._consume(",")
            args.append(self._parse_expr())

        self._consume(")")

        self._validate_arity(op, len(args))
        return _Expr(op=op, args=tuple(args))

    @staticmethod
    def _validate_arity(op: str, count: int) -> None:
        if op in {"ADD", "SUB", "MUL", "DIV"} and count != 2:
            raise ValueError(f"{op} requires exactly two operands")

        if op in {"NEG", "ABS"} and count != 1:
            raise ValueError(f"{op} requires exactly one operand")

        if op in {"MIN", "MAX", "SUM", "AVG"} and count < 1:
            raise ValueError(f"{op} requires at least one operand")


def parse_formula(formula: str) -> _Expr:
    """Parse canonical CEL v1 syntax."""

    return _Parser(formula).parse()


def _referenced_indices(expr: _Expr) -> Iterable[int]:
    if expr.op == "REF":
        assert expr.ref_index is not None
        yield expr.ref_index
        return

    for arg in expr.args:
        yield from _referenced_indices(arg)


def validate_calculation(calculation: Calculation) -> None:
    """Validate pure Calculation/CEL v1 domain invariants."""

    if not isinstance(calculation.subject_id, str) or not calculation.subject_id:
        raise ValueError("Calculation subject_id must be non-empty")

    if (
        not isinstance(calculation.model_version, str)
        or not calculation.model_version
    ):
        raise ValueError("Calculation model_version must be non-empty")

    if calculation.model_version != "cel-v1":
        raise ValueError(
            f"Unsupported Calculation model_version: "
            f"{calculation.model_version}"
        )

    if not isinstance(calculation.value, Decimal):
        raise ValueError("Calculation value must be Decimal")

    if not calculation.value.is_finite():
        raise ValueError("Calculation value must be finite")

    if not isinstance(calculation.input_ids, tuple):
        raise ValueError("Calculation input_ids must be a tuple")

    if not calculation.input_ids:
        raise ValueError("Calculation input_ids must be non-empty")

    if len(set(calculation.input_ids)) != len(calculation.input_ids):
        raise ValueError("Calculation input_ids must be duplicate-free")

    for input_id in calculation.input_ids:
        if not isinstance(input_id, str) or not input_id:
            raise ValueError("Calculation input IDs must be non-empty strings")

        if _CANONICAL_INPUT_ID_RE.fullmatch(input_id) is None:
            raise ValueError(
                "Calculation inputs must use canonical Metric or Calculation IDs"
            )

        if input_id == calculation.id:
            raise ValueError("Calculation cannot depend on itself")

    expr = parse_formula(calculation.formula)
    refs = tuple(_referenced_indices(expr))

    if any(index >= len(calculation.input_ids) for index in refs):
        raise ValueError("Calculation REF index out of range")

    if set(refs) != set(range(len(calculation.input_ids))):
        raise ValueError(
            "Every Calculation input must be referenced by formula"
        )


_CEL_V1_CONTEXT = Context(
    prec=34,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    capitals=1,
    clamp=0,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)


@dataclass(frozen=True)
class _Value:
    value: Decimal
    unit: str
    currency: str | None = None


_SUPPORTED_UNITS = {
    "currency",
    "percent",
    "ratio",
    "shares",
    "currency_per_share",
}


def _validate_currency(unit: str, currency: str | None) -> None:
    requires_currency = unit in {"currency", "currency_per_share"}

    if requires_currency:
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isascii()
            or not currency.isalpha()
            or currency != currency.upper()
        ):
            raise CalculationUnitError(
                f"{unit} requires three-letter uppercase ASCII currency"
            )
        return

    if currency is not None:
        raise CalculationUnitError(
            f"{unit} does not accept a currency qualifier"
        )


def _validate_value(value: _Value) -> None:
    if not isinstance(value.value, Decimal):
        raise CalculationEvaluationError(
            "Calculation inputs must use Decimal values"
        )

    if not value.value.is_finite():
        raise CalculationEvaluationError(
            "Calculation values must be finite"
        )

    if value.unit not in _SUPPORTED_UNITS:
        raise CalculationUnitError(
            f"Unsupported CEL v1 unit: {value.unit}"
        )

    _validate_currency(value.unit, value.currency)


def _same_unit(left: _Value, right: _Value) -> bool:
    return (
        left.unit == right.unit
        and left.currency == right.currency
    )


def _reject_percent(*values: _Value) -> None:
    if any(value.unit == "percent" for value in values):
        raise CalculationUnitError(
            "Percent arithmetic is forbidden in CEL v1"
        )


def _eval_expr(
    expr: _Expr,
    inputs: tuple[_Value, ...],
) -> _Value:
    if expr.op == "REF":
        assert expr.ref_index is not None
        return inputs[expr.ref_index]

    if expr.op == "CONST":
        assert expr.const_value is not None
        return _Value(expr.const_value, "ratio", None)

    values = tuple(_eval_expr(arg, inputs) for arg in expr.args)

    if expr.op in {"NEG", "ABS"}:
        value = values[0]
        _reject_percent(value)

        result = (
            -value.value
            if expr.op == "NEG"
            else abs(value.value)
        )
        return _Value(result, value.unit, value.currency)

    if expr.op in {"ADD", "SUB", "MIN", "MAX", "SUM", "AVG"}:
        _reject_percent(*values)

        first = values[0]
        if not all(_same_unit(first, item) for item in values[1:]):
            raise CalculationUnitError(
                f"{expr.op} requires identical units and currency qualifiers"
            )

        raw = tuple(item.value for item in values)

        if expr.op == "ADD":
            result = raw[0] + raw[1]
        elif expr.op == "SUB":
            result = raw[0] - raw[1]
        elif expr.op == "MIN":
            result = min(raw)
        elif expr.op == "MAX":
            result = max(raw)
        elif expr.op == "SUM":
            result = sum(raw, Decimal("0"))
        else:
            result = sum(raw, Decimal("0")) / Decimal(len(raw))

        return _Value(result, first.unit, first.currency)

    if expr.op == "MUL":
        left, right = values
        _reject_percent(left, right)

        if left.unit == "ratio" and right.unit == "ratio":
            return _Value(
                left.value * right.value,
                "ratio",
                None,
            )

        if left.unit == "currency" and right.unit == "ratio":
            return _Value(
                left.value * right.value,
                "currency",
                left.currency,
            )

        if left.unit == "ratio" and right.unit == "currency":
            return _Value(
                left.value * right.value,
                "currency",
                right.currency,
            )

        if (
            left.unit == "currency_per_share"
            and right.unit == "shares"
        ):
            return _Value(
                left.value * right.value,
                "currency",
                left.currency,
            )

        if (
            left.unit == "shares"
            and right.unit == "currency_per_share"
        ):
            return _Value(
                left.value * right.value,
                "currency",
                right.currency,
            )

        raise CalculationUnitError(
            f"Unsupported CEL v1 multiplication: "
            f"{left.unit} * {right.unit}"
        )

    if expr.op == "DIV":
        left, right = values
        _reject_percent(left, right)

        if right.value == 0:
            raise CalculationEvaluationError("Division by zero")

        if left.unit == "ratio" and right.unit == "ratio":
            return _Value(
                left.value / right.value,
                "ratio",
                None,
            )

        if left.unit == "currency" and right.unit == "currency":
            if left.currency != right.currency:
                raise CalculationUnitError(
                    "Currency division requires matching currencies"
                )
            return _Value(
                left.value / right.value,
                "ratio",
                None,
            )

        if left.unit == "currency" and right.unit == "shares":
            return _Value(
                left.value / right.value,
                "currency_per_share",
                left.currency,
            )

        if (
            left.unit == "currency"
            and right.unit == "currency_per_share"
        ):
            if left.currency != right.currency:
                raise CalculationUnitError(
                    "Currency division requires matching currencies"
                )
            return _Value(
                left.value / right.value,
                "shares",
                None,
            )

        raise CalculationUnitError(
            f"Unsupported CEL v1 division: "
            f"{left.unit} / {right.unit}"
        )

    raise CalculationEvaluationError(
        f"Unsupported CEL v1 expression: {expr.op}"
    )


def evaluate_calculation(
    calculation: Calculation,
    *,
    inputs,
    verify_materialized: bool = False,
):
    """Evaluate a Calculation deterministically under CEL v1."""

    validate_calculation(calculation)

    if not isinstance(inputs, tuple):
        raise CalculationEvaluationError(
            "Calculation inputs must be a tuple"
        )

    if len(inputs) != len(calculation.input_ids):
        raise CalculationEvaluationError(
            "Calculation input count does not match input_ids"
        )

    normalized = []

    for item in inputs:
        if not isinstance(item, tuple) or len(item) != 3:
            raise CalculationEvaluationError(
                "Each Calculation input must be "
                "(Decimal, unit, currency)"
            )

        value = _Value(item[0], item[1], item[2])
        _validate_value(value)
        normalized.append(value)

    expr = parse_formula(calculation.formula)

    try:
        with localcontext(_CEL_V1_CONTEXT):
            result = _eval_expr(expr, tuple(normalized))
    except CalculationEvaluationError:
        raise
    except (DivisionByZero, InvalidOperation, ArithmeticError) as exc:
        raise CalculationEvaluationError(
            "CEL v1 arithmetic evaluation failed"
        ) from exc

    _validate_value(result)

    if not result.value.is_finite():
        raise CalculationEvaluationError(
            "CEL v1 produced a non-finite result"
        )

    if verify_materialized:
        if calculation.unit != result.unit:
            raise CalculationUnitError(
                "Persisted Calculation unit does not match recomputation"
            )

        if calculation.currency != result.currency:
            raise CalculationUnitError(
                "Persisted Calculation currency does not match recomputation"
            )

        if calculation.value != result.value:
            raise CalculationEvaluationError(
                "Persisted Calculation value does not match recomputation"
            )

    return result.value, result.unit, result.currency

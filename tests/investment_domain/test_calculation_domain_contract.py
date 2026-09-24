from dataclasses import FrozenInstanceError
from decimal import Decimal, getcontext

import pytest

from investment_domain import Calculation
from investment_domain.calculations import (
    CalculationEvaluationError,
    CalculationUnitError,
    evaluate_calculation,
    validate_calculation,
)


def calc(
    *,
    id_override="calculation:test",
    formula="DIV(REF(0),REF(1))",
    input_ids=(
        "metric:" + ("a" * 64),
        "metric:" + ("b" * 64),
    ),
    value=Decimal("0.5"),
    unit="ratio",
    currency=None,
    model_version="cel-v1",
):
    return Calculation(
        id=id_override,
        subject_id="company:test",
        formula=formula,
        input_ids=input_ids,
        value=value,
        unit=unit,
        currency=currency,
        model_version=model_version,
    )


# ---------------------------------------------------------------------------
# Identity / immutability
# ---------------------------------------------------------------------------


def test_calculation_is_immutable():
    node = calc()

    with pytest.raises(FrozenInstanceError):
        node.value = Decimal("1")


def test_value_unit_and_currency_are_not_identity_bearing():
    from investment_domain.identity import canonical_id

    left = calc(
        value=Decimal("0.5"),
        unit="ratio",
        currency=None,
    )
    right = calc(
        value=Decimal("999"),
        unit="currency",
        currency="USD",
    )

    def identity(node):
        return canonical_id(
            "calculation",
            {
                "subject_id": node.subject_id,
                "formula": node.formula,
                "input_ids": node.input_ids,
                "model_version": node.model_version,
            },
        )

    assert identity(left) == identity(right)


def test_ordered_input_ids_are_identity_bearing():
    from investment_domain.identity import canonical_id

    left = calc(input_ids=(
        "metric:" + ("a" * 64),
        "metric:" + ("b" * 64),
    ))
    right = calc(input_ids=(
        "metric:" + ("b" * 64),
        "metric:" + ("a" * 64),
    ))

    def identity(node):
        return canonical_id(
            "calculation",
            {
                "subject_id": node.subject_id,
                "formula": node.formula,
                "input_ids": node.input_ids,
                "model_version": node.model_version,
            },
        )

    assert identity(left) != identity(right)


# ---------------------------------------------------------------------------
# Formula / dependency contract
# ---------------------------------------------------------------------------


def test_valid_formula_and_dependencies():
    validate_calculation(calc())


@pytest.mark.parametrize(
    "formula",
    [
        "REF(2)",
        "DIV(REF(0),REF(2))",
        "UNKNOWN(REF(0))",
        "gross_profit / revenue",
        "__import__('os').system('echo nope')",
    ],
)
def test_invalid_formula_fails_closed(formula):
    with pytest.raises(ValueError):
        validate_calculation(calc(formula=formula))


def test_duplicate_inputs_are_rejected():
    with pytest.raises(ValueError):
        validate_calculation(
            calc(input_ids=("metric:m1", "metric:m1"))
        )


def test_unused_inputs_are_rejected():
    with pytest.raises(ValueError):
        validate_calculation(
            calc(
                formula="REF(0)",
                input_ids=(
        "metric:" + ("a" * 64),
        "metric:" + ("b" * 64),
    ),
            )
        )


def test_empty_inputs_are_rejected():
    with pytest.raises(ValueError):
        validate_calculation(
            calc(
                formula="CONST(1)",
                input_ids=(),
            )
        )


def test_wrong_input_type_is_rejected():
    with pytest.raises(ValueError):
        validate_calculation(
            calc(
                formula="REF(0)",
                input_ids=("claim:c1",),
            )
        )


def test_direct_self_dependency_is_rejected():
    node = calc(
        formula="REF(0)",
        input_ids=("calculation:test",),
    )

    with pytest.raises(ValueError):
        validate_calculation(node)


# ---------------------------------------------------------------------------
# Deterministic evaluation
# ---------------------------------------------------------------------------


def test_division_evaluates_with_decimal():
    result = evaluate_calculation(
        calc(),
        inputs=(
            (Decimal("10"), "currency", "USD"),
            (Decimal("20"), "currency", "USD"),
        ),
    )

    assert result == (Decimal("0.5"), "ratio", None)


def test_subtraction_respects_input_order():
    node = calc(
        formula="SUB(REF(0),REF(1))",
        value=Decimal("7"),
        unit="currency",
        currency="USD",
    )

    result = evaluate_calculation(
        node,
        inputs=(
            (Decimal("10"), "currency", "USD"),
            (Decimal("3"), "currency", "USD"),
        ),
    )

    assert result == (Decimal("7"), "currency", "USD")


def test_dimensionless_const_supports_growth_formula():
    node = calc(
        formula="SUB(DIV(REF(0),REF(1)),CONST(1))",
        value=Decimal("0.25"),
        unit="ratio",
    )

    result = evaluate_calculation(
        node,
        inputs=(
            (Decimal("125"), "currency", "USD"),
            (Decimal("100"), "currency", "USD"),
        ),
    )

    assert result == (Decimal("0.25"), "ratio", None)


def test_division_by_zero_fails_closed():
    with pytest.raises(CalculationEvaluationError):
        evaluate_calculation(
            calc(),
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("0"), "currency", "USD"),
            ),
        )


def test_ambient_decimal_precision_does_not_change_result():
    node = calc(
        formula="DIV(REF(0),REF(1))",
        value=Decimal(1) / Decimal(7),
        unit="ratio",
    )

    original = getcontext().prec

    try:
        getcontext().prec = 6
        low_context = evaluate_calculation(
            node,
            inputs=(
                (Decimal("1"), "currency", "USD"),
                (Decimal("7"), "currency", "USD"),
            ),
        )

        getcontext().prec = 50
        high_context = evaluate_calculation(
            node,
            inputs=(
                (Decimal("1"), "currency", "USD"),
                (Decimal("7"), "currency", "USD"),
            ),
        )
    finally:
        getcontext().prec = original

    assert low_context == high_context


# ---------------------------------------------------------------------------
# Unit algebra
# ---------------------------------------------------------------------------


def test_add_same_currency_is_valid():
    node = calc(
        formula="ADD(REF(0),REF(1))",
        value=Decimal("30"),
        unit="currency",
        currency="USD",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("10"), "currency", "USD"),
            (Decimal("20"), "currency", "USD"),
        ),
    ) == (Decimal("30"), "currency", "USD")


def test_add_different_currencies_is_rejected():
    node = calc(
        formula="ADD(REF(0),REF(1))",
        unit="currency",
        currency="USD",
    )

    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            node,
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("20"), "currency", "EUR"),
            ),
        )


def test_currency_divided_by_shares_produces_currency_per_share():
    node = calc(
        formula="DIV(REF(0),REF(1))",
        value=Decimal("5"),
        unit="currency_per_share",
        currency="USD",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("100"), "currency", "USD"),
            (Decimal("20"), "shares", None),
        ),
    ) == (
        Decimal("5"),
        "currency_per_share",
        "USD",
    )


def test_percent_arithmetic_is_rejected_in_cel_v1():
    node = calc(
        formula="MUL(REF(0),REF(1))",
        unit="currency",
        currency="USD",
    )

    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            node,
            inputs=(
                (Decimal("100"), "currency", "USD"),
                (Decimal("20"), "percent", None),
            ),
        )


# ---------------------------------------------------------------------------
# Materialized result integrity
# ---------------------------------------------------------------------------


def test_persisted_value_must_match_recomputation():
    node = calc(value=Decimal("0.6"))

    with pytest.raises(CalculationEvaluationError):
        evaluate_calculation(
            node,
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("20"), "currency", "USD"),
            ),
            verify_materialized=True,
        )


def test_persisted_unit_must_match_recomputation():
    node = calc(
        value=Decimal("0.5"),
        unit="currency",
        currency="USD",
    )

    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            node,
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("20"), "currency", "USD"),
            ),
            verify_materialized=True,
        )


# ---------------------------------------------------------------------------
# CEL v1 hardening
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "formula",
    [
        "ADD(REF(0))",
        "ADD(REF(0),REF(1),REF(0))",
        "SUB(REF(0))",
        "MUL(REF(0))",
        "DIV(REF(0))",
        "NEG(REF(0),REF(1))",
        "ABS(REF(0),REF(1))",
    ],
)
def test_fixed_arity_operators_fail_closed(formula):
    with pytest.raises(ValueError):
        validate_calculation(calc(formula=formula))


@pytest.mark.parametrize(
    "formula",
    [
        "REF(00)",
        "REF(01)",
        "CONST(01)",
        "CONST(+1)",
        "CONST(1.)",
        "CONST(.5)",
        "CONST(1e2)",
        "CONST(1E+2)",
        " ADD(REF(0),REF(1))",
        "ADD(REF(0), REF(1))",
        "add(REF(0),REF(1))",
    ],
)
def test_noncanonical_formula_spellings_fail_closed(formula):
    with pytest.raises(ValueError):
        validate_calculation(calc(formula=formula))


@pytest.mark.parametrize(
    "input_id",
    [
        "metric:m1",
        "metric:",
        "metric:xyz",
        "calculation:test",
        "calculation:1234",
        "claim:" + ("a" * 64),
    ],
)
def test_noncanonical_dependency_ids_fail_closed(input_id):
    node = calc(
        formula="REF(0)",
        input_ids=(input_id,),
    )

    with pytest.raises(ValueError):
        validate_calculation(node)


def test_real_canonical_metric_dependency_id_is_accepted():
    input_id = "metric:" + ("a" * 64)
    node = calc(
        formula="REF(0)",
        input_ids=(input_id,),
    )

    validate_calculation(node)


def test_real_canonical_calculation_dependency_id_is_accepted():
    input_id = "calculation:" + ("b" * 64)
    node = calc(
        id_override="calculation:" + ("c" * 64),
        formula="REF(0)",
        input_ids=(input_id,),
    )

    validate_calculation(node)


@pytest.mark.parametrize(
    ("formula", "expected"),
    [
        ("NEG(REF(0))", Decimal("-10")),
        ("ABS(NEG(REF(0)))", Decimal("10")),
        ("MIN(REF(0),REF(1))", Decimal("3")),
        ("MAX(REF(0),REF(1))", Decimal("10")),
        ("SUM(REF(0),REF(1))", Decimal("13")),
        ("AVG(REF(0),REF(1))", Decimal("6.5")),
    ],
)
def test_remaining_cel_v1_operators(formula, expected):
    is_unary = formula in {
        "NEG(REF(0))",
        "ABS(NEG(REF(0)))",
    }

    input_ids = (
        ("metric:" + ("a" * 64),)
        if is_unary
        else (
            "metric:" + ("a" * 64),
            "metric:" + ("b" * 64),
        )
    )

    inputs = (
        ((Decimal("10"), "currency", "USD"),)
        if is_unary
        else (
            (Decimal("10"), "currency", "USD"),
            (Decimal("3"), "currency", "USD"),
        )
    )

    node = calc(
        formula=formula,
        input_ids=input_ids,
        value=expected,
        unit="currency",
        currency="USD",
    )

    result = evaluate_calculation(
        node,
        inputs=inputs,
    )

    assert result == (expected, "currency", "USD")


@pytest.mark.parametrize(
    ("unit", "currency"),
    [
        ("bogus", None),
        ("currency", None),
        ("currency", "usd"),
        ("currency", "US"),
        ("currency", "USDD"),
        ("currency", "ÜSD"),
        ("ratio", "USD"),
        ("shares", "USD"),
        ("percent", "USD"),
    ],
)
def test_invalid_input_unit_or_currency_fails_closed(unit, currency):
    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            calc(),
            inputs=(
                (Decimal("10"), unit, currency),
                (Decimal("20"), "currency", "USD"),
            ),
        )


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_nonfinite_input_values_fail_closed(value):
    with pytest.raises(CalculationEvaluationError):
        evaluate_calculation(
            calc(),
            inputs=(
                (value, "currency", "USD"),
                (Decimal("20"), "currency", "USD"),
            ),
        )


def test_binary_float_input_fails_closed():
    with pytest.raises(CalculationEvaluationError):
        evaluate_calculation(
            calc(),
            inputs=(
                (10.0, "currency", "USD"),
                (Decimal("20"), "currency", "USD"),
            ),
        )


def test_input_count_must_match_input_ids():
    with pytest.raises(CalculationEvaluationError):
        evaluate_calculation(
            calc(),
            inputs=((Decimal("10"), "currency", "USD"),),
        )


def test_materialized_currency_must_match_recomputation():
    node = calc(
        formula="ADD(REF(0),REF(1))",
        value=Decimal("30"),
        unit="currency",
        currency="EUR",
    )

    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            node,
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("20"), "currency", "USD"),
            ),
            verify_materialized=True,
        )


def test_ratio_multiplication_is_supported():
    node = calc(
        formula="MUL(REF(0),REF(1))",
        value=Decimal("6"),
        unit="ratio",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("2"), "ratio", None),
            (Decimal("3"), "ratio", None),
        ),
    ) == (Decimal("6"), "ratio", None)


def test_currency_times_ratio_is_supported():
    node = calc(
        formula="MUL(REF(0),REF(1))",
        value=Decimal("25"),
        unit="currency",
        currency="USD",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("100"), "currency", "USD"),
            (Decimal("0.25"), "ratio", None),
        ),
    ) == (Decimal("25"), "currency", "USD")


def test_currency_per_share_times_shares_is_currency():
    node = calc(
        formula="MUL(REF(0),REF(1))",
        value=Decimal("100"),
        unit="currency",
        currency="USD",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("5"), "currency_per_share", "USD"),
            (Decimal("20"), "shares", None),
        ),
    ) == (Decimal("100"), "currency", "USD")


def test_currency_divided_by_currency_per_share_is_shares():
    node = calc(
        formula="DIV(REF(0),REF(1))",
        value=Decimal("20"),
        unit="shares",
    )

    assert evaluate_calculation(
        node,
        inputs=(
            (Decimal("100"), "currency", "USD"),
            (Decimal("5"), "currency_per_share", "USD"),
        ),
    ) == (Decimal("20"), "shares", None)


def test_cross_currency_division_fails_closed():
    with pytest.raises(CalculationUnitError):
        evaluate_calculation(
            calc(),
            inputs=(
                (Decimal("10"), "currency", "USD"),
                (Decimal("20"), "currency", "EUR"),
            ),
        )


def test_cel_v1_decimal_context_is_fully_frozen():
    from decimal import (
        DivisionByZero,
        InvalidOperation,
        Overflow,
        ROUND_HALF_EVEN,
    )
    from investment_domain.calculations import _CEL_V1_CONTEXT

    ctx = _CEL_V1_CONTEXT

    assert ctx.prec == 34
    assert ctx.rounding == ROUND_HALF_EVEN
    assert ctx.Emin == -999999
    assert ctx.Emax == 999999
    assert ctx.capitals == 1
    assert ctx.clamp == 0

    enabled = {
        signal
        for signal, is_enabled in ctx.traps.items()
        if is_enabled
    }

    assert enabled == {
        DivisionByZero,
        InvalidOperation,
        Overflow,
    }


@pytest.mark.parametrize(
    "value",
    [
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
    ],
)
def test_nonfinite_materialized_calculation_value_fails_domain_validation(value):
    node = calc(value=value)

    with pytest.raises(ValueError):
        validate_calculation(node)

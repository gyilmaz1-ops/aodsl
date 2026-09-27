from dataclasses import replace
from datetime import UTC, datetime
from decimal import (
    Decimal,
    InvalidOperation,
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    localcontext,
)

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Security, Valuation
from investment_domain.valuations import (
    ValuationEvaluationError,
    evaluate_dcf_v1,
)


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


def estimate(
    metric_name,
    value,
    *,
    period_end=None,
    unit="ratio",
    currency=None,
):
    payload = {
        "subject_id": (
            "security:nvidia"
            if metric_name == "market.diluted_shares_outstanding"
            else "company:nvidia"
        ),
        "metric_name": metric_name,
        "period_end": period_end or utc(2027, 12, 31),
        "value": Decimal(value),
        "unit": unit,
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": utc(2026, 9, 27),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def security():
    return Security(
        id="security:nvidia",
        company_id="company:nvidia",
        venue="NASDAQ",
        ticker="NVDA",
        currency="USD",
    )


def inputs():
    return (
        estimate(
            "financial.free_cash_flow",
            "100",
            period_end=utc(2027, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate(
            "financial.free_cash_flow",
            "120",
            period_end=utc(2028, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate("valuation.wacc", "0.10"),
        estimate("valuation.terminal_growth_rate", "0.03"),
        estimate(
            "financial.net_debt",
            "50",
            unit="currency",
            currency="USD",
        ),
        estimate(
            "market.diluted_shares_outstanding",
            "10",
            unit="shares",
        ),
    )


def valuation(value="0"):
    payload = {
        "security_id": "security:nvidia",
        "method": "DCF",
        "value": Decimal(value),
        "currency": "USD",
        "as_of": utc(2026, 9, 27),
        "model_version": "dcf-v1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def expected_value():
    with localcontext() as ctx:
        ctx.prec = 34
        ctx.rounding = ROUND_HALF_EVEN

        one = Decimal("1")
        wacc = Decimal("0.10")
        growth = Decimal("0.03")

        pv1 = Decimal("100") / (one + wacc)
        pv2 = Decimal("120") / ((one + wacc) ** 2)

        terminal = (
            Decimal("120")
            * (one + growth)
            / (wacc - growth)
        )
        terminal_pv = terminal / ((one + wacc) ** 2)

        enterprise = pv1 + pv2 + terminal_pv
        equity = enterprise - Decimal("50")

        return +(equity / Decimal("10"))


def test_dcf_v1_exact_formula():
    result = evaluate_dcf_v1(
        inputs(),
        valuation=valuation(),
        security=security(),
    )

    assert result == expected_value()


def test_dcf_v1_input_order_does_not_change_result():
    original = inputs()
    reordered = tuple(reversed(original))

    left = evaluate_dcf_v1(
        original,
        valuation=valuation(),
        security=security(),
    )
    right = evaluate_dcf_v1(
        reordered,
        valuation=valuation(),
        security=security(),
    )

    assert left == right


def test_dcf_v1_materialized_value_exact_match_passes():
    expected = expected_value()

    result = evaluate_dcf_v1(
        inputs(),
        valuation=valuation(str(expected)),
        security=security(),
        verify_materialized=True,
    )

    assert result == expected


def test_dcf_v1_materialized_value_mismatch_fails_closed():
    with pytest.raises(
        ValuationEvaluationError,
        match="IDM-V020: DCF_MATERIALIZED_VALUE_MISMATCH",
    ):
        evaluate_dcf_v1(
            inputs(),
            valuation=valuation("999"),
            security=security(),
            verify_materialized=True,
        )


def test_dcf_v1_is_deterministic_across_repeated_execution():
    values = tuple(
        evaluate_dcf_v1(
            inputs(),
            valuation=valuation(),
            security=security(),
        )
        for _ in range(20)
    )

    assert len(set(values)) == 1


def test_dcf_v1_net_debt_reduces_equity_value():
    base = evaluate_dcf_v1(
        inputs(),
        valuation=valuation(),
        security=security(),
    )

    changed = list(inputs())
    changed[4] = replace(
        changed[4],
        value=Decimal("150"),
    )

    higher_debt = evaluate_dcf_v1(
        tuple(changed),
        valuation=valuation(),
        security=security(),
    )

    assert higher_debt < base


def test_dcf_v1_more_shares_reduce_per_share_value():
    base = evaluate_dcf_v1(
        inputs(),
        valuation=valuation(),
        security=security(),
    )

    changed = list(inputs())
    changed[-1] = replace(
        changed[-1],
        value=Decimal("20"),
    )

    diluted = evaluate_dcf_v1(
        tuple(changed),
        valuation=valuation(),
        security=security(),
    )

    assert diluted < base



def test_dcf_v1_rejects_non_dcf_method():
    with pytest.raises(
        ValuationEvaluationError,
        match="IDM-V017: DCF_METHOD_UNSUPPORTED",
    ):
        evaluate_dcf_v1(
            inputs(),
            valuation=replace(
                valuation(),
                method="PE",
            ),
            security=security(),
        )


def test_dcf_v1_rejects_unknown_model_version():
    with pytest.raises(
        ValuationEvaluationError,
        match="IDM-V018: DCF_MODEL_VERSION_UNSUPPORTED",
    ):
        evaluate_dcf_v1(
            inputs(),
            valuation=replace(
                valuation(),
                model_version="dcf-v2",
            ),
            security=security(),
        )


def test_dcf_v1_isolated_from_outer_decimal_context():
    baseline = evaluate_dcf_v1(
        inputs(),
        valuation=valuation(),
        security=security(),
    )

    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        ctx.traps[InvalidOperation] = False

        hostile = evaluate_dcf_v1(
            inputs(),
            valuation=valuation(),
            security=security(),
        )

    assert hostile == baseline

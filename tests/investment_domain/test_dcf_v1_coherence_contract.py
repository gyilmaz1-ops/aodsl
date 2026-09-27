from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Security, Valuation
from investment_domain.valuations import (
    ValuationInputResolutionError,
    resolve_dcf_v1_inputs,
)


def utc(year, month, day):
    return datetime(year, month, day, tzinfo=UTC)


def estimate(
    metric_name,
    value,
    *,
    subject_id=None,
    period_end=None,
    unit="ratio",
    currency=None,
    scenario="BASE",
    model_version="forecast-v1",
):
    if subject_id is None:
        subject_id = (
            "security:nvidia"
            if metric_name == "market.diluted_shares_outstanding"
            else "company:nvidia"
        )

    payload = {
        "subject_id": subject_id,
        "metric_name": metric_name,
        "period_end": period_end or utc(2027, 12, 31),
        "value": Decimal(value),
        "unit": unit,
        "scenario": scenario,
        "model_version": model_version,
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


def valuation():
    payload = {
        "security_id": "security:nvidia",
        "method": "DCF",
        "value": Decimal("0"),
        "currency": "USD",
        "as_of": utc(2026, 9, 27),
        "model_version": "dcf-v1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
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


def resolve(items=None, *, v=None, s=None):
    return resolve_dcf_v1_inputs(
        items or inputs(),
        valuation=v or valuation(),
        security=s or security(),
    )


def test_coherent_dcf_context_resolves():
    resolved = resolve()
    assert len(resolved.free_cash_flows) == 2


def test_security_must_match_valuation_security():
    s = replace(security(), id="security:other")

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V006: DCF_SECURITY_MISMATCH",
    ):
        resolve(s=s)


def test_company_scoped_role_must_match_issuer():
    items = list(inputs())
    items[2] = estimate(
        "valuation.wacc",
        "0.10",
        subject_id="company:other",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V007: DCF_COMPANY_SUBJECT_MISMATCH",
    ):
        resolve(tuple(items))


def test_shares_must_match_valuation_security():
    items = list(inputs())
    items[-1] = estimate(
        "market.diluted_shares_outstanding",
        "10",
        subject_id="security:other",
        unit="shares",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V008: DCF_SECURITY_SUBJECT_MISMATCH",
    ):
        resolve(tuple(items))


def test_estimate_scenario_must_match_valuation():
    items = list(inputs())
    items[0] = replace(
        items[0],
        scenario="BULL",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V009: DCF_SCENARIO_MISMATCH",
    ):
        resolve(tuple(items))


def test_fcf_series_must_use_one_input_model_version():
    items = list(inputs())
    items[1] = replace(
        items[1],
        model_version="forecast-v2",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V010: DCF_FCF_MODEL_VERSION_MISMATCH",
    ):
        resolve(tuple(items))


def test_fcf_currency_must_match_valuation_currency():
    items = list(inputs())
    items[0] = replace(
        items[0],
        currency="EUR",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V011: DCF_CURRENCY_MISMATCH",
    ):
        resolve(tuple(items))


def test_net_debt_currency_must_match_valuation_currency():
    items = list(inputs())
    items[4] = replace(
        items[4],
        currency="EUR",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V011: DCF_CURRENCY_MISMATCH",
    ):
        resolve(tuple(items))


def test_partial_coherence_context_fails_closed():
    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V005: DCF_COHERENCE_CONTEXT_INCOMPLETE",
    ):
        resolve_dcf_v1_inputs(
            inputs(),
            valuation=valuation(),
        )


def test_diluted_shares_must_be_positive():
    items = list(inputs())
    items[-1] = replace(
        items[-1],
        value=Decimal("0"),
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V012: DCF_SHARES_NOT_POSITIVE",
    ):
        resolve(tuple(items))


def test_wacc_must_exceed_terminal_growth():
    items = list(inputs())
    items[2] = replace(
        items[2],
        value=Decimal("0.03"),
    )
    items[3] = replace(
        items[3],
        value=Decimal("0.03"),
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V013: DCF_WACC_NOT_ABOVE_TERMINAL_GROWTH",
    ):
        resolve(tuple(items))


def test_wacc_must_be_greater_than_negative_one():
    items = list(inputs())
    items[2] = replace(
        items[2],
        value=Decimal("-1"),
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V014: DCF_WACC_OUT_OF_DOMAIN",
    ):
        resolve(tuple(items))


def test_fcf_periods_must_be_unique():
    items = list(inputs())
    items[1] = replace(
        items[1],
        period_end=items[0].period_end,
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V015: DCF_FCF_PERIOD_DUPLICATE",
    ):
        resolve(tuple(items))


def test_fcf_periods_must_be_after_valuation_as_of():
    items = list(inputs())
    items[0] = replace(
        items[0],
        period_end=valuation().as_of,
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V016: DCF_FCF_PERIOD_NOT_FUTURE",
    ):
        resolve(tuple(items))

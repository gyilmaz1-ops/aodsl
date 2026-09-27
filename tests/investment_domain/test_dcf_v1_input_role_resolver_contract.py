from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Metric
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
        "model_version": "dcf-input-v1",
        "as_of": utc(2026, 9, 27),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def dcf_inputs():
    return (
        estimate(
            "financial.free_cash_flow",
            "120",
            period_end=utc(2028, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate(
            "financial.free_cash_flow",
            "100",
            period_end=utc(2027, 12, 31),
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


def test_resolver_maps_required_dcf_roles():
    resolved = resolve_dcf_v1_inputs(dcf_inputs())

    assert resolved.wacc.metric_name == "valuation.wacc"
    assert (
        resolved.terminal_growth_rate.metric_name
        == "valuation.terminal_growth_rate"
    )
    assert resolved.net_debt.metric_name == "financial.net_debt"
    assert (
        resolved.diluted_shares_outstanding.metric_name
        == "market.diluted_shares_outstanding"
    )


def test_free_cash_flows_are_ordered_by_period_end():
    resolved = resolve_dcf_v1_inputs(dcf_inputs())

    assert tuple(
        item.period_end for item in resolved.free_cash_flows
    ) == (
        utc(2027, 12, 31),
        utc(2028, 12, 31),
    )


def test_missing_free_cash_flow_fails_closed():
    inputs = tuple(
        node
        for node in dcf_inputs()
        if node.metric_name != "financial.free_cash_flow"
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V003: DCF_FREE_CASH_FLOW_MISSING",
    ):
        resolve_dcf_v1_inputs(inputs)


def test_missing_scalar_role_fails_closed():
    inputs = tuple(
        node
        for node in dcf_inputs()
        if node.metric_name != "valuation.wacc"
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V004: DCF_REQUIRED_ROLE_MISSING:wacc",
    ):
        resolve_dcf_v1_inputs(inputs)


def test_duplicate_scalar_role_fails_closed():
    duplicate = estimate(
        "valuation.wacc",
        "0.11",
        period_end=utc(2028, 12, 31),
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V002: DCF_AMBIGUOUS_ROLE:wacc",
    ):
        resolve_dcf_v1_inputs(dcf_inputs() + (duplicate,))


def test_metric_free_cash_flow_is_rejected():
    metric = Metric(
        id="metric:" + "a" * 64,
        subject_id="company:nvidia",
        name="financial.free_cash_flow",
        value=Decimal("90"),
        unit="currency",
        period_start=utc(2026, 1, 1),
        period_end=utc(2026, 12, 31),
        effective_at=utc(2026, 12, 31),
        observed_at=utc(2027, 1, 1),
        published_at=utc(2027, 1, 2),
        ingested_at=utc(2027, 1, 3),
        source_id="source:test",
        source_version="v1",
        currency="USD",
    )

    with pytest.raises(
        ValuationInputResolutionError,
        match="IDM-V001: DCF_FREE_CASH_FLOW_MUST_BE_ESTIMATE",
    ):
        resolve_dcf_v1_inputs(dcf_inputs() + (metric,))

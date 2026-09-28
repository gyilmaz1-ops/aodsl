from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Security
from investment_domain.valuation_execution import (
    ValuationExecutionRequest,
    ValuationExecutionRequestError,
    materialize_valuation,
    validate_valuation_execution_request,
)
from investment_domain.validation import validate_node


def utc(
    year: int,
    month: int,
    day: int,
) -> datetime:
    return datetime(
        year,
        month,
        day,
        tzinfo=UTC,
    )


def security() -> Security:
    return Security(
        id="security:nasdaq:nvda",
        company_id="company:nvidia",
        venue="NASDAQ",
        ticker="NVDA",
        currency="USD",
    )


def estimate(
    metric_name: str,
    value: str,
    *,
    subject_id: str = "company:nvidia",
    period_end: datetime | None = None,
    unit: str = "ratio",
    currency: str | None = None,
) -> Estimate:
    payload = {
        "subject_id": subject_id,
        "metric_name": metric_name,
        "period_end": period_end or utc(2026, 9, 27),
        "value": Decimal(value),
        "unit": unit,
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": utc(2026, 9, 26),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id("estimate", payload),
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
            "110",
            period_end=utc(2028, 12, 31),
            unit="currency",
            currency="USD",
        ),
        estimate(
            "valuation.wacc",
            "0.10",
        ),
        estimate(
            "valuation.terminal_growth_rate",
            "0",
        ),
        estimate(
            "financial.net_debt",
            "10",
            unit="currency",
            currency="USD",
        ),
        estimate(
            "market.diluted_shares_outstanding",
            "100",
            subject_id="security:nasdaq:nvda",
            unit="shares",
        ),
    )


def request(**overrides):
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "currency": "USD",
        "as_of": utc(2026, 9, 27),
        "model_version": "dcf-v1",
        "scenario": "BASE",
        "research_cutoff": utc(2026, 9, 28),
        "dependency_ids": tuple(
            node.id for node in inputs()
        ),
    }
    payload.update(overrides)
    return ValuationExecutionRequest(**payload)


def test_request_contains_no_materialized_value():
    assert "value" not in (
        ValuationExecutionRequest.__dataclass_fields__
    )


def test_valid_request_is_accepted():
    validate_valuation_execution_request(
        request()
    )


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        (
            "method",
            "EV_EBITDA",
            "IDM-X002",
        ),
        (
            "model_version",
            "dcf-v2",
            "IDM-X003",
        ),
        (
            "scenario",
            "UPSIDE",
            "IDM-X004",
        ),
        (
            "currency",
            "usd",
            "IDM-X005",
        ),
    ],
)
def test_request_vocabulary_is_fail_closed(
    field,
    value,
    code,
):
    with pytest.raises(
        ValuationExecutionRequestError,
        match=code,
    ):
        validate_valuation_execution_request(
            request(**{field: value})
        )


def test_request_rejects_naive_cutoff():
    with pytest.raises(
        ValuationExecutionRequestError,
        match="IDM-X006",
    ):
        validate_valuation_execution_request(
            request(
                research_cutoff=datetime(
                    2026,
                    9,
                    28,
                )
            )
        )


def test_request_rejects_as_of_after_cutoff():
    with pytest.raises(
        ValuationExecutionRequestError,
        match="IDM-X007",
    ):
        validate_valuation_execution_request(
            request(
                as_of=utc(2026, 9, 29),
            )
        )


def test_request_rejects_duplicate_dependencies():
    dependency_id = inputs()[0].id

    with pytest.raises(
        ValuationExecutionRequestError,
        match="IDM-X010",
    ):
        validate_valuation_execution_request(
            request(
                dependency_ids=(
                    dependency_id,
                    dependency_id,
                )
            )
        )


def test_materializer_rejects_security_mismatch():
    other = Security(
        id="security:nasdaq:amd",
        company_id="company:amd",
        venue="NASDAQ",
        ticker="AMD",
        currency="USD",
    )

    with pytest.raises(
        ValuationExecutionRequestError,
        match="IDM-X011",
    ):
        materialize_valuation(
            request(),
            security=other,
            inputs=inputs(),
        )


def test_materializer_computes_authoritative_value():
    valuation = materialize_valuation(
        request(),
        security=security(),
        inputs=inputs(),
    )

    assert valuation.value == Decimal(
        "10.80909090909090909090909090909091"
    )
    assert valuation.method == "DCF"
    assert valuation.model_version == "dcf-v1"
    assert valuation.scenario == "BASE"

    validate_node(valuation)


def test_materialized_identity_includes_computed_value():
    valuation = materialize_valuation(
        request(),
        security=security(),
        inputs=inputs(),
    )

    payload = {
        "security_id": valuation.security_id,
        "method": valuation.method,
        "value": valuation.value,
        "currency": valuation.currency,
        "as_of": valuation.as_of,
        "model_version": valuation.model_version,
        "scenario": valuation.scenario,
    }

    assert valuation.id == canonical_id(
        "valuation",
        payload,
    )


def test_request_has_no_way_to_supply_fair_value():
    with pytest.raises(TypeError):
        ValuationExecutionRequest(
            security_id="security:nasdaq:nvda",
            method="DCF",
            currency="USD",
            as_of=utc(2026, 9, 27),
            model_version="dcf-v1",
            scenario="BASE",
            research_cutoff=utc(2026, 9, 28),
            dependency_ids=("estimate:x",),
            value=Decimal("999"),
        )

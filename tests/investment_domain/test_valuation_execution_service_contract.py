from datetime import UTC, datetime
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Security
from investment_domain.valuation_execution import (
    ValuationExecutionRequest,
    ValuationExecutionService,
    ValuationExecutionServiceError,
)


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
        "period_end": (
            period_end
            or utc(2026, 9, 27)
        ),
        "value": Decimal(value),
        "unit": unit,
        "scenario": "BASE",
        "model_version": "forecast-v1",
        "as_of": utc(2026, 9, 26),
        "currency": currency,
    }
    return Estimate(
        id=canonical_id(
            "estimate",
            payload,
        ),
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


def request(
    resolved_inputs=None,
):
    resolved_inputs = (
        resolved_inputs
        if resolved_inputs is not None
        else inputs()
    )

    return ValuationExecutionRequest(
        security_id="security:nasdaq:nvda",
        method="DCF",
        currency="USD",
        as_of=utc(2026, 9, 27),
        model_version="dcf-v1",
        scenario="BASE",
        research_cutoff=utc(2026, 9, 28),
        dependency_ids=tuple(
            node.id
            for node in resolved_inputs
        ),
    )


class FakeRepository:
    def __init__(
        self,
        *,
        resolved_inputs=None,
        stored_security=None,
    ):
        self.resolved_inputs = (
            tuple(resolved_inputs)
            if resolved_inputs is not None
            else inputs()
        )
        self.stored_security = (
            stored_security
            if stored_security is not None
            else security()
        )

        self.persisted_valuation = None
        self.persisted_dependencies = None
        self.cutoff = None
        self.requested_dependency_ids = None

    def security(
        self,
        security_id,
    ):
        if (
            self.stored_security is not None
            and self.stored_security.id
            == security_id
        ):
            return self.stored_security
        return None

    def valuation_inputs_by_ids_at(
        self,
        dependency_ids,
        research_cutoff,
    ):
        self.requested_dependency_ids = (
            dependency_ids
        )
        self.cutoff = research_cutoff
        return self.resolved_inputs

    def persist_verified_valuation(
        self,
        valuation,
        dependency_ids,
    ):
        self.persisted_valuation = valuation
        self.persisted_dependencies = dependency_ids
        return valuation



def test_service_executes_complete_boundary():
    resolved = inputs()
    req = request(resolved)
    repo = FakeRepository(
        resolved_inputs=resolved
    )

    result = ValuationExecutionService(
        repo
    ).execute(req)

    assert result.valuation.value == Decimal(
        "10.80909090909090909090909090909091"
    )
    assert (
        result.valuation
        == repo.persisted_valuation
    )
    assert (
        repo.persisted_dependencies
        == req.dependency_ids
    )
    assert repo.cutoff == utc(
        2026,
        9,
        28,
    )



def test_service_rejects_missing_security():
    repo = FakeRepository()
    repo.stored_security = None

    with pytest.raises(
        ValuationExecutionServiceError,
        match="IDM-X012",
    ):
        ValuationExecutionService(
            repo
        ).execute(
            request()
        )

    assert repo.persisted_valuation is None
    assert repo.persisted_dependencies is None



def test_service_rejects_dependency_set_mismatch():
    resolved = inputs()
    requested = request(resolved)
    repo = FakeRepository(
        resolved_inputs=resolved[:-1]
    )

    with pytest.raises(
        ValuationExecutionServiceError,
        match="IDM-X013",
    ):
        ValuationExecutionService(
            repo
        ).execute(
            requested
        )

    assert repo.persisted_valuation is None
    assert repo.persisted_dependencies is None



def test_service_accepts_repository_reordering():
    resolved = inputs()
    repo = FakeRepository(
        resolved_inputs=tuple(
            reversed(resolved)
        )
    )

    result = ValuationExecutionService(
        repo
    ).execute(
        request(resolved)
    )

    assert result.valuation.value == Decimal(
        "10.80909090909090909090909090909091"
    )


def test_service_passes_exact_requested_ids_to_repository():
    resolved = inputs()
    req = request(resolved)
    repo = FakeRepository(
        resolved_inputs=resolved
    )

    ValuationExecutionService(
        repo
    ).execute(req)

    assert (
        repo.requested_dependency_ids
        == req.dependency_ids
    )
    assert (
        repo.persisted_dependencies
        == req.dependency_ids
    )



def test_service_rejects_verified_object_mismatch():
    resolved = inputs()
    repo = FakeRepository(
        resolved_inputs=resolved
    )

    original_persist = repo.persist_verified_valuation

    def wrong_persist(
        valuation,
        dependency_ids,
    ):
        persisted = original_persist(
            valuation,
            dependency_ids,
        )
        return type(persisted)(
            id=persisted.id,
            security_id=persisted.security_id,
            method=persisted.method,
            value=persisted.value + Decimal("1"),
            currency=persisted.currency,
            as_of=persisted.as_of,
            model_version=persisted.model_version,
            scenario=persisted.scenario,
        )

    repo.persist_verified_valuation = wrong_persist

    with pytest.raises(
        ValuationExecutionServiceError,
        match="IDM-X014",
    ):
        ValuationExecutionService(
            repo
        ).execute(
            request(resolved)
        )

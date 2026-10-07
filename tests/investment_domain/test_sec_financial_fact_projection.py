from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.nodes import Evidence
from investment_domain.sec_financial_fact_projection import (
    SecFinancialFactProjectionError,
    project_sec_financial_fact,
)
from investment_domain.sec_financial_facts import ExtractedFinancialFact
from investment_domain.validation import validate_node


UTC = timezone.utc
T0 = datetime(2026, 2, 1, 8, tzinfo=UTC)
T1 = datetime(2026, 2, 1, 9, tzinfo=UTC)
T2 = datetime(2026, 2, 1, 10, tzinfo=UTC)


def evidence() -> Evidence:
    payload = {
        "source_id": "sec:edgar:0000123456",
        "source_version": "0000123456-26-000001",
        "content_hash": "a" * 64,
        "effective_at": datetime(2026, 1, 31, tzinfo=UTC),
        "observed_at": T0,
        "published_at": T1,
    }
    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=T2,
        source_uri="https://www.sec.gov/example",
    )


def revenue_fact() -> ExtractedFinancialFact:
    return ExtractedFinancialFact(
        concept="us-gaap:Revenues",
        value=Decimal("1000000"),
        unit_ref="USD",
        period_start=datetime(2026, 1, 1, tzinfo=UTC),
        period_end=datetime(2026, 1, 31, tzinfo=UTC),
        context_id="FY2026",
        dimensions=(),
        decimals="-3",
    )


def test_projects_revenue_to_canonical_metric_and_supported_by_link():
    ev = evidence()

    result = project_sec_financial_fact(
        fact=revenue_fact(),
        evidence=ev,
        subject_id="company:example",
        created_at=T2,
    )

    metric = result.metric
    link = result.link

    validate_node(metric)

    assert metric.name == "financial.revenue"
    assert metric.subject_id == "company:example"
    assert metric.value == Decimal("1000000")
    assert metric.unit == "currency"
    assert metric.currency == "USD"
    assert metric.period_start == datetime(2026, 1, 1, tzinfo=UTC)
    assert metric.period_end == datetime(2026, 1, 31, tzinfo=UTC)
    assert metric.effective_at == metric.period_end
    assert metric.observed_at == ev.observed_at
    assert metric.published_at == ev.published_at
    assert metric.ingested_at == ev.ingested_at
    assert metric.source_id == ev.source_id
    assert metric.source_version == ev.source_version

    assert link.metric_id == metric.id
    assert link.evidence_id == ev.id
    assert link.relation is EdgeType.SUPPORTED_BY
    assert link.created_at == T2


def test_projection_is_deterministic():
    ev = evidence()

    first = project_sec_financial_fact(
        fact=revenue_fact(),
        evidence=ev,
        subject_id="company:example",
        created_at=T2,
    )
    second = project_sec_financial_fact(
        fact=revenue_fact(),
        evidence=ev,
        subject_id="company:example",
        created_at=T2,
    )

    assert first == second
    assert first.metric.id == second.metric.id


def test_unsupported_concept_fails_closed():
    fact = replace(
        revenue_fact(),
        concept="us-gaap:OperatingIncomeLoss",
    )

    with pytest.raises(
        SecFinancialFactProjectionError,
        match="unsupported SEC financial concept",
    ):
        project_sec_financial_fact(
            fact=fact,
            evidence=evidence(),
            subject_id="company:example",
            created_at=T2,
        )


def test_dimensioned_fact_fails_closed():
    fact = replace(
        revenue_fact(),
        dimensions=(
            ("us-gaap:ProductAxis", "example:Widgets"),
        ),
    )

    with pytest.raises(
        SecFinancialFactProjectionError,
        match="dimensioned SEC financial facts are not supported",
    ):
        project_sec_financial_fact(
            fact=fact,
            evidence=evidence(),
            subject_id="company:example",
            created_at=T2,
        )


def test_non_usd_fact_fails_closed():
    fact = replace(revenue_fact(), unit_ref="EUR")

    with pytest.raises(
        SecFinancialFactProjectionError,
        match="unsupported SEC financial fact unit",
    ):
        project_sec_financial_fact(
            fact=fact,
            evidence=evidence(),
            subject_id="company:example",
            created_at=T2,
        )


@pytest.mark.parametrize(
    "subject_id",
    ["security:xnas:example", "company:", "", "example"],
)
def test_non_company_subject_fails_closed(subject_id):
    with pytest.raises(
        SecFinancialFactProjectionError,
        match="subject must reference Company",
    ):
        project_sec_financial_fact(
            fact=revenue_fact(),
            evidence=evidence(),
            subject_id=subject_id,
            created_at=T2,
        )


def test_naive_created_at_fails_closed():
    with pytest.raises(
        SecFinancialFactProjectionError,
        match="created_at must be timezone-aware",
    ):
        project_sec_financial_fact(
            fact=revenue_fact(),
            evidence=evidence(),
            subject_id="company:example",
            created_at=datetime(2026, 2, 1, 10),
        )

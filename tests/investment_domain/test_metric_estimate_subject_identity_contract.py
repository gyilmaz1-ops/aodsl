from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Estimate, Metric
from investment_domain.validation import DomainValidationError, validate_node


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def company_metric(**changes):
    node = Metric(
        id="metric:" + "a" * 64,
        subject_id="company:acme",
        name="financial.revenue",
        value=Decimal("100"),
        unit="currency",
        period_start=NOW,
        period_end=NOW,
        effective_at=NOW,
        observed_at=NOW,
        published_at=NOW,
        ingested_at=NOW,
        source_id="source:test",
        source_version="1",
        currency="USD",
    )
    node = replace(node, **changes)
    identity = {
        "subject_id": node.subject_id,
        "name": node.name,
        "period_start": node.period_start,
        "period_end": node.period_end,
        "effective_at": node.effective_at,
        "observed_at": node.observed_at,
         "published_at": node.published_at,
        "source_id": node.source_id,
        "source_version": node.source_version,
    }
    return replace(node, id=canonical_id("metric", identity))


def security_metric(**changes):
    node = Metric(
        id="metric:" + "b" * 64,
        subject_id="security:nasdaq:nvda",
        name="market.price",
        value=Decimal("100"),
        unit="currency",
        period_start=None,
        period_end=NOW,
        effective_at=NOW,
        observed_at=NOW,
        published_at=NOW,
        ingested_at=NOW,
        source_id="source:test",
        source_version="1",
        currency="USD",
    )
    node = replace(node, **changes)
    identity = {
        "subject_id": node.subject_id,
        "name": node.name,
        "period_start": node.period_start,
        "period_end": node.period_end,
        "effective_at": node.effective_at,
        "observed_at": node.observed_at,
         "published_at": node.published_at,
        "source_id": node.source_id,
        "source_version": node.source_version,
    }
    return replace(node, id=canonical_id("metric", identity))


def company_estimate(**changes):
    node = Estimate(
        id="estimate:" + "c" * 64,
        subject_id="company:acme",
        metric_name="financial.revenue",
        period_end=NOW,
        value=Decimal("100"),
        unit="currency",
        scenario="base",
        model_version="test",
        as_of=NOW,
        currency="USD",
    )
    node = replace(node, **changes)
    identity = {
        "subject_id": node.subject_id,
        "metric_name": node.metric_name,
        "period_end": node.period_end,
        "value": node.value,
        "unit": node.unit,
        "scenario": node.scenario,
        "model_version": node.model_version,
        "as_of": node.as_of,
        "currency": node.currency,
    }
    return replace(node, id=canonical_id("estimate", identity))


def security_estimate(**changes):
    node = Estimate(
        id="estimate:" + "d" * 64,
        subject_id="security:nasdaq:nvda",
        metric_name="market.price",
        period_end=NOW,
        value=Decimal("100"),
        unit="currency",
        scenario="base",
        model_version="test",
        as_of=NOW,
        currency="USD",
    )
    node = replace(node, **changes)
    identity = {
        "subject_id": node.subject_id,
        "metric_name": node.metric_name,
        "period_end": node.period_end,
        "value": node.value,
        "unit": node.unit,
        "scenario": node.scenario,
        "model_version": node.model_version,
        "as_of": node.as_of,
        "currency": node.currency,
    }
    return replace(node, id=canonical_id("estimate", identity))


@pytest.mark.parametrize(
    ("factory", "subject_id"),
    [
        (company_metric, "company:ACME"),
        (company_metric, "company:acme corp"),
        (company_metric, "company:-acme"),
        (company_metric, "company:acme-"),
        (security_metric, "security:nvda"),
        (security_metric, "security:NASDAQ:NVDA"),
        (security_metric, "security:nasdaq:nv da"),
        (security_metric, "security:-nasdaq:nvda"),
        (security_metric, "security:nasdaq:nvda-"),
        (company_estimate, "company:ACME"),
        (company_estimate, "company:acme corp"),
        (company_estimate, "company:-acme"),
        (company_estimate, "company:acme-"),
        (security_estimate, "security:nvda"),
        (security_estimate, "security:NASDAQ:NVDA"),
        (security_estimate, "security:nasdaq:nv da"),
        (security_estimate, "security:-nasdaq:nvda"),
        (security_estimate, "security:nasdaq:nvda-"),
    ],
)
def test_subject_id_rejects_noncanonical_identity(factory, subject_id):
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(factory(subject_id=subject_id))


@pytest.mark.parametrize(
    "node",
    [
        company_metric(),
        security_metric(),
        company_estimate(),
        security_estimate(),
    ],
)
def test_subject_id_accepts_canonical_identity(node):
    validate_node(node)

from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import Metric, canonical_id, validate_node
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def metric(
    *,
    subject_id="company:example",
    name="financial.revenue",
    unit="currency",
    currency="USD",
    period_start=datetime(2026, 1, 1, tzinfo=UTC),
    period_end=datetime(2026, 12, 31, tzinfo=UTC),
):
    payload = {
        "subject_id": subject_id,
        "name": name,
        "period_start": period_start,
        "period_end": period_end,
        "effective_at": period_end,
        "observed_at": period_end,
        "published_at": datetime(2027, 2, 1, tzinfo=UTC),
        "source_id": "filing:test",
        "source_version": "1",
    }

    return Metric(
        id=canonical_id("metric", payload),
        subject_id=subject_id,
        name=name,
        value=Decimal("100"),
        unit=unit,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        effective_at=period_end,
        observed_at=period_end,
        published_at=datetime(2027, 2, 1, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 1, 0, 5, tzinfo=UTC),
        source_id="filing:test",
        source_version="1",
    )


def test_unknown_metric_name_rejected():
    node = metric(name="whatever.some_metric")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_financial_revenue_accepts_company_subject():
    validate_node(metric())


def test_financial_revenue_rejects_security_subject():
    node = metric(subject_id="security:nvda")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_market_price_accepts_security_subject():
    validate_node(
        metric(
            subject_id="security:nvda",
            name="market.price",
            period_start=None,
        )
    )


def test_market_price_rejects_company_subject():
    node = metric(
        subject_id="company:nvidia",
        name="market.price",
        period_start=None,
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_currency_metric_requires_currency_unit():
    node = metric(unit="ratio", currency=None)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_currency_unit_requires_currency_code():
    node = metric(currency=None)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_non_currency_metric_forbids_currency():
    node = metric(
        name="financial.gross_margin",
        unit="percent",
        currency="USD",
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_currency_code_must_be_uppercase_iso_like():
    node = metric(currency="usd")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_financial_revenue_requires_duration_period():
    node = metric(period_start=None)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_market_price_requires_instant_period():
    node = metric(
        subject_id="security:nvda",
        name="market.price",
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_legacy_revenue_alias_remains_valid():
    validate_node(metric(name="revenue"))


def test_legacy_revenue_alias_does_not_change_identity():
    node = metric(name="revenue")

    expected = canonical_id(
        "metric",
        {
            "subject_id": node.subject_id,
            "name": "revenue",
            "period_start": node.period_start,
            "period_end": node.period_end,
            "effective_at": node.effective_at,
            "observed_at": node.observed_at,
            "published_at": node.published_at,
            "source_id": node.source_id,
            "source_version": node.source_version,
        },
    )

    assert node.id == expected


def test_company_subject_requires_nonempty_local_identifier():
    node = metric(subject_id="company:")

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_security_subject_requires_nonempty_local_identifier():
    node = metric(
        subject_id="security:",
        name="market.price",
        period_start=None,
    )

    with pytest.raises(DomainValidationError):
        validate_node(node)


@pytest.mark.parametrize(
    "currency",
    [
        "ÜSD",
        "US1",
        "US$",
        " USD",
        "USD ",
        "US",
        "USDD",
    ],
)
def test_currency_code_requires_exactly_three_ascii_uppercase_letters(currency):
    node = metric(currency=currency)

    with pytest.raises(DomainValidationError):
        validate_node(node)


def test_metric_registry_keys_match_definition_names():
    from investment_domain.metrics import METRIC_DEFINITIONS

    assert METRIC_DEFINITIONS
    for name, definition in METRIC_DEFINITIONS.items():
        assert name == definition.name


def test_metric_aliases_do_not_shadow_canonical_names():
    from investment_domain.metrics import METRIC_ALIASES, METRIC_DEFINITIONS

    assert set(METRIC_ALIASES).isdisjoint(METRIC_DEFINITIONS)


def test_metric_alias_targets_exist():
    from investment_domain.metrics import METRIC_ALIASES, METRIC_DEFINITIONS

    for alias, target in METRIC_ALIASES.items():
        assert alias != target
        assert target in METRIC_DEFINITIONS


def test_metric_registry_subject_types_are_supported():
    from investment_domain.metrics import METRIC_DEFINITIONS
    from investment_domain.types import NodeType

    supported = {NodeType.COMPANY, NodeType.SECURITY}

    for definition in METRIC_DEFINITIONS.values():
        assert definition.subject_type in supported


def test_unknown_metric_name_precedes_temporal_validation():
    node = metric(name="unknown.metric")
    node = replace(
        node,
        observed_at=datetime(2025, 12, 31, tzinfo=UTC),
    )

    with pytest.raises(
        DomainValidationError,
        match="unknown Metric.name",
    ):
        validate_node(node)


def test_known_metric_preserves_temporal_validation():
    node = metric()
    node = replace(
        node,
        observed_at=datetime(2025, 12, 31, tzinfo=UTC),
    )

    with pytest.raises(
        DomainValidationError,
        match="Metric.observed_at precedes effective_at",
    ):
        validate_node(node)

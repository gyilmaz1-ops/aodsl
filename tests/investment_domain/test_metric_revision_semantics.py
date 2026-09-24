from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_domain.metrics import (
    metric_revision_key,
    validate_metric_revision,
)
from investment_domain.nodes import Metric


UTC = timezone.utc


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=UTC)


def root_metric():
    return Metric(
        id="metric:revenue-q2-v1",
        subject_id="company:abc",
        name="financial.revenue",
        value=Decimal("100"),
        unit="currency",
        currency="USD",
        period_start=utc(2026, 4, 1),
        period_end=utc(2026, 6, 30),
        effective_at=utc(2026, 6, 30),
        observed_at=utc(2026, 7, 10),
        published_at=utc(2026, 7, 11),
        ingested_at=utc(2026, 7, 11, 1),
        source_id="issuer:abc",
        source_version="q2-original",
    )


def revision(**changes):
    root = root_metric()
    defaults = dict(
        id="metric:revenue-q2-v2",
        value=Decimal("105"),
        effective_at=utc(2026, 7, 1),
        observed_at=utc(2026, 8, 10),
        published_at=utc(2026, 8, 11),
        ingested_at=utc(2026, 8, 11, 1),
        source_version="q2-restated",
        supersedes_id=root.id,
    )
    defaults.update(changes)
    return replace(root, **defaults)


def test_revision_key_contains_measurement_identity():
    metric = root_metric()

    assert metric_revision_key(metric) == (
        "company:abc",
        "financial.revenue",
        utc(2026, 4, 1),
        utc(2026, 6, 30),
        "currency",
        "USD",
        "issuer:abc",
    )


def test_valid_restatement_is_accepted():
    validate_metric_revision(root_metric(), revision())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("subject_id", "company:other"),
        ("name", "financial.gross_margin"),
        ("period_start", utc(2026, 1, 1)),
        ("period_end", utc(2026, 9, 30)),
        ("unit", "percent"),
        ("currency", "EUR"),
        ("source_id", "vendor:other"),
    ],
)
def test_revision_key_change_is_rejected(field, value):
    predecessor = root_metric()
    successor = revision(**{field: value})

    with pytest.raises(ValueError, match="revision key"):
        validate_metric_revision(predecessor, successor)


def test_value_may_change():
    validate_metric_revision(
        root_metric(),
        revision(value=Decimal("110")),
    )


def test_effective_at_may_change():
    validate_metric_revision(
        root_metric(),
        revision(effective_at=utc(2026, 7, 15)),
    )


def test_source_version_may_change():
    validate_metric_revision(
        root_metric(),
        revision(source_version="restatement-2"),
    )


def test_successor_must_reference_predecessor():
    predecessor = root_metric()
    successor = revision(supersedes_id="metric:other")

    with pytest.raises(ValueError, match="supersedes"):
        validate_metric_revision(predecessor, successor)


def test_revision_ingestion_must_be_strictly_later():
    predecessor = root_metric()
    successor = revision(
        ingested_at=predecessor.ingested_at,
    )

    with pytest.raises(ValueError, match="ingested"):
        validate_metric_revision(predecessor, successor)


def test_metric_revision_rejects_self_supersession():
    predecessor = root_metric()

    successor = replace(
        predecessor,
        supersedes_id=predecessor.id,
        ingested_at=predecessor.ingested_at + timedelta(seconds=1),
    )

    with pytest.raises(ValueError, match="itself"):
        validate_metric_revision(predecessor, successor)

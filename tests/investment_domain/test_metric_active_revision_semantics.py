from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from investment_domain import Metric
from investment_domain.temporal import active_revision_at


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def metric(
    *,
    metric_id,
    value,
    published_at,
    ingested_at,
    supersedes_id=None,
    source_version="v1",
):
    return Metric(
        id=metric_id,
        subject_id="company:abc",
        name="financial.revenue",
        value=Decimal(value),
        unit="currency",
        currency="USD",
        period_start=utc(2026, 1, 1),
        period_end=utc(2026, 6, 30),
        effective_at=utc(2026, 6, 30),
        observed_at=published_at,
        published_at=published_at,
        ingested_at=ingested_at,
        source_id="issuer:abc",
        source_version=source_version,
        supersedes_id=supersedes_id,
    )


def lineage():
    root = metric(
        metric_id="metric:m1",
        value="100",
        published_at=utc(2026, 7, 5),
        ingested_at=utc(2026, 7, 10),
    )
    successor = metric(
        metric_id="metric:m2",
        value="105",
        published_at=utc(2026, 8, 15),
        ingested_at=utc(2026, 8, 20),
        supersedes_id=root.id,
        source_version="v2",
    )
    return root, successor


def test_root_is_active_after_it_becomes_available():
    root, _ = lineage()

    assert active_revision_at(
        (root,),
        utc(2026, 7, 11),
    ) == root


def test_no_metric_revision_is_active_before_root_is_available():
    root, _ = lineage()

    assert active_revision_at(
        (root,),
        utc(2026, 7, 9),
    ) is None


def test_future_restatement_does_not_change_historical_metric():
    root, successor = lineage()

    assert active_revision_at(
        (root, successor),
        utc(2026, 8, 1),
    ) == root


def test_restatement_becomes_active_after_it_is_available():
    root, successor = lineage()

    assert active_revision_at(
        (root, successor),
        utc(2026, 8, 21),
    ) == successor


def test_effective_at_is_not_metric_visibility_gate():
    root, _ = lineage()

    shifted = replace(
        root,
        effective_at=utc(2027, 1, 1),
    )

    assert active_revision_at(
        (shifted,),
        utc(2026, 7, 11),
    ) == shifted

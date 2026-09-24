from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain import canonical_id
from investment_domain.edges import EdgeType
from investment_domain.metrics import (
    MetricEvidenceLink,
    eligible_metric_evidence,
)
from investment_domain.nodes import Evidence, Metric


UTC = timezone.utc

T0 = datetime(2026, 9, 1, 8, tzinfo=UTC)
T1 = datetime(2026, 9, 1, 9, tzinfo=UTC)
T2 = datetime(2026, 9, 1, 10, tzinfo=UTC)
T3 = datetime(2026, 9, 1, 11, tzinfo=UTC)


def make_metric(
    seed="metric-pit",
    *,
    published_at=T1,
    ingested_at=T2,
    effective_at=None,
):
    effective_at = effective_at or datetime(
        2026, 3, 31, tzinfo=UTC
    )
    payload = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "period_start": datetime(
            2026, 1, 1, tzinfo=UTC
        ),
        "period_end": datetime(
            2026, 3, 31, tzinfo=UTC
        ),
        "effective_at": effective_at,
        "observed_at": T0,
        "published_at": published_at,
        "source_id": f"source:{seed}",
        "source_version": "1",
    }
    return Metric(
        id=canonical_id("metric", payload),
        **payload,
        value=Decimal("100.00"),
        unit="currency",
        currency="USD",
        ingested_at=ingested_at,
    )


def make_evidence(
    seed,
    *,
    published_at=T1,
    ingested_at=T2,
    effective_at=None,
    supersedes_id=None,
):
    effective_at = effective_at or datetime(
        2026, 6, 30, tzinfo=UTC
    )
    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": f"{sum(seed.encode()):064x}"[-64:],
        "effective_at": effective_at,
        "observed_at": T0,
        "published_at": published_at,
    }
    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
        supersedes_id=supersedes_id,
        source_uri=f"https://example.test/{seed}",
    )


def make_link(
    metric,
    evidence,
    *,
    created_at=T3,
):
    return MetricEvidenceLink(
        metric_id=metric.id,
        evidence_id=evidence.id,
        relation=EdgeType.SUPPORTED_BY,
        created_at=created_at,
    )


def test_visible_metric_evidence_is_eligible():
    metric = make_metric()
    evidence = make_evidence("visible")

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == (evidence,)


def test_unpublished_metric_returns_empty():
    metric = make_metric(
        "metric-unpublished",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
    )
    evidence = make_evidence("metric-unpublished-evidence")

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == ()


def test_uningested_metric_returns_empty():
    metric = make_metric(
        "metric-uningested",
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
    )
    evidence = make_evidence("metric-uningested-evidence")

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == ()


def test_metric_effective_at_is_not_visibility_gate():
    metric = make_metric(
        "future-effective",
        effective_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    evidence = make_evidence("future-effective-evidence")

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == (evidence,)


def test_evidence_publication_and_ingestion_gate_visibility():
    metric = make_metric("evidence-visibility")
    evidence = make_evidence(
        "future-evidence",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
    )

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == ()


def test_evidence_effective_at_is_not_visibility_gate():
    metric = make_metric("evidence-effective")
    evidence = make_evidence(
        "future-economic-evidence",
        effective_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [make_link(metric, evidence)],
        T3,
    ) == (evidence,)


def test_future_link_is_not_eligible():
    metric = make_metric("future-link")
    evidence = make_evidence("future-link")

    link = make_link(
        metric,
        evidence,
        created_at=datetime(2026, 9, 2, 11, tzinfo=UTC),
    )

    assert eligible_metric_evidence(
        metric,
        [evidence],
        [link],
        T3,
    ) == ()


def test_predecessor_active_before_successor_available():
    metric = make_metric("revision-before")
    first = make_evidence("revision-v1")
    second = make_evidence(
        "revision-v2",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    assert eligible_metric_evidence(
        metric,
        [first, second],
        [make_link(metric, first)],
        T3,
    ) == (first,)


def test_successor_without_explicit_link_does_not_inherit():
    metric = make_metric("no-inherit")
    first = make_evidence("no-inherit-v1")
    second = make_evidence(
        "no-inherit-v2",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    cutoff = datetime(2026, 9, 2, 12, tzinfo=UTC)

    assert eligible_metric_evidence(
        metric,
        [first, second],
        [make_link(metric, first)],
        cutoff,
    ) == ()


def test_successor_with_explicit_link_becomes_eligible():
    metric = make_metric("relinked")
    first = make_evidence("relinked-v1")
    second = make_evidence(
        "relinked-v2",
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=first.id,
    )

    cutoff = datetime(2026, 9, 2, 12, tzinfo=UTC)

    assert eligible_metric_evidence(
        metric,
        [first, second],
        [
            make_link(metric, first),
            make_link(
                metric,
                second,
                created_at=datetime(
                    2026, 9, 2, 10, 5, tzinfo=UTC
                ),
            ),
        ],
        cutoff,
    ) == (second,)


def test_duplicate_evidence_identity_fails_closed():
    metric = make_metric("duplicate")
    evidence = make_evidence("duplicate")

    with pytest.raises(
        ValueError,
        match="duplicate evidence identity",
    ):
        eligible_metric_evidence(
            metric,
            [evidence, evidence],
            [make_link(metric, evidence)],
            T3,
        )


def test_missing_revision_predecessor_fails_closed():
    metric = make_metric("missing-predecessor")
    evidence = make_evidence(
        "missing-predecessor",
        supersedes_id="evidence:missing",
    )

    with pytest.raises(
        ValueError,
        match="revision supersedes_id is outside supplied evidence set",
    ):
        eligible_metric_evidence(
            metric,
            [evidence],
            [make_link(metric, evidence)],
            T3,
        )


def test_link_for_different_metric_fails_closed():
    metric = make_metric("metric-a")
    other = make_metric("metric-b")
    evidence = make_evidence("mixed-metric")

    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink belongs to a different metric",
    ):
        eligible_metric_evidence(
            metric,
            [evidence],
            [make_link(other, evidence)],
            T3,
        )


def test_duplicate_metric_evidence_link_fails_closed():
    metric = make_metric("duplicate-link")
    evidence = make_evidence("duplicate-link")
    link = make_link(metric, evidence)

    with pytest.raises(
        ValueError,
        match="duplicate metric-evidence link",
    ):
        eligible_metric_evidence(
            metric,
            [evidence],
            [link, link],
            T3,
        )


def test_link_to_missing_evidence_fails_closed():
    metric = make_metric("missing-evidence")
    evidence = make_evidence("missing-evidence")

    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink references missing Evidence",
    ):
        eligible_metric_evidence(
            metric,
            [],
            [make_link(metric, evidence)],
            T3,
        )


def test_result_order_is_deterministic():
    metric = make_metric("ordering")
    later = make_evidence(
        "later",
        published_at=datetime(2026, 9, 1, 9, 30, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
    )
    earlier = make_evidence("earlier")

    result = eligible_metric_evidence(
        metric,
        [later, earlier],
        [
            make_link(metric, later),
            make_link(metric, earlier),
        ],
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == (earlier, later)


def test_naive_cutoff_fails_closed():
    metric = make_metric("naive")
    evidence = make_evidence("naive")

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        eligible_metric_evidence(
            metric,
            [evidence],
            [make_link(metric, evidence)],
            datetime(2026, 9, 1, 12),
        )

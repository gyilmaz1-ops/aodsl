from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Evidence, Metric
from investment_domain.validation import (
    DomainValidationError,
    validate_node,
)


UTC = timezone.utc
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def evidence(*, supersedes_id=None):
    values = {
        "source_id": "source:test",
        "source_version": "v1",
        "content_hash": "0" * 64,
        "effective_at": NOW,
        "observed_at": NOW,
        "published_at": NOW,
        "ingested_at": NOW,
        "supersedes_id": supersedes_id,
        "source_uri": None,
    }

    identity = {
        "source_id": values["source_id"],
        "source_version": values["source_version"],
        "content_hash": values["content_hash"],
        "effective_at": values["effective_at"],
        "observed_at": values["observed_at"],
        "published_at": values["published_at"],
    }

    return Evidence(
        id=canonical_id("evidence", identity),
        **values,
    )


def metric(*, supersedes_id=None):
    values = {
        "subject_id": "company:test",
        "name": "revenue",
        "value": Decimal("1"),
        "unit": "currency",
        "period_start": NOW,
        "period_end": NOW,
        "effective_at": NOW,
        "observed_at": NOW,
        "published_at": NOW,
        "ingested_at": NOW,
        "source_id": "source:test",
        "source_version": "v1",
        "supersedes_id": supersedes_id,
        "currency": "USD",
    }

    identity = {
        "subject_id": values["subject_id"],
        "name": values["name"],
        "period_start": values["period_start"],
        "period_end": values["period_end"],
        "effective_at": values["effective_at"],
        "observed_at": values["observed_at"],
        "published_at": values["published_at"],
        "source_id": values["source_id"],
        "source_version": values["source_version"],
    }

    return Metric(
        id=canonical_id("metric", identity),
        **values,
    )


def test_evidence_accepts_no_predecessor():
    validate_node(evidence())


def test_evidence_accepts_canonical_predecessor():
    validate_node(
        evidence(
            supersedes_id="evidence:" + "1" * 64,
        )
    )


@pytest.mark.parametrize(
    "supersedes_id",
    [
        "evidence:bad",
        "evidence:",
        "evidence:" + "A" * 64,
        "metric:" + "1" * 64,
        "evidence:" + "1" * 63,
        "evidence:" + "1" * 65,
    ],
)
def test_evidence_rejects_noncanonical_predecessor(
    supersedes_id,
):
    with pytest.raises(
        DomainValidationError,
        match="IDM-C004",
    ):
        validate_node(
            evidence(
                supersedes_id=supersedes_id,
            )
        )


def test_metric_accepts_no_predecessor():
    validate_node(metric())


def test_metric_accepts_canonical_predecessor():
    validate_node(
        metric(
            supersedes_id="metric:" + "2" * 64,
        )
    )


@pytest.mark.parametrize(
    "supersedes_id",
    [
        "metric:bad",
        "metric:",
        "metric:" + "A" * 64,
        "evidence:" + "2" * 64,
        "metric:" + "2" * 63,
        "metric:" + "2" * 65,
    ],
)
def test_metric_rejects_noncanonical_predecessor(
    supersedes_id,
):
    with pytest.raises(
        DomainValidationError,
        match="IDM-C004",
    ):
        validate_node(
            metric(
                supersedes_id=supersedes_id,
            )
        )

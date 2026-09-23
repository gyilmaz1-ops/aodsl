from __future__ import annotations

from datetime import datetime, timezone

import pytest

from investment_domain.edges import EdgeType
from investment_domain.metrics import (
    MetricEvidenceLink,
    validate_metric_evidence_link,
)


NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)


def _link(**overrides) -> MetricEvidenceLink:
    values = {
        "metric_id": "metric:m1",
        "evidence_id": "evidence:e1",
        "relation": EdgeType.SUPPORTED_BY,
        "created_at": NOW,
    }
    values.update(overrides)
    return MetricEvidenceLink(**values)


def test_metric_supported_by_evidence_is_valid():
    validate_metric_evidence_link(_link())


@pytest.mark.parametrize(
    "metric_id",
    ["claim:c1", "metric:", "", "Metric:m1"],
)
def test_metric_id_must_be_nonempty_metric_identity(metric_id):
    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.metric_id must reference Metric",
    ):
        validate_metric_evidence_link(_link(metric_id=metric_id))


@pytest.mark.parametrize(
    "evidence_id",
    ["claim:c1", "evidence:", "", "Evidence:e1"],
)
def test_evidence_id_must_be_nonempty_evidence_identity(evidence_id):
    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.evidence_id must reference Evidence",
    ):
        validate_metric_evidence_link(_link(evidence_id=evidence_id))


@pytest.mark.parametrize(
    "relation",
    [
        EdgeType.CONTRADICTED_BY,
        EdgeType.DERIVED_FROM,
        EdgeType.AFFECTS,
    ],
)
def test_metric_evidence_relation_must_be_supported_by(relation):
    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.relation must be SUPPORTED_BY",
    ):
        validate_metric_evidence_link(_link(relation=relation))


def test_created_at_must_be_timezone_aware():
    with pytest.raises(
        ValueError,
        match="MetricEvidenceLink.created_at must be timezone-aware",
    ):
        validate_metric_evidence_link(
            _link(created_at=datetime(2026, 9, 23, 12))
        )

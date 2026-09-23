from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .edges import EdgeType
from .types import NodeType


class MetricPeriodKind(str, Enum):
    INSTANT = "instant"
    DURATION = "duration"


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    subject_type: NodeType
    unit: str
    period_kind: MetricPeriodKind
    requires_currency: bool = False


METRIC_DEFINITIONS: dict[str, MetricDefinition] = {
    "financial.revenue": MetricDefinition(
        name="financial.revenue",
        subject_type=NodeType.COMPANY,
        unit="currency",
        period_kind=MetricPeriodKind.DURATION,
        requires_currency=True,
    ),
    "financial.gross_margin": MetricDefinition(
        name="financial.gross_margin",
        subject_type=NodeType.COMPANY,
        unit="percent",
        period_kind=MetricPeriodKind.DURATION,
        requires_currency=False,
    ),
    "market.price": MetricDefinition(
        name="market.price",
        subject_type=NodeType.SECURITY,
        unit="currency",
        period_kind=MetricPeriodKind.INSTANT,
        requires_currency=True,
    ),
}


METRIC_ALIASES: dict[str, str] = {
    "revenue": "financial.revenue",
}


def metric_definition(name: str) -> MetricDefinition | None:
    canonical_name = METRIC_ALIASES.get(name, name)
    return METRIC_DEFINITIONS.get(canonical_name)


@dataclass(frozen=True)
class MetricEvidenceLink:
    metric_id: str
    evidence_id: str
    relation: EdgeType
    created_at: datetime


def validate_metric_evidence_link(
    link: MetricEvidenceLink,
) -> None:
    if not link.metric_id.startswith("metric:") or link.metric_id == "metric:":
        raise ValueError(
            "MetricEvidenceLink.metric_id must reference Metric"
        )

    if not link.evidence_id.startswith("evidence:") or link.evidence_id == "evidence:":
        raise ValueError(
            "MetricEvidenceLink.evidence_id must reference Evidence"
        )

    if link.relation is not EdgeType.SUPPORTED_BY:
        raise ValueError(
            "MetricEvidenceLink.relation must be SUPPORTED_BY"
        )

    if (
        link.created_at.tzinfo is None
        or link.created_at.utcoffset() is None
    ):
        raise ValueError(
            "MetricEvidenceLink.created_at must be timezone-aware"
        )

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

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

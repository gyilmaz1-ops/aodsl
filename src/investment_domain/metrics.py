from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Iterable

from .edges import EdgeType
from .nodes import Evidence, Metric
from .temporal import active_revision_at, available_at
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
    "financial.free_cash_flow": MetricDefinition(
        name="financial.free_cash_flow",
        subject_type=NodeType.COMPANY,
        unit="currency",
        period_kind=MetricPeriodKind.DURATION,
        requires_currency=True,
    ),
    "valuation.wacc": MetricDefinition(
        name="valuation.wacc",
        subject_type=NodeType.COMPANY,
        unit="ratio",
        period_kind=MetricPeriodKind.INSTANT,
        requires_currency=False,
    ),
    "valuation.terminal_growth_rate": MetricDefinition(
        name="valuation.terminal_growth_rate",
        subject_type=NodeType.COMPANY,
        unit="ratio",
        period_kind=MetricPeriodKind.INSTANT,
        requires_currency=False,
    ),
    "financial.net_debt": MetricDefinition(
        name="financial.net_debt",
        subject_type=NodeType.COMPANY,
        unit="currency",
        period_kind=MetricPeriodKind.INSTANT,
        requires_currency=True,
    ),
    "market.diluted_shares_outstanding": MetricDefinition(
        name="market.diluted_shares_outstanding",
        subject_type=NodeType.SECURITY,
        unit="shares",
        period_kind=MetricPeriodKind.INSTANT,
        requires_currency=False,
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


def eligible_metric_evidence(
    metric: Metric,
    evidence: Iterable[Evidence],
    links: Iterable[MetricEvidenceLink],
    research_cutoff: datetime,
) -> tuple[Evidence, ...]:
    """Resolve explicitly linked, active evidence at a historical cutoff.

    The Metric itself must be available at the cutoff. A superseded
    Evidence revision does not remain eligible once its successor becomes
    available. Metric provenance is not inherited by a successor: the
    active Evidence revision must itself have an explicit link.

    Missing references, duplicate links, malformed revision chains and
    mixed-Metric input fail closed.
    """
    if (
        research_cutoff.tzinfo is None
        or research_cutoff.utcoffset() is None
    ):
        raise ValueError("research_cutoff must be timezone-aware")

    if not available_at(metric, research_cutoff):
        return ()

    evidence_nodes = list(evidence)
    evidence_by_id: dict[str, Evidence] = {}

    for node in evidence_nodes:
        if node.id in evidence_by_id:
            raise ValueError("duplicate evidence identity")
        evidence_by_id[node.id] = node

    # Build Evidence revision components from explicit supersedes_id
    # relationships. source_id must not define revision lineage.
    adjacency: dict[str, set[str]] = {
        node.id: set()
        for node in evidence_nodes
    }

    for node in evidence_nodes:
        if node.supersedes_id is None:
            continue

        predecessor = evidence_by_id.get(node.supersedes_id)
        if predecessor is None:
            raise ValueError(
                "revision supersedes_id is outside supplied evidence set"
            )

        adjacency[node.id].add(predecessor.id)
        adjacency[predecessor.id].add(node.id)

    active_ids: set[str] = set()
    visited: set[str] = set()

    for node in evidence_nodes:
        if node.id in visited:
            continue

        component_ids: set[str] = set()
        stack = [node.id]

        while stack:
            current_id = stack.pop()

            if current_id in component_ids:
                continue

            component_ids.add(current_id)
            visited.add(current_id)
            stack.extend(
                adjacency[current_id] - component_ids
            )

        component = [
            evidence_by_id[node_id]
            for node_id in component_ids
        ]

        active = active_revision_at(
            component,
            research_cutoff,
        )

        if active is not None:
            active_ids.add(active.id)

    seen_links: set[tuple[str, str, EdgeType]] = set()
    result: list[Evidence] = []

    for link in links:
        validate_metric_evidence_link(link)

        if link.metric_id != metric.id:
            raise ValueError(
                "MetricEvidenceLink belongs to a different metric"
            )

        key = (
            link.metric_id,
            link.evidence_id,
            link.relation,
        )

        if key in seen_links:
            raise ValueError(
                "duplicate metric-evidence link"
            )

        seen_links.add(key)

        node = evidence_by_id.get(link.evidence_id)

        if node is None:
            raise ValueError(
                "MetricEvidenceLink references missing Evidence"
            )

        if link.created_at > research_cutoff:
            continue

        if node.id not in active_ids:
            continue

        result.append(node)

    return tuple(
        sorted(
            result,
            key=lambda node: (
                node.ingested_at,
                node.published_at,
                node.id,
            ),
        )
    )


def metric_revision_key(metric: Metric) -> tuple:
    """Return the fields that must remain invariant across Metric revisions.

    Revision lineage is explicit through supersedes_id. This key validates
    whether two explicitly linked Metric nodes describe the same measurement;
    it must never be used to discover or infer lineage.
    """
    return (
        metric.subject_id,
        metric.name,
        metric.period_start,
        metric.period_end,
        metric.unit,
        metric.currency,
        metric.source_id,
    )


def validate_metric_revision(
    predecessor: Metric,
    successor: Metric,
) -> None:
    """Validate one explicit predecessor -> successor Metric revision."""

    if successor.supersedes_id == successor.id:
        raise ValueError("Metric revision cannot supersede itself")

    if successor.supersedes_id != predecessor.id:
        raise ValueError(
            "Metric revision supersedes_id must reference predecessor"
        )

    if metric_revision_key(successor) != metric_revision_key(predecessor):
        raise ValueError(
            "Metric revision key must match predecessor"
        )

    if successor.ingested_at <= predecessor.ingested_at:
        raise ValueError(
            "Metric revision must be ingested after predecessor"
        )

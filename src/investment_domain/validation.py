from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .edges import ALLOWED_EDGES, Edge
from .identity import canonical_id
from .metrics import MetricPeriodKind, metric_definition
from .nodes import (
    Calculation,
    Claim,
    Company,
    Estimate,
    Evidence,
    Forecast,
    Metric,
    Security,
    Valuation,
)
from .types import NodeType


class DomainValidationError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


ID_PREFIX = {
    NodeType.COMPANY: "company:",
    NodeType.SECURITY: "security:",
    NodeType.METRIC: "metric:",
    NodeType.CLAIM: "claim:",
    NodeType.EVIDENCE: "evidence:",
    NodeType.CALCULATION: "calculation:",
    NodeType.ESTIMATE: "estimate:",
    NodeType.FORECAST: "forecast:",
    NodeType.CATALYST: "catalyst:",
    NodeType.CATALYST_IMPACT: "catalyst_impact:",
    NodeType.RISK: "risk:",
    NodeType.VALUATION: "valuation:",
    NodeType.RECOMMENDATION: "recommendation:",
}


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DomainValidationError(
            "IDM-C004",
            f"{name} must be timezone-aware",
        )


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise DomainValidationError("IDM-C001", f"{name} must not be empty")


def _validate_temporal(node) -> None:
    for f in fields(node):
        value = getattr(node, f.name)
        if isinstance(value, datetime):
            _aware(value, f.name)

    if isinstance(node, Evidence):
        # effective_at is economic applicability, not information
        # availability. Evidence may validly describe a future-effective
        # event already known to the system.
        if node.published_at < node.observed_at:
            raise DomainValidationError(
                "IDM-C004",
                "Evidence.published_at precedes observed_at",
            )
        if node.ingested_at < node.published_at:
            raise DomainValidationError(
                "IDM-C004",
                "Evidence.ingested_at precedes published_at",
            )
        if node.supersedes_id is not None:
            if not node.supersedes_id.startswith("evidence:"):
                raise DomainValidationError(
                    "IDM-C004",
                    "Evidence.supersedes_id must reference Evidence",
                )
            if node.supersedes_id == node.id:
                raise DomainValidationError(
                    "IDM-C004",
                    "Evidence cannot supersede itself",
                )

    if isinstance(node, Estimate):
        definition = metric_definition(node.metric_name)

        if definition is None:
            raise DomainValidationError(
                "IDM-C004",
                f"unknown Estimate.metric_name: {node.metric_name}",
            )

        expected_prefix = ID_PREFIX.get(definition.subject_type)
        if expected_prefix is None:
            raise DomainValidationError(
                "IDM-C004",
                "unsupported Estimate subject type: "
                f"{definition.subject_type.value}",
            )

        if (
            not node.subject_id.startswith(expected_prefix)
            or len(node.subject_id) == len(expected_prefix)
        ):
            raise DomainValidationError(
                "IDM-C004",
                "Estimate.subject_id must reference "
                f"{definition.subject_type.value}",
            )

        if node.unit != definition.unit:
            raise DomainValidationError(
                "IDM-C004",
                f"Estimate.unit must be {definition.unit} "
                f"for {node.metric_name}",
            )

        if definition.requires_currency:
            if node.currency is None:
                raise DomainValidationError(
                    "IDM-C004",
                    "Estimate.currency is required "
                    f"for {node.metric_name}",
                )
        elif node.currency is not None:
            raise DomainValidationError(
                "IDM-C004",
                "Estimate.currency is forbidden "
                f"for {node.metric_name}",
            )

        if node.currency is not None:
            if (
                len(node.currency) != 3
                or not node.currency.isascii()
                or not node.currency.isalpha()
                or node.currency != node.currency.upper()
            ):
                raise DomainValidationError(
                    "IDM-C004",
                    "Estimate.currency must be an uppercase "
                    "three-letter code",
                )

        if not isinstance(node.value, Decimal):
            raise DomainValidationError(
                "IDM-C001",
                "Estimate.value must be Decimal",
            )

        if not node.value.is_finite():
            raise DomainValidationError(
                "IDM-C001",
                "Estimate.value must be finite",
            )

    if isinstance(node, Metric):
        definition = metric_definition(node.name)
        if definition is None:
            raise DomainValidationError(
                "IDM-C004",
                f"unknown Metric.name: {node.name}",
            )

        expected_prefix = ID_PREFIX.get(definition.subject_type)
        if expected_prefix is None:
            raise DomainValidationError(
                "IDM-C004",
                f"unsupported Metric subject type: {definition.subject_type.value}",
            )
        if (
            not node.subject_id.startswith(expected_prefix)
            or len(node.subject_id) == len(expected_prefix)
        ):
            raise DomainValidationError(
                "IDM-C004",
                f"Metric.subject_id must reference {definition.subject_type.value}",
            )

        if node.unit != definition.unit:
            raise DomainValidationError(
                "IDM-C004",
                f"Metric.unit must be {definition.unit} for {node.name}",
            )

        if definition.requires_currency:
            if node.currency is None:
                raise DomainValidationError(
                    "IDM-C004",
                    f"Metric.currency is required for {node.name}",
                )
        elif node.currency is not None:
            raise DomainValidationError(
                "IDM-C004",
                f"Metric.currency is forbidden for {node.name}",
            )

        if node.currency is not None:
            if (
                len(node.currency) != 3
                or not node.currency.isascii()
                or not node.currency.isalpha()
                or node.currency != node.currency.upper()
            ):
                raise DomainValidationError(
                    "IDM-C004",
                    "Metric.currency must be an uppercase three-letter code",
                )

        if (
            definition.period_kind is MetricPeriodKind.INSTANT
            and node.period_start is not None
        ):
            raise DomainValidationError(
                "IDM-C004",
                f"Metric.period_start must be None for instant metric {node.name}",
            )

        if (
            definition.period_kind is MetricPeriodKind.DURATION
            and node.period_start is None
        ):
            raise DomainValidationError(
                "IDM-C004",
                f"Metric.period_start is required for duration metric {node.name}",
            )

        if node.period_start and node.period_start > node.period_end:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.period_start exceeds period_end",
            )
        if node.observed_at < node.effective_at:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.observed_at precedes effective_at",
            )
        if node.published_at < node.observed_at:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.published_at precedes observed_at",
            )
        if node.ingested_at < node.published_at:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.ingested_at precedes published_at",
            )
        if node.supersedes_id is not None:
            if not node.supersedes_id.startswith("metric:"):
                raise DomainValidationError(
                    "IDM-C004",
                    "Metric.supersedes_id must reference Metric",
                )
            if node.supersedes_id == node.id:
                raise DomainValidationError(
                    "IDM-C004",
                    "Metric cannot supersede itself",
                )


def _expected_identity(node):
    if isinstance(node, Company):
        return canonical_id("company", {"canonical_name": node.canonical_name})

    if isinstance(node, Security):
        return canonical_id(
            "security",
            {"venue": node.venue, "ticker": node.ticker},
        )

    if isinstance(node, Evidence):
        return canonical_id("evidence", {
            "source_id": node.source_id,
            "source_version": node.source_version,
            "content_hash": node.content_hash,
            "effective_at": node.effective_at,
            "observed_at": node.observed_at,
            "published_at": node.published_at,
        })

    if isinstance(node, Metric):
        return canonical_id("metric", {
            "subject_id": node.subject_id,
            "name": node.name,
            "period_start": node.period_start,
            "period_end": node.period_end,
            "effective_at": node.effective_at,
            "observed_at": node.observed_at,
            "published_at": node.published_at,
            "source_id": node.source_id,
            "source_version": node.source_version,
        })

    if isinstance(node, Claim):
        return canonical_id("claim", {
            "subject_id": node.subject_id,
            "predicate": node.predicate,
            "object_value": node.object_value,
            "object_ref": node.object_ref,
            "polarity": node.polarity,
            "scope": node.scope,
            "as_of": node.as_of,
        })

    if isinstance(node, Calculation):
        return canonical_id("calculation", {
            "subject_id": node.subject_id,
            "formula": node.formula,
            "input_ids": node.input_ids,
            "model_version": node.model_version,
        })
    if isinstance(node, Estimate):
        return canonical_id("estimate", {
            "subject_id": node.subject_id,
            "metric_name": node.metric_name,
            "period_end": node.period_end,
            "value": node.value,
            "unit": node.unit,
            "scenario": node.scenario,
            "model_version": node.model_version,
            "as_of": node.as_of,
            "currency": node.currency,
        })

    # Remaining analytical node identities are content-addressed over all
    # semantic fields except id/node_type. This keeps Slice A deterministic
    # while allowing later node-specific identity contracts.
    payload = {
        f.name: getattr(node, f.name)
        for f in fields(node)
        if f.name not in {"id", "node_type", "metadata"}
    }
    if node.node_type == NodeType.CATALYST_IMPACT:
        if node.direction not in {
            "POSITIVE",
            "NEGATIVE",
            "NEUTRAL",
        }:
            raise DomainValidationError(
                "IDM-C017",
                "unsupported catalyst impact direction",
            )

        if node.magnitude not in {
            "LOW",
            "MEDIUM",
            "HIGH",
        }:
            raise DomainValidationError(
                "IDM-C018",
                "unsupported catalyst impact magnitude",
            )

        if node.horizon not in {
            "NEAR_TERM",
            "MEDIUM_TERM",
            "LONG_TERM",
        }:
            raise DomainValidationError(
                "IDM-C019",
                "unsupported catalyst impact horizon",
            )

        for field_name in ("probability", "confidence"):
            value = getattr(node, field_name)

            if not isinstance(value, Decimal):
                raise DomainValidationError(
                    "IDM-C020",
                    f"{field_name} must be Decimal",
                )

            if not value.is_finite():
                raise DomainValidationError(
                    "IDM-C021",
                    f"{field_name} must be finite",
                )

            if value < Decimal("0") or value > Decimal("1"):
                raise DomainValidationError(
                    "IDM-C021",
                    f"{field_name} must be within [0, 1]",
                )

    kind = (
        "catalyst_impact"
        if node.node_type == NodeType.CATALYST_IMPACT
        else node.node_type.value.lower()
    )
    return canonical_id(kind, payload)


def validate_node(node) -> None:
    if not is_dataclass(node) or not hasattr(node, "node_type") or not hasattr(node, "id"):
        raise DomainValidationError("IDM-C001", "unsupported node object")

    prefix = ID_PREFIX.get(node.node_type)
    if prefix is None or not node.id.startswith(prefix):
        raise DomainValidationError("IDM-C003", "node id prefix/type mismatch")

    for f in fields(node):
        value = getattr(node, f.name)
        if isinstance(value, str) and f.name not in {"object_value", "source_uri"}:
            _nonempty(value, f.name)
        if isinstance(value, float):
            raise DomainValidationError(
                "IDM-C001",
                f"{f.name} must not use binary float",
            )

    if isinstance(node, Security):
        if re.fullmatch(r"company:[a-z0-9._](?:[a-z0-9._-]*[a-z0-9._])?", node.company_id) is None:
            raise DomainValidationError(
                "IDM-C004",
                "Security.company_id must be a canonical Company identity",
            )

    if isinstance(node, Claim):
        if (node.object_value is None) == (node.object_ref is None):
            raise DomainValidationError(
                "IDM-C001",
                "Claim requires exactly one of object_value or object_ref",
            )
        if node.polarity not in {"POSITIVE", "NEGATIVE"}:
            raise DomainValidationError("IDM-C001", "invalid Claim.polarity")

    if isinstance(node, Evidence):
        if not re.fullmatch(r"[0-9a-f]{64}", node.content_hash):
            raise DomainValidationError(
                "IDM-C001",
                "Evidence.content_hash must be lowercase SHA-256",
            )

    if isinstance(node, (Metric, Calculation, Estimate, Valuation)):
        if not isinstance(node.value, Decimal):
            raise DomainValidationError(
                "IDM-C001",
                f"{node.node_type.value}.value must be Decimal",
            )

    if isinstance(node, Forecast):
        if node.scenario not in {"BASE", "BULL", "BEAR"}:
            raise DomainValidationError(
                "IDM-C004",
                "unsupported Forecast.scenario",
            )

    if isinstance(node, Valuation):
        if re.fullmatch(
            r"security:[a-z0-9._](?:[a-z0-9._-]*[a-z0-9._])?:"
            r"[a-z0-9._](?:[a-z0-9._-]*[a-z0-9._])?",
            node.security_id,
        ) is None:
            raise DomainValidationError(
                "IDM-C004",
                "Valuation.security_id must be a canonical Security identity",
            )

        if node.method not in {"DCF", "EV_EBITDA", "PE"}:
            raise DomainValidationError(
                "IDM-C004",
                "unsupported Valuation.method",
            )

        if node.scenario not in {"BASE", "BULL", "BEAR"}:
            raise DomainValidationError(
                "IDM-C004",
                "unsupported Valuation.scenario",
            )

        if not re.fullmatch(r"[A-Z]{3}", node.currency):
            raise DomainValidationError(
                "IDM-C004",
                "Valuation.currency must be a 3-letter uppercase code",
            )

        if not node.value.is_finite():
            raise DomainValidationError(
                "IDM-C004",
                "Valuation.value must be finite",
            )

    _validate_temporal(node)

    expected = _expected_identity(node)
    if node.id != expected:
        raise DomainValidationError(
            "IDM-C003",
            f"non-canonical identity: expected {expected}",
        )


def validate_edge(edge: Edge) -> None:
    if not isinstance(edge, Edge):
        raise DomainValidationError("IDM-C002", "unsupported edge object")

    if not edge.source_id or not edge.target_id:
        raise DomainValidationError("IDM-C002", "edge endpoints must not be empty")

    if edge.source_id == edge.target_id:
        raise DomainValidationError("IDM-C002", "self-edge is forbidden")

    source_prefix = ID_PREFIX.get(edge.source_type)
    target_prefix = ID_PREFIX.get(edge.target_type)

    if source_prefix is None or not edge.source_id.startswith(source_prefix):
        raise DomainValidationError(
            "IDM-C002",
            "edge source_id does not match source_type",
        )

    if target_prefix is None or not edge.target_id.startswith(target_prefix):
        raise DomainValidationError(
            "IDM-C002",
            "edge target_id does not match target_type",
        )

    if (edge.source_type, edge.edge_type, edge.target_type) not in ALLOWED_EDGES:
        raise DomainValidationError(
            "IDM-C002",
            "edge source/type/target combination is not allowed",
        )

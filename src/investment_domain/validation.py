from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from decimal import Decimal

from .edges import ALLOWED_EDGES, Edge
from .identity import canonical_id
from .nodes import (
    Calculation,
    Claim,
    Company,
    Estimate,
    Evidence,
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
        if node.ingested_at < node.published_at:
            raise DomainValidationError(
                "IDM-C004",
                "Evidence.ingested_at precedes published_at",
            )

    if isinstance(node, Metric):
        if node.period_start and node.period_start > node.period_end:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.period_start exceeds period_end",
            )
        if node.ingested_at < node.published_at:
            raise DomainValidationError(
                "IDM-C004",
                "Metric.ingested_at precedes published_at",
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
        })

    if isinstance(node, Metric):
        return canonical_id("metric", {
            "subject_id": node.subject_id,
            "name": node.name,
            "period_start": node.period_start,
            "period_end": node.period_end,
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

    # Remaining analytical node identities are content-addressed over all
    # semantic fields except id/node_type. This keeps Slice A deterministic
    # while allowing later node-specific identity contracts.
    payload = {
        f.name: getattr(node, f.name)
        for f in fields(node)
        if f.name not in {"id", "node_type", "metadata"}
    }
    return canonical_id(node.node_type.value.lower(), payload)


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

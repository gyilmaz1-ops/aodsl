"""Investment Domain Model v1.0 — canonical investment semantics."""

from .edges import Edge, EdgeType
from .identity import canonical_id
from .nodes import (
    Calculation,
    Catalyst,
    Claim,
    Company,
    Estimate,
    Evidence,
    Forecast,
    Metric,
    Recommendation,
    Risk,
    Security,
    Valuation,
)
from .types import NodeType
from .validation import DomainValidationError, validate_edge, validate_node

__version__ = "1.0.0"

__all__ = [
    "NodeType",
    "EdgeType",
    "Edge",
    "Company",
    "Security",
    "Metric",
    "Claim",
    "Evidence",
    "Calculation",
    "Estimate",
    "Forecast",
    "Catalyst",
    "Risk",
    "Valuation",
    "Recommendation",
    "canonical_id",
    "DomainValidationError",
    "validate_node",
    "validate_edge",
]

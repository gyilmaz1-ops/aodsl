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
from .claims import (
    ClaimEvidenceLink,
    ClaimStatus,
    eligible_claim_evidence,
    validate_claim_evidence_link,
    validate_claim_transition,
)
from .repository import EvidenceRepository
from .postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)
from .temporal import active_revision_at, available_at
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
    "available_at",
    "active_revision_at",
    "EvidenceRepository",
    "PostgreSQLEvidenceRepository",
    "RepositoryWriteError",
    "ClaimStatus",
    "ClaimEvidenceLink",
    "validate_claim_transition",
    "validate_claim_evidence_link",
    "eligible_claim_evidence",
]

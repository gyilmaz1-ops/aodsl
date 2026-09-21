import pytest

from investment_domain import Edge, EdgeType, NodeType, validate_edge
from investment_domain.validation import DomainValidationError


def test_supported_by_edge_allowed():
    validate_edge(Edge(
        source_id="claim:a",
        source_type=NodeType.CLAIM,
        edge_type=EdgeType.SUPPORTED_BY,
        target_id="evidence:b",
        target_type=NodeType.EVIDENCE,
    ))


def test_recommendation_cannot_be_evidence():
    edge = Edge(
        source_id="claim:a",
        source_type=NodeType.CLAIM,
        edge_type=EdgeType.SUPPORTED_BY,
        target_id="recommendation:b",
        target_type=NodeType.RECOMMENDATION,
    )
    with pytest.raises(DomainValidationError, match="IDM-C002"):
        validate_edge(edge)

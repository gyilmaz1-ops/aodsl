from __future__ import annotations

import inspect

from investment_domain.edges import EdgeType
from investment_domain.postgres_migrations import ADD_METRIC_EVIDENCE_EDGES
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_add_metric_evidence_link():
    assert hasattr(EvidenceRepository, "add_metric_evidence_link")

    sig = inspect.signature(EvidenceRepository.add_metric_evidence_link)
    assert list(sig.parameters) == ["self", "link"]


def test_postgres_repository_exposes_add_metric_evidence_link():
    assert hasattr(PostgreSQLEvidenceRepository, "add_metric_evidence_link")

    sig = inspect.signature(
        PostgreSQLEvidenceRepository.add_metric_evidence_link
    )
    assert list(sig.parameters) == ["self", "link"]


def test_schema_v5_adds_metric_evidence_edges():
    migration = ADD_METRIC_EVIDENCE_EDGES

    assert migration.version == 5
    assert migration.name == "add_metric_evidence_edges"


def test_v5_allows_only_canonical_metric_evidence_relation():
    sql = " ".join(
        "\n".join(ADD_METRIC_EVIDENCE_EDGES.statements).split()
    )

    assert "source_type = 'Claim'" in sql
    assert "edge_type IN ( 'SUPPORTED_BY', 'CONTRADICTED_BY' )" in sql

    assert "source_type = 'Metric'" in sql
    assert "edge_type = 'SUPPORTED_BY'" in sql

    assert "target_type = 'Evidence'" in sql


def test_domain_contract_metric_supported_by_evidence_is_canonical():
    from investment_domain.edges import ALLOWED_EDGES
    from investment_domain.types import NodeType

    assert (
        NodeType.METRIC,
        EdgeType.SUPPORTED_BY,
        NodeType.EVIDENCE,
    ) in ALLOWED_EDGES


def test_domain_contract_metric_contradicted_by_evidence_is_forbidden():
    from investment_domain.edges import ALLOWED_EDGES
    from investment_domain.types import NodeType

    assert (
        NodeType.METRIC,
        EdgeType.CONTRADICTED_BY,
        NodeType.EVIDENCE,
    ) not in ALLOWED_EDGES

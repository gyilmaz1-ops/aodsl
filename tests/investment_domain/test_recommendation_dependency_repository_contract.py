from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
)
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_recommendation_dependency_writer():
    assert hasattr(
        EvidenceRepository,
        "add_recommendation_dependencies",
    )


def test_postgres_repository_exposes_recommendation_dependency_writer():
    assert hasattr(
        PostgreSQLEvidenceRepository,
        "add_recommendation_dependencies",
    )

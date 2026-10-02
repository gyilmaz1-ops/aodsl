from datetime import datetime
from pathlib import Path
from typing import get_type_hints

from investment_domain.nodes import Recommendation
from investment_domain.repository import EvidenceRepository


SOURCE = Path(
    "src/investment_domain/postgres_repository.py"
).read_text()


def section(name: str, next_name: str) -> str:
    start = SOURCE.index(f"    def {name}(")
    end = SOURCE.index(f"    def {next_name}(", start)
    return SOURCE[start:end]


def test_protocol_exposes_recommendation_at():
    assert hasattr(EvidenceRepository, "recommendation_at")


def test_recommendation_at_return_contract():
    method = EvidenceRepository.recommendation_at
    hints = get_type_hints(method)

    assert hints["recommendation_id"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Recommendation | None


def test_protocol_exposes_latest_recommendation_at():
    assert hasattr(EvidenceRepository, "latest_recommendation_at")


def test_latest_recommendation_at_return_contract():
    method = EvidenceRepository.latest_recommendation_at
    hints = get_type_hints(method)

    assert hints["security_id"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Recommendation | None


def test_postgres_recommendation_at_contract():
    source = section(
        "recommendation_at",
        "latest_recommendation_at",
    )

    assert 'kind="recommendation"' in source
    assert "research_cutoff must be timezone-aware" in source
    assert "FROM domain_nodes" in source
    assert "FROM recommendation_facts" in source
    assert "IDM-R563: RECOMMENDATION_TYPE_MISMATCH" in source
    assert "IDM-R564: INVALID_STORED_RECOMMENDATION" in source
    assert "IDM-R565: RECOMMENDATION_INTEGRITY_FAILURE" in source
    assert "IDM-R566: RECOMMENDATION_PROJECTION_NOT_FOUND" in source
    assert "recommendation.as_of > research_cutoff" in source


def test_postgres_latest_recommendation_at_contract():
    source = section(
        "latest_recommendation_at",
        "add_valuation",
    )

    assert "security_id must not be empty" in source
    assert "research_cutoff must be timezone-aware" in source
    assert "FROM recommendation_facts" in source
    assert "MAX(as_of)" in source
    assert "as_of <= %s" in source
    assert "ORDER BY node_id" in source
    assert "IDM-R567: RECOMMENDATION_PIT_AMBIGUITY" in source
    assert "self.recommendation_at(" in source

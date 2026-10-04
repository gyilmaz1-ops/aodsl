from datetime import datetime
from typing import get_type_hints

from investment_domain.nodes import Valuation
from investment_domain.repository import EvidenceRepository


def test_protocol_exposes_valuation_at():
    assert hasattr(EvidenceRepository, "valuation_at")


def test_valuation_at_return_contract():
    method = EvidenceRepository.valuation_at
    hints = get_type_hints(method)

    assert hints["valuation_id"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Valuation | None


def test_protocol_exposes_latest_valuation_at():
    assert hasattr(EvidenceRepository, "latest_valuation_at")


def test_latest_valuation_at_return_contract():
    method = EvidenceRepository.latest_valuation_at
    hints = get_type_hints(method)

    assert hints["security_id"] is str
    assert hints["method"] is str
    assert hints["scenario"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Valuation | None

from pathlib import Path


SOURCE = Path(
    "src/investment_domain/postgres_repository.py"
).read_text()


def section(name: str, next_name: str) -> str:
    start = SOURCE.index(f"    def {name}(")
    end = SOURCE.index(f"    def {next_name}(", start)
    return SOURCE[start:end]


def test_postgres_valuation_at_contract():
    source = section(
        "valuation_at",
        "latest_valuation_at",
    )

    assert 'kind="valuation"' in source
    assert "research_cutoff must be timezone-aware" in source
    assert "FROM domain_nodes" in source
    assert "FROM valuation_facts" in source
    assert "IDM-R568: VALUATION_TYPE_MISMATCH" in source
    assert "IDM-R569: INVALID_STORED_VALUATION" in source
    assert "IDM-R570: VALUATION_INTEGRITY_FAILURE" in source
    assert "IDM-R571: VALUATION_PROJECTION_NOT_FOUND" in source
    assert "valuation.as_of > research_cutoff" in source


def test_postgres_latest_valuation_at_contract():
    source = section(
        "latest_valuation_at",
        "add_valuation",
    )

    assert "security_id must not be empty" in source
    assert "method must not be empty" in source
    assert "scenario must not be empty" in source
    assert "research_cutoff must be timezone-aware" in source
    assert "FROM valuation_facts" in source
    assert "security_id = %s" in source
    assert "method = %s" in source
    assert "scenario = %s" in source
    assert "MAX(as_of)" in source
    assert "as_of <= %s" in source
    assert "ORDER BY node_id" in source
    assert "IDM-R572: VALUATION_PIT_AMBIGUITY" in source
    assert "self.valuation_at(" in source

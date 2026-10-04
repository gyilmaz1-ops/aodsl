from datetime import datetime
from typing import get_type_hints

from investment_domain.nodes import Evidence
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_has_evidence_at():
    assert hasattr(EvidenceRepository, "evidence_at")


def test_repository_protocol_has_latest_evidence_at():
    assert hasattr(EvidenceRepository, "latest_evidence_at")


def test_postgres_repository_has_evidence_at():
    assert hasattr(PostgreSQLEvidenceRepository, "evidence_at")


def test_postgres_repository_has_latest_evidence_at():
    assert hasattr(PostgreSQLEvidenceRepository, "latest_evidence_at")


def test_evidence_at_type_contract():
    hints = get_type_hints(EvidenceRepository.evidence_at)

    assert hints == {
        "evidence_id": str,
        "research_cutoff": datetime,
        "return": Evidence | None,
    }


def test_latest_evidence_at_type_contract():
    hints = get_type_hints(EvidenceRepository.latest_evidence_at)

    assert hints == {
        "evidence_id": str,
        "research_cutoff": datetime,
        "return": Evidence | None,
    }

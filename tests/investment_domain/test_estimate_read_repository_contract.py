from datetime import datetime
from typing import get_type_hints

from investment_domain.nodes import Estimate
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_estimate_at():
    assert hasattr(EvidenceRepository, "estimate_at")


def test_repository_protocol_exposes_latest_estimate_at():
    assert hasattr(EvidenceRepository, "latest_estimate_at")


def test_postgres_repository_exposes_estimate_at():
    assert hasattr(PostgreSQLEvidenceRepository, "estimate_at")


def test_postgres_repository_exposes_latest_estimate_at():
    assert hasattr(PostgreSQLEvidenceRepository, "latest_estimate_at")


def test_estimate_at_return_contract():
    hints = get_type_hints(EvidenceRepository.estimate_at)

    assert hints["estimate_id"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Estimate | None


def test_latest_estimate_at_return_contract():
    hints = get_type_hints(EvidenceRepository.latest_estimate_at)

    assert hints["subject_id"] is str
    assert hints["metric_name"] is str
    assert hints["period_end"] is datetime
    assert hints["scenario"] is str
    assert hints["model_version"] is str
    assert hints["research_cutoff"] is datetime
    assert hints["return"] == Estimate | None

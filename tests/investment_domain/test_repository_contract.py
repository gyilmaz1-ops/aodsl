from datetime import datetime
from typing import get_type_hints

from investment_domain import EvidenceRepository


def test_evidence_repository_contract_is_runtime_visible():
    assert EvidenceRepository.__name__ == "EvidenceRepository"

    expected = {
        "add_evidence",
        "add_claim_evidence_link",
        "evidence_for_claim_at",
    }

    assert expected <= set(EvidenceRepository.__dict__)


def test_historical_read_requires_explicit_research_cutoff():
    hints = get_type_hints(
        EvidenceRepository.evidence_for_claim_at
    )

    assert hints["claim_id"] is str
    assert hints["research_cutoff"] is datetime

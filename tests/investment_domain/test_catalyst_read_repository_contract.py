from datetime import datetime
from typing import get_type_hints

from investment_domain.nodes import Catalyst
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_catalyst_at():
    assert hasattr(EvidenceRepository, "catalyst_at")

    annotations = get_type_hints(
        EvidenceRepository.catalyst_at
    )

    assert annotations["catalyst_id"] is str
    assert annotations["research_cutoff"] is datetime
    assert annotations["return"] == Catalyst | None


def test_repository_protocol_exposes_latest_catalyst_at():
    assert hasattr(EvidenceRepository, "latest_catalyst_at")

    annotations = get_type_hints(
        EvidenceRepository.latest_catalyst_at
    )

    assert annotations["subject_id"] is str
    assert annotations["research_cutoff"] is datetime
    assert annotations["return"] == Catalyst | None

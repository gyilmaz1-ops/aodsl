from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .claims import ClaimEvidenceLink
from .edges import EdgeType
from .nodes import Evidence


class EvidenceRepository(Protocol):
    """Persistence-independent Evidence/Claim repository contract."""

    def add_evidence(
        self,
        evidence: Evidence,
    ) -> None:
        ...

    def add_claim_evidence_link(
        self,
        link: ClaimEvidenceLink,
    ) -> None:
        ...

    def evidence_for_claim_at(
        self,
        claim_id: str,
        research_cutoff: datetime,
    ) -> tuple[tuple[Evidence, EdgeType], ...]:
        ...

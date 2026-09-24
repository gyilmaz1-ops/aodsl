from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .claims import ClaimEvidenceLink
from .edges import EdgeType
from .nodes import Claim, Evidence, Metric
from .metrics import MetricEvidenceLink


class EvidenceRepository(Protocol):
    """Persistence-independent Evidence/Claim repository contract."""

    def add_claim(
        self,
        claim: Claim,
    ) -> None:
        ...

    def add_evidence(
        self,
        evidence: Evidence,
    ) -> None:
        ...

    def add_metric(
        self,
        metric: Metric,
    ) -> None:
        ...

    def add_claim_evidence_link(
        self,
        link: ClaimEvidenceLink,
    ) -> None:
        ...

    def add_metric_evidence_link(
        self,
        link: MetricEvidenceLink,
    ) -> None:
        ...

    def active_metric_at(
        self,
        metric_id: str,
        research_cutoff: datetime,
    ) -> Metric | None:
        ...

    def evidence_for_metric_at(
        self,
        metric_id: str,
        research_cutoff: datetime,
    ) -> tuple[Evidence, ...]:
        ...

    def evidence_for_claim_at(
        self,
        claim_id: str,
        research_cutoff: datetime,
    ) -> tuple[tuple[Evidence, EdgeType], ...]:
        ...

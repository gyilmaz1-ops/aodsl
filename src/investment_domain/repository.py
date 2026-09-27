from __future__ import annotations


from datetime import datetime
from typing import Protocol

from .claims import ClaimEvidenceLink
from .edges import EdgeType
from .nodes import (
    CatalystImpact,
    Calculation,
    Claim,
    Estimate,
    Evidence,
    Catalyst,
    Forecast,
    Metric,
    Valuation,
)
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

    def add_estimate(
        self,
        estimate: Estimate,
    ) -> None:
        ...

    def add_forecast(
        self,
        forecast: Forecast,
    ) -> None:
        ...

    def add_catalyst(
        self,
        catalyst: Catalyst,
    ) -> None:
        ...

    def add_catalyst_impact(
        self,
        catalyst_impact: CatalystImpact,
    ) -> None:
        ...

    def catalyst_impact_at(
        self,
        catalyst_impact_id: str,
        research_cutoff: datetime,
    ) -> CatalystImpact | None:
        ...

    def latest_catalyst_impact_at(
        self,
        catalyst_id: str,
        target_id: str,
        research_cutoff: datetime,
    ) -> CatalystImpact | None:
        ...

    def add_catalyst_affects(
        self,
        catalyst_id: str,
        target_ids: tuple[str, ...],
    ) -> None:
        ...

    def add_forecast_estimates(
        self,
        forecast_id: str,
        estimate_ids: tuple[str, ...],
    ) -> None:
        ...

    def add_valuation(
        self,
        valuation: Valuation,
    ) -> None:
        ...

    def add_valuation_dependencies(
        self,
        valuation_id: str,
        dependency_ids: tuple[str, ...],
    ) -> None:
        ...

    def add_estimate_inputs(
        self,
        estimate_id: str,
        input_ids: tuple[str, ...],
    ) -> None:
        ...

    def add_calculation(
        self,
        calculation: Calculation,
    ) -> None:
        ...

    def add_calculation_inputs(
        self,
        calculation_id: str,
    ) -> None:
        ...

    def calculation_at(
        self,
        calculation_id: str,
        research_cutoff: datetime,
    ) -> Calculation | None:
        ...

    def verify_calculation(
        self,
        calculation_id: str,
    ) -> Calculation:
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

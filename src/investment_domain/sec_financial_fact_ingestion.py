from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .ingestion import (
    ExternalObservation,
    normalize_external_observation,
)
from .metrics import MetricEvidenceLink
from .nodes import Evidence, Metric
from .repository import EvidenceRepository
from .sec_edgar import SecFilingRequest
from .sec_edgar_discovery import SecFilingSelector
from .sec_financial_fact_projection import (
    project_sec_financial_fact,
)
from .sec_financial_fact_selection import select_revenue_fact
from .sec_financial_facts import extract_financial_facts_from_ixbrl


class SecFilingResolver(Protocol):
    def resolve(
        self,
        selector: SecFilingSelector,
    ) -> SecFilingRequest:
        ...


class SecFilingProvider(Protocol):
    def fetch_filing(
        self,
        request: SecFilingRequest,
    ) -> tuple[ExternalObservation, ...]:
        ...


class SecFinancialFactRepository(EvidenceRepository, Protocol):
    def add_metric_with_evidence_link(
        self,
        metric: Metric,
        link: MetricEvidenceLink,
    ) -> None:
        ...


@dataclass(frozen=True)
class SecFinancialFactIngestionResult:
    evidence: Evidence
    metrics: tuple[Metric, ...]


class SecFinancialFactIngestionService:
    """
    Ingest one resolved SEC filing into canonical Evidence and financial Metrics.

    The filing is fetched exactly once. Parsing and projection complete before
    repository persistence begins, so malformed or unsupported filing content
    cannot leave a partial Evidence-only ingestion state.
    """

    def __init__(
        self,
        *,
        resolver: SecFilingResolver,
        provider: SecFilingProvider,
        repository: SecFinancialFactRepository,
    ) -> None:
        self._resolver = resolver
        self._provider = provider
        self._repository = repository

    def execute(
        self,
        selector: SecFilingSelector,
        *,
        subject_id: str,
        created_at: datetime,
    ) -> SecFinancialFactIngestionResult:
        request = self._resolver.resolve(selector)
        observations = self._provider.fetch_filing(request)

        if len(observations) != 1:
            raise ValueError(
                "SEC filing fetch must return exactly one observation"
            )

        observation = observations[0]

        # Build canonical Evidence without persistence. This preserves the
        # all-validation-before-write contract for parser/projection failures.
        evidence = normalize_external_observation(observation)

        facts = extract_financial_facts_from_ixbrl(
            observation.raw_content
        )

        selected_fact = select_revenue_fact(
            facts,
            effective_at=request.effective_at,
        )

        projected = project_sec_financial_fact(
            fact=selected_fact,
            evidence=evidence,
            subject_id=subject_id,
            created_at=created_at,
        )

        # Selection and projection complete before persistence begins.
        # Unsupported facts are ignored by selection; missing or ambiguous
        # eligible revenue facts fail closed before any repository write.
        self._repository.add_evidence(evidence)

        self._repository.add_metric_with_evidence_link(
            projected.metric,
            projected.link,
        )

        return SecFinancialFactIngestionResult(
            evidence=evidence,
            metrics=(projected.metric,),
        )

from __future__ import annotations

from typing import Protocol

from .ingestion import (
    ExternalObservation,
    ingest_external_observation,
)
from .nodes import Evidence
from .repository import EvidenceRepository
from .sec_edgar import SecFilingRequest
from .sec_edgar_discovery import SecFilingSelector


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


class SecEdgarIngestionService:
    """
    Orchestrate one resolved SEC filing into canonical Evidence.

    Resolution and fetch complete before persistence begins. The service
    requires exactly one observation and delegates canonical normalization
    and repository persistence to the existing ingestion contract.
    """

    def __init__(
        self,
        *,
        resolver: SecFilingResolver,
        provider: SecFilingProvider,
        repository: EvidenceRepository,
    ) -> None:
        self._resolver = resolver
        self._provider = provider
        self._repository = repository

    def execute(
        self,
        selector: SecFilingSelector,
    ) -> Evidence:
        request = self._resolver.resolve(selector)
        observations = self._provider.fetch_filing(request)

        if len(observations) != 1:
            raise ValueError(
                "SEC filing fetch must return exactly one observation"
            )

        return ingest_external_observation(
            self._repository,
            observations[0],
        )

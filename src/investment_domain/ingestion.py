from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from .canonical import canonical_sha256
from .identity import canonical_id
from .nodes import Evidence
from .repository import EvidenceRepository
from .validation import validate_node


@dataclass(frozen=True)
class ExternalObservation:
    """
    Provider-independent observation received from an external research source.

    raw_content is the exact normalized textual payload whose hash establishes
    source-content identity. Provider-specific transport concerns do not belong
    in this contract.
    """

    source_id: str
    source_version: str
    raw_content: str
    effective_at: datetime
    observed_at: datetime
    published_at: datetime
    ingested_at: datetime
    source_uri: str | None = None
    supersedes_id: str | None = None


class ExternalResearchProvider(Protocol):
    def fetch(self) -> tuple[ExternalObservation, ...]:
        ...


def normalize_external_observation(
    observation: ExternalObservation,
) -> Evidence:
    if not isinstance(observation.raw_content, str):
        raise TypeError("raw_content must be str")

    if not observation.raw_content:
        raise ValueError("raw_content must not be empty")

    content_hash = canonical_sha256(observation.raw_content)

    identity_payload = {
        "source_id": observation.source_id,
        "source_version": observation.source_version,
        "content_hash": content_hash,
        "effective_at": observation.effective_at,
        "observed_at": observation.observed_at,
        "published_at": observation.published_at,
    }

    evidence = Evidence(
        id=canonical_id("evidence", identity_payload),
        source_id=observation.source_id,
        source_version=observation.source_version,
        content_hash=content_hash,
        effective_at=observation.effective_at,
        observed_at=observation.observed_at,
        published_at=observation.published_at,
        ingested_at=observation.ingested_at,
        supersedes_id=observation.supersedes_id,
        source_uri=observation.source_uri,
    )

    validate_node(evidence)
    return evidence


def ingest_external_observation(
    repository: EvidenceRepository,
    observation: ExternalObservation,
) -> Evidence:
    evidence = normalize_external_observation(observation)
    repository.add_evidence(evidence)
    return evidence

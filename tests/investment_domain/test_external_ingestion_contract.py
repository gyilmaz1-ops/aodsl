from datetime import datetime, timezone

import pytest

from investment_domain.ingestion import (
    ExternalObservation,
    ExternalResearchProvider,
    ingest_external_observation,
    normalize_external_observation,
)


UTC = timezone.utc

T0 = datetime(2026, 9, 1, 8, tzinfo=UTC)
T1 = datetime(2026, 9, 1, 9, tzinfo=UTC)
T2 = datetime(2026, 9, 1, 10, tzinfo=UTC)


def observation(**overrides):
    values = {
        "source_id": "sec:0000320193",
        "source_version": "10-k:2025",
        "raw_content": '{"revenue":"100"}',
        "effective_at": T0,
        "observed_at": T0,
        "published_at": T1,
        "ingested_at": T2,
        "source_uri": "https://example.test/filing",
    }
    values.update(overrides)
    return ExternalObservation(**values)


def test_external_provider_contract_exposes_fetch():
    assert hasattr(ExternalResearchProvider, "fetch")


def test_normalization_is_deterministic():
    left = normalize_external_observation(observation())
    right = normalize_external_observation(observation())

    assert left == right
    assert left.id.startswith("evidence:")
    assert len(left.content_hash) == 64


def test_content_change_changes_evidence_identity():
    left = normalize_external_observation(
        observation(raw_content='{"revenue":"100"}')
    )
    right = normalize_external_observation(
        observation(raw_content='{"revenue":"101"}')
    )

    assert left.content_hash != right.content_hash
    assert left.id != right.id


def test_transport_uri_does_not_change_content_identity():
    left = normalize_external_observation(
        observation(source_uri="https://mirror-a.test/filing")
    )
    right = normalize_external_observation(
        observation(source_uri="https://mirror-b.test/filing")
    )

    assert left.id == right.id
    assert left.content_hash == right.content_hash


def test_empty_raw_content_fails_closed():
    with pytest.raises(ValueError, match="raw_content must not be empty"):
        normalize_external_observation(
            observation(raw_content="")
        )


def test_naive_datetime_fails_closed():
    naive = datetime(2026, 9, 1, 9)

    with pytest.raises(
        TypeError,
        match="naive datetime is forbidden in canonical investment payloads",
    ):
        normalize_external_observation(
            observation(published_at=naive)
        )


class RecordingRepository:
    def __init__(self):
        self.items = []

    def add_evidence(self, evidence):
        self.items.append(evidence)


def test_ingestion_writes_exact_normalized_evidence():
    repo = RecordingRepository()

    result = ingest_external_observation(repo, observation())

    assert repo.items == [result]
    assert result.source_id == "sec:0000320193"
    assert result.source_version == "10-k:2025"

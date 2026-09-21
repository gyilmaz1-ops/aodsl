from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from investment_domain import (
    Evidence,
    Metric,
    active_revision_at,
    available_at,
    canonical_id,
    validate_node,
)
from investment_domain.canonical import canonical_json
from investment_domain.validation import DomainValidationError


UTC = timezone.utc


def evidence(
    *,
    source_version: str,
    content_hash: str,
    effective_at: datetime,
    observed_at: datetime,
    published_at: datetime,
    ingested_at: datetime,
    supersedes_id: str | None = None,
) -> Evidence:
    payload = {
        "source_id": "filing:example",
        "source_version": source_version,
        "content_hash": content_hash,
        "effective_at": effective_at,
        "observed_at": observed_at,
        "published_at": published_at,
    }
    return Evidence(
        id=canonical_id("evidence", payload),
        source_id="filing:example",
        source_version=source_version,
        content_hash=content_hash,
        effective_at=effective_at,
        observed_at=observed_at,
        published_at=published_at,
        ingested_at=ingested_at,
        supersedes_id=supersedes_id,
    )


def test_equivalent_instants_have_identical_canonical_form():
    utc = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    tr = datetime(
        2026, 9, 22, 15, 0,
        tzinfo=timezone(timedelta(hours=3)),
    )
    assert canonical_json({"t": utc}) == canonical_json({"t": tr})


def test_naive_datetime_forbidden_at_canonical_boundary():
    with pytest.raises(TypeError, match="naive datetime"):
        canonical_json({"t": datetime(2026, 9, 22, 12, 0)})


def test_information_not_available_before_publication():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )
    assert not available_at(
        node,
        datetime(2027, 2, 10, 7, 59, tzinfo=UTC),
    )


def test_information_not_available_before_ingestion():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )
    assert not available_at(
        node,
        datetime(2027, 2, 10, 8, 3, 59, tzinfo=UTC),
    )
    assert available_at(
        node,
        datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )


def test_effective_time_does_not_create_lookahead_visibility():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )
    assert not available_at(
        node,
        datetime(2027, 1, 15, tzinfo=UTC),
    )


def test_temporal_order_violation_rejected():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 11, tzinfo=UTC),
    )
    with pytest.raises(DomainValidationError, match="IDM-C004"):
        validate_node(node)


def test_historical_replay_uses_revision_available_at_cutoff():
    original = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )
    revised = evidence(
        source_version="2",
        content_hash="b" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 3, 15, 7, tzinfo=UTC),
        published_at=datetime(2027, 3, 15, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 3, 15, 8, 2, tzinfo=UTC),
        supersedes_id=original.id,
    )

    assert active_revision_at(
        [original, revised],
        datetime(2027, 2, 20, tzinfo=UTC),
    ) == original

    assert active_revision_at(
        [original, revised],
        datetime(2027, 4, 1, tzinfo=UTC),
    ) == revised


def test_naive_research_cutoff_rejected():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        available_at(node, datetime(2027, 2, 20))


def test_metric_revision_identity_includes_temporal_provenance():
    base = {
        "subject_id": "company:x",
        "name": "revenue",
        "period_start": datetime(2026, 1, 1, tzinfo=UTC),
        "period_end": datetime(2026, 12, 31, tzinfo=UTC),
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": datetime(2027, 2, 10, 8, tzinfo=UTC),
        "source_id": "filing:x",
        "source_version": "1",
    }
    later = dict(base)
    later["published_at"] = datetime(2027, 2, 10, 9, tzinfo=UTC)

    assert canonical_id("metric", base) != canonical_id("metric", later)


def test_mixed_evidence_lineages_rejected():
    first = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )

    payload = {
        "source_id": "filing:other",
        "source_version": "1",
        "content_hash": "b" * 64,
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": datetime(2027, 2, 10, 8, tzinfo=UTC),
    }
    second = Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=datetime(2027, 2, 10, 8, 5, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="one logical source lineage"):
        active_revision_at(
            [first, second],
            datetime(2027, 4, 1, tzinfo=UTC),
        )


def test_missing_superseded_revision_rejected():
    predecessor = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )

    revised = evidence(
        source_version="2",
        content_hash="b" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 3, 15, 7, tzinfo=UTC),
        published_at=datetime(2027, 3, 15, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 3, 15, 8, 2, tzinfo=UTC),
        supersedes_id=predecessor.id,
    )

    with pytest.raises(ValueError, match="outside supplied lineage"):
        active_revision_at(
            [revised],
            datetime(2027, 4, 1, tzinfo=UTC),
        )


def test_duplicate_revision_identity_rejected():
    node = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 2, 10, 8, 4, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="duplicate revision identities"):
        active_revision_at(
            [node, node],
            datetime(2027, 4, 1, tzinfo=UTC),
        )


def test_superseding_revision_must_be_ingested_later():
    original = evidence(
        source_version="1",
        content_hash="a" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 2, 10, 7, tzinfo=UTC),
        published_at=datetime(2027, 2, 10, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 3, 20, tzinfo=UTC),
    )

    revised = evidence(
        source_version="2",
        content_hash="b" * 64,
        effective_at=datetime(2026, 12, 31, tzinfo=UTC),
        observed_at=datetime(2027, 3, 15, 7, tzinfo=UTC),
        published_at=datetime(2027, 3, 15, 8, tzinfo=UTC),
        ingested_at=datetime(2027, 3, 16, tzinfo=UTC),
        supersedes_id=original.id,
    )

    with pytest.raises(ValueError, match="ingested after"):
        active_revision_at(
            [original, revised],
            datetime(2027, 4, 1, tzinfo=UTC),
        )

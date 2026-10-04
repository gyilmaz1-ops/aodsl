from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from investment_domain import (
    Evidence,
    PostgreSQLEvidenceRepository,
    canonical_id,
)
from investment_domain.postgres_migrations import PostgreSQLMigrationManager


UTC = timezone.utc
DSN = os.environ.get("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN is not configured",
)


def connect():
    import psycopg

    return psycopg.connect(DSN)


@pytest.fixture(autouse=True)
def clean_database():
    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM valuation_facts")
        con.execute("DELETE FROM forecast_facts")
        con.execute("DELETE FROM estimate_facts")
        con.execute("DELETE FROM calculation_facts")
        con.execute("DELETE FROM metric_facts")
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()

    yield

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM valuation_facts")
        con.execute("DELETE FROM forecast_facts")
        con.execute("DELETE FROM estimate_facts")
        con.execute("DELETE FROM calculation_facts")
        con.execute("DELETE FROM metric_facts")
        con.execute("DELETE FROM evidence_facts")
        con.execute("DELETE FROM claim_facts")
        con.execute("DELETE FROM domain_nodes")
        con.commit()


def make_evidence(
    seed="direct-read",
    *,
    published_at=None,
    ingested_at=None,
):
    effective_at = datetime(2026, 6, 30, tzinfo=UTC)
    observed_at = datetime(2026, 9, 1, 8, tzinfo=UTC)
    published_at = published_at or datetime(
        2026, 9, 1, 9, tzinfo=UTC
    )
    ingested_at = ingested_at or datetime(
        2026, 9, 1, 10, tzinfo=UTC
    )

    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": f"{sum(seed.encode()):064x}"[-64:],
        "effective_at": effective_at,
        "observed_at": observed_at,
        "published_at": published_at,
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
        supersedes_id=None,
        source_uri=f"https://example.test/{seed}",
    )


def test_evidence_at_rejects_noncanonical_id():
    repo = PostgreSQLEvidenceRepository(DSN)

    with pytest.raises(ValueError):
        repo.evidence_at(
            "not-a-canonical-evidence-id",
            datetime(2026, 9, 2, tzinfo=UTC),
        )


def test_evidence_at_rejects_naive_cutoff():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence()

    with pytest.raises(ValueError):
        repo.evidence_at(
            evidence.id,
            datetime(2026, 9, 2),
        )


def test_evidence_at_missing_anchor_returns_none():
    repo = PostgreSQLEvidenceRepository(DSN)

    missing_payload = {
        "source_id": "source:missing",
        "source_version": "1",
        "content_hash": "0" * 64,
        "effective_at": datetime(2026, 6, 30, tzinfo=UTC),
        "observed_at": datetime(2026, 9, 1, 8, tzinfo=UTC),
        "published_at": datetime(2026, 9, 1, 9, tzinfo=UTC),
    }
    missing_id = canonical_id("evidence", missing_payload)

    assert repo.evidence_at(
        missing_id,
        datetime(2026, 9, 2, tzinfo=UTC),
    ) is None


def test_evidence_at_round_trip_and_visibility():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence()
    repo.add_evidence(evidence)

    assert repo.evidence_at(
        evidence.id,
        datetime(2026, 9, 1, 9, 30, tzinfo=UTC),
    ) is None

    assert repo.evidence_at(
        evidence.id,
        datetime(2026, 9, 1, 10, tzinfo=UTC),
    ) == evidence

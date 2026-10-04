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
    effective_at=None,
    observed_at=None,
    published_at=None,
    ingested_at=None,
    supersedes_id=None,
):
    effective_at = effective_at or datetime(
        2026, 6, 30, tzinfo=UTC
    )
    observed_at = observed_at or datetime(
        2026, 9, 1, 8, tzinfo=UTC
    )
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
        supersedes_id=supersedes_id,
        source_uri=f"https://example.test/{seed}",
    )

def test_latest_evidence_at_rejects_noncanonical_anchor():
    repo = PostgreSQLEvidenceRepository(DSN)
    with pytest.raises(ValueError):
        repo.latest_evidence_at(
            "not-an-evidence-id",
            datetime(2026, 9, 1, 12, tzinfo=UTC),
        )


def test_latest_evidence_at_rejects_naive_cutoff():
    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence("latest-naive")
    repo.add_evidence(evidence)

    with pytest.raises(ValueError):
        repo.latest_evidence_at(
            evidence.id,
            datetime(2026, 9, 1, 12),
        )


def test_latest_evidence_at_missing_anchor_returns_none():
    repo = PostgreSQLEvidenceRepository(DSN)
    missing_id = canonical_id(
        "evidence",
        {
            "source_id": "missing-source",
            "source_version": "1",
            "content_hash": "a" * 64,
            "effective_at": datetime(
                2026, 6, 30, tzinfo=UTC
            ),
            "observed_at": datetime(
                2026, 9, 1, 8, tzinfo=UTC
            ),
            "published_at": datetime(
                2026, 9, 1, 9, tzinfo=UTC
            ),
        },
    )

    assert repo.latest_evidence_at(
        missing_id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    ) is None


def test_latest_evidence_at_returns_root_before_successor_visible():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("chain-root")

    successor = make_evidence(
        "chain-successor",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(successor)

    result = repo.latest_evidence_at(
        root.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == root


def test_latest_evidence_at_returns_successor_after_visible():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("visible-root")

    successor = make_evidence(
        "visible-successor",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(successor)

    result = repo.latest_evidence_at(
        root.id,
        datetime(2026, 9, 2, 12, tzinfo=UTC),
    )

    assert result == successor


def test_latest_evidence_at_accepts_successor_as_anchor():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("anchor-root")

    successor = make_evidence(
        "anchor-successor",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(successor)

    result = repo.latest_evidence_at(
        successor.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == root


def test_latest_evidence_at_traverses_multiple_revisions():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("multi-root")

    second = make_evidence(
        "multi-second",
        observed_at=datetime(2026, 9, 2, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    third = make_evidence(
        "multi-third",
        observed_at=datetime(2026, 9, 3, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 3, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 3, 10, tzinfo=UTC),
        supersedes_id=second.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(second)
    repo.add_evidence(third)

    assert repo.latest_evidence_at(
        third.id,
        datetime(2026, 9, 2, 12, tzinfo=UTC),
    ) == second

    assert repo.latest_evidence_at(
        root.id,
        datetime(2026, 9, 3, 12, tzinfo=UTC),
    ) == third


def test_latest_evidence_visibility_ignores_effective_at():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence(
        "future-effective",
        effective_at=datetime(2027, 1, 1, tzinfo=UTC),
        observed_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
        published_at=datetime(2026, 9, 1, 9, tzinfo=UTC),
        ingested_at=datetime(2026, 9, 1, 10, tzinfo=UTC),
    )

    repo.add_evidence(root)

    result = repo.latest_evidence_at(
        root.id,
        datetime(2026, 9, 1, 12, tzinfo=UTC),
    )

    assert result == root

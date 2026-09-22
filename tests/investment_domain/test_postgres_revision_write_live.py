from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from investment_domain import (
    Evidence,
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
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


def reset_database():
    with connect() as con:
        with con.transaction():
            con.execute("DROP TABLE IF EXISTS domain_edges CASCADE")
            con.execute("DROP TABLE IF EXISTS claim_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS evidence_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS domain_nodes CASCADE")
            con.execute(
                "DROP TABLE IF EXISTS "
                "investment_domain_schema_migrations CASCADE"
            )

    PostgreSQLMigrationManager(DSN).migrate()


@pytest.fixture(autouse=True)
def clean_database():
    reset_database()


def make_evidence(
    seed: str,
    *,
    ingested_at: datetime,
    supersedes_id: str | None = None,
) -> Evidence:
    published_at = datetime(2027, 2, 10, 8, tzinfo=UTC)

    payload = {
        "source_id": f"filing:{seed}",
        "source_version": seed,
        "content_hash": (seed.encode().hex() + "0" * 64)[:64],
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": published_at,
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=ingested_at,
        supersedes_id=supersedes_id,
        source_uri=f"https://example.test/{seed}",
    )


def persisted_revision_count(predecessor_id: str) -> int:
    with connect() as con:
        return con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE supersedes_id = %s
            """,
            (predecessor_id,),
        ).fetchone()[0]


def test_revision_missing_predecessor_fails_with_w512():
    repo = PostgreSQLEvidenceRepository(DSN)

    revision = make_evidence(
        "missing-predecessor",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
        supersedes_id="evidence:" + "f" * 64,
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W512: REVISION_PREDECESSOR_NOT_FOUND",
    ):
        repo.add_evidence(revision)

    with connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (revision.id,),
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    "delta",
    [
        timedelta(0),
        timedelta(seconds=-1),
    ],
)
def test_revision_ingestion_must_be_strictly_later(delta):
    repo = PostgreSQLEvidenceRepository(DSN)

    root_ingested = datetime(2027, 2, 10, 9, tzinfo=UTC)
    root = make_evidence(
        "root-monotonic",
        ingested_at=root_ingested,
    )
    repo.add_evidence(root)

    revision = make_evidence(
        f"revision-{delta.total_seconds()}",
        ingested_at=root_ingested + delta,
        supersedes_id=root.id,
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W514: NON_MONOTONIC_REVISION_INGESTION",
    ):
        repo.add_evidence(revision)

    assert persisted_revision_count(root.id) == 0


def test_second_distinct_successor_fails_with_w515():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence(
        "root-branch",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
    )
    first = make_evidence(
        "branch-first",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )
    second = make_evidence(
        "branch-second",
        ingested_at=datetime(2027, 2, 10, 11, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(first)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W515: REVISION_BRANCH_FORBIDDEN",
    ):
        repo.add_evidence(second)

    assert persisted_revision_count(root.id) == 1

    with connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (second.id,),
        ).fetchone()[0] == 0


def test_concurrent_distinct_successors_have_one_domain_winner():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence(
        "root-concurrent",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
    )
    repo.add_evidence(root)

    first = make_evidence(
        "concurrent-first",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )
    second = make_evidence(
        "concurrent-second",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    def write(node):
        try:
            PostgreSQLEvidenceRepository(DSN).add_evidence(node)
            return ("success", node.id)
        except RepositoryWriteError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write, first),
            pool.submit(write, second),
        ]
        results = [future.result() for future in futures]

    successes = [
        value
        for status, value in results
        if status == "success"
    ]
    errors = [
        value
        for status, value in results
        if status == "error"
    ]

    assert len(successes) == 1
    assert len(errors) == 1
    assert "IDM-W515: REVISION_BRANCH_FORBIDDEN" in errors[0]
    assert persisted_revision_count(root.id) == 1


def test_concurrent_identical_revision_replay_remains_idempotent():
    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence(
        "root-concurrent-replay",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
    )
    revision = make_evidence(
        "revision-concurrent-replay",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )
    repo.add_evidence(root)

    def write():
        try:
            PostgreSQLEvidenceRepository(DSN).add_evidence(revision)
            return ("success", revision.id)
        except RepositoryWriteError as exc:
            return ("error", str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(write),
            pool.submit(write),
        ]
        results = [future.result() for future in futures]

    assert results.count(("success", revision.id)) == 2

    assert persisted_revision_count(root.id) == 1

    with connect() as con:
        assert con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (revision.id,),
        ).fetchone()[0] == 1

        assert con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (revision.id,),
        ).fetchone()[0] == 1


def test_exact_revision_replay_remains_idempotent():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence(
        "root-replay",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
    )
    revision = make_evidence(
        "revision-replay",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(revision)
    repo.add_evidence(revision)

    assert persisted_revision_count(root.id) == 1


def test_valid_multistep_revision_chain_persists():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence(
        "chain-root",
        ingested_at=datetime(2027, 2, 10, 9, tzinfo=UTC),
    )
    first = make_evidence(
        "chain-first",
        ingested_at=datetime(2027, 2, 10, 10, tzinfo=UTC),
        supersedes_id=root.id,
    )
    second = make_evidence(
        "chain-second",
        ingested_at=datetime(2027, 2, 10, 11, tzinfo=UTC),
        supersedes_id=first.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(first)
    repo.add_evidence(second)

    assert persisted_revision_count(root.id) == 1
    assert persisted_revision_count(first.id) == 1

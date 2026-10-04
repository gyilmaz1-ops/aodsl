from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from investment_domain import (
    Evidence,
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
    canonical_id,
)
from investment_domain.postgres_migrations import (
    PostgreSQLMigrationManager,
)


DSN = os.environ["IDM_TEST_POSTGRES_DSN"]

INDEX_NAME = "idx_evidence_supersedes"
INDEX_DDL = """
CREATE UNIQUE INDEX idx_evidence_supersedes
ON evidence_facts (supersedes_id)
WHERE supersedes_id IS NOT NULL
"""


def connect():
    return psycopg.connect(DSN)


@pytest.fixture(autouse=True)
def clean_database():
    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with connect() as con:
        con.execute("DELETE FROM evidence_facts")
        con.execute(
            """
            DELETE FROM domain_nodes
            WHERE node_type = 'Evidence'
            """
        )
        con.commit()

    yield

    with connect() as con:
        con.execute("DELETE FROM evidence_facts")
        con.execute(
            """
            DELETE FROM domain_nodes
            WHERE node_type = 'Evidence'
            """
        )
        con.commit()


def make_evidence(
    seed: str,
    *,
    published_at: datetime,
    ingested_at: datetime,
    supersedes_id: str | None = None,
) -> Evidence:
    observed_at = published_at - timedelta(hours=1)

    payload = {
        "source_id": f"source:{seed}",
        "source_version": "1",
        "content_hash": f"{sum(seed.encode()):064x}"[-64:],
        "effective_at": datetime(
            2026, 6, 30, tzinfo=UTC
        ),
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


def index_definition() -> str | None:
    with connect() as con:
        row = con.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'evidence_facts'
              AND indexname = %s
            """,
            (INDEX_NAME,),
        ).fetchone()

    return None if row is None else row[0]


def restore_unique_successor_index() -> None:
    with connect() as con:
        con.execute(
            f"DROP INDEX IF EXISTS {INDEX_NAME}"
        )
        con.execute(INDEX_DDL)
        con.commit()


def test_latest_evidence_at_fails_closed_on_branch_ambiguity():
    repo = PostgreSQLEvidenceRepository(DSN)

    root = make_evidence(
        "r592-root",
        published_at=datetime(
            2026, 9, 1, 9, tzinfo=UTC
        ),
        ingested_at=datetime(
            2026, 9, 1, 10, tzinfo=UTC
        ),
    )
    left = make_evidence(
        "r592-left",
        published_at=datetime(
            2026, 9, 2, 9, tzinfo=UTC
        ),
        ingested_at=datetime(
            2026, 9, 2, 10, tzinfo=UTC
        ),
        supersedes_id=root.id,
    )
    right = make_evidence(
        "r592-right",
        published_at=datetime(
            2026, 9, 3, 9, tzinfo=UTC
        ),
        ingested_at=datetime(
            2026, 9, 3, 10, tzinfo=UTC
        ),
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(left)

    original_definition = index_definition()
    assert original_definition is not None
    assert "CREATE UNIQUE INDEX" in original_definition
    assert "supersedes_id" in original_definition

    try:
        with connect() as con:
            con.execute(
                f"DROP INDEX {INDEX_NAME}"
            )
            con.commit()

        assert index_definition() is None

        payload, payload_hash = repo._payload(right)

        with connect() as con:
            con.execute(
                """
                INSERT INTO domain_nodes (
                    id,
                    node_type,
                    canonical_payload,
                    payload_hash
                )
                VALUES (%s, %s, %s, %s)
                """,
                (
                    right.id,
                    right.node_type.value,
                    payload,
                    payload_hash,
                ),
            )

            con.execute(
                """
                INSERT INTO evidence_facts (
                    node_id,
                    node_type,
                    source_id,
                    source_version,
                    content_hash,
                    effective_at,
                    observed_at,
                    published_at,
                    ingested_at,
                    supersedes_id,
                    source_uri
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    right.id,
                    right.node_type.value,
                    right.source_id,
                    right.source_version,
                    right.content_hash,
                    right.effective_at,
                    right.observed_at,
                    right.published_at,
                    right.ingested_at,
                    right.supersedes_id,
                    right.source_uri,
                ),
            )
            con.commit()

        cutoff = datetime(
            2026, 9, 4, 12, tzinfo=UTC
        )

        with pytest.raises(
            RepositoryReadError,
            match=r"IDM-R592: EVIDENCE_PIT_AMBIGUITY",
        ):
            repo.latest_evidence_at(
                root.id,
                cutoff,
            )
    finally:
        with connect() as con:
            con.execute(
                "DELETE FROM evidence_facts WHERE node_id = %s",
                (right.id,),
            )
            con.execute(
                "DELETE FROM domain_nodes WHERE id = %s",
                (right.id,),
            )
            con.commit()

        restore_unique_successor_index()

    restored = index_definition()
    assert restored is not None
    assert "CREATE UNIQUE INDEX" in restored
    assert "supersedes_id" in restored

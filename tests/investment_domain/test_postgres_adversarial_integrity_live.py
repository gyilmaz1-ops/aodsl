from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from investment_domain import (
    Claim,
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


def reset_database():
    with connect() as con:
        with con.transaction():
            con.execute("DROP TABLE IF EXISTS domain_edges CASCADE")
            con.execute("DROP TABLE IF EXISTS metric_facts CASCADE")
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
    ingested_hour: int = 10,
    supersedes_id: str | None = None,
) -> Evidence:
    payload = {
        "source_id": f"filing:{seed}",
        "source_version": seed,
        "content_hash": (seed.encode().hex() + "0" * 64)[:64],
        "effective_at": datetime(2026, 12, 31, tzinfo=UTC),
        "observed_at": datetime(2027, 2, 10, 7, tzinfo=UTC),
        "published_at": datetime(2027, 2, 10, 8, tzinfo=UTC),
    }

    return Evidence(
        id=canonical_id("evidence", payload),
        **payload,
        ingested_at=datetime(
            2027, 2, 10, ingested_hour, tzinfo=UTC
        ),
        supersedes_id=supersedes_id,
        source_uri=f"https://example.test/{seed}",
    )


def make_claim(seed: str) -> Claim:
    payload = {
        "subject_id": f"company:{seed}",
        "predicate": "revenue_growth",
        "object_value": "positive",
        "object_ref": None,
        "polarity": "POSITIVE",
        "scope": "company",
        "as_of": datetime(2027, 2, 10, tzinfo=UTC),
    }

    return Claim(
        id=canonical_id("claim", payload),
        **payload,
        created_by="Fundamental_Analyst",
    )


def test_predecessor_with_successor_cannot_be_deleted():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("delete-root", ingested_hour=9)
    child = make_evidence(
        "delete-child",
        ingested_hour=10,
        supersedes_id=root.id,
    )

    repo.add_evidence(root)
    repo.add_evidence(child)

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with connect() as con:
            con.execute(
                "DELETE FROM evidence_facts WHERE node_id = %s",
                (root.id,),
            )

    with connect() as con:
        assert con.execute(
            "SELECT COUNT(*) FROM evidence_facts WHERE node_id = %s",
            (root.id,),
        ).fetchone()[0] == 1


def test_direct_sql_second_successor_rejected():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    root = make_evidence("sql-branch-root", ingested_hour=9)
    first = make_evidence(
        "sql-branch-first",
        ingested_hour=10,
        supersedes_id=root.id,
    )
    second = make_evidence(
        "sql-branch-second",
        ingested_hour=11,
    )

    repo.add_evidence(root)
    repo.add_evidence(first)
    repo.add_evidence(second)

    with pytest.raises(psycopg.errors.UniqueViolation):
        with connect() as con:
            con.execute(
                """
                UPDATE evidence_facts
                SET supersedes_id = %s
                WHERE node_id = %s
                """,
                (root.id, second.id),
            )

    with connect() as con:
        assert con.execute(
            """
            SELECT supersedes_id
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (second.id,),
        ).fetchone()[0] is None


def test_evidence_projection_cannot_reference_claim_node():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("typed-evidence")
    repo.add_claim(claim)

    with pytest.raises(
        (
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.CheckViolation,
        )
    ):
        with connect() as con:
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
                    ingested_at
                )
                VALUES (
                    %s, 'Evidence', 'filing:test', 'v1', %s,
                    %s, %s, %s, %s
                )
                """,
                (
                    claim.id,
                    "a" * 64,
                    datetime(2026, 12, 31, tzinfo=UTC),
                    datetime(2027, 2, 10, 7, tzinfo=UTC),
                    datetime(2027, 2, 10, 8, tzinfo=UTC),
                    datetime(2027, 2, 10, 9, tzinfo=UTC),
                ),
            )


def test_claim_projection_cannot_reference_evidence_node():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence("typed-claim")
    repo.add_evidence(evidence)

    with pytest.raises(
        (
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.CheckViolation,
        )
    ):
        with connect() as con:
            con.execute(
                """
                INSERT INTO claim_facts (
                    node_id,
                    node_type,
                    subject_id,
                    predicate,
                    as_of,
                    polarity,
                    scope
                )
                VALUES (
                    %s, 'Claim', 'company:test',
                    'revenue_growth', %s, 'POSITIVE', 'company'
                )
                """,
                (
                    evidence.id,
                    datetime(2027, 2, 10, tzinfo=UTC),
                ),
            )


def test_direct_sql_reversed_claim_evidence_edge_rejected():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    claim = make_claim("reversed-edge")
    evidence = make_evidence("reversed-edge")

    repo.add_claim(claim)
    repo.add_evidence(evidence)

    with pytest.raises(
        (
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.CheckViolation,
        )
    ):
        with connect() as con:
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s, 'Evidence',
                    'SUPPORTED_BY',
                    %s, 'Claim',
                    %s
                )
                """,
                (
                    evidence.id,
                    claim.id,
                    datetime(2027, 2, 10, 11, tzinfo=UTC),
                ),
            )


def test_direct_sql_self_supersession_rejected():
    import psycopg

    repo = PostgreSQLEvidenceRepository(DSN)
    evidence = make_evidence("self-supersession")
    repo.add_evidence(evidence)

    with pytest.raises(psycopg.errors.CheckViolation):
        with connect() as con:
            con.execute(
                """
                UPDATE evidence_facts
                SET supersedes_id = node_id
                WHERE node_id = %s
                """,
                (evidence.id,),
            )

    with connect() as con:
        assert con.execute(
            """
            SELECT supersedes_id
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (evidence.id,),
        ).fetchone()[0] is None

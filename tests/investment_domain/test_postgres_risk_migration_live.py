import os

import psycopg
import pytest

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    PostgreSQLMigrationManager,
)


DSN = os.environ["IDM_TEST_POSTGRES_DSN"]


@pytest.fixture()
def con():
    connection = psycopg.connect(DSN)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def _reset_schema(con):
    with con.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE")
        cur.execute("CREATE SCHEMA public")
    con.commit()


def _insert_domain_node(
    con,
    *,
    node_id,
    node_type,
    payload="{}",
    payload_hash="0" * 64,
):
    with con.cursor() as cur:
        cur.execute(
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
                node_id,
                node_type,
                payload,
                payload_hash,
            ),
        )
    con.commit()


def test_migration_21_creates_risk_facts_projection(con):
    _reset_schema(con)

    assert PostgreSQLMigrationManager(DSN).migrate() == CURRENT_SCHEMA_VERSION

    with con.cursor() as cur:
        cur.execute(
            """
            SELECT version
            FROM investment_domain_schema_migrations
            ORDER BY version DESC
            LIMIT 1
            """
        )
        version = cur.fetchone()[0]

        cur.execute(
            """
            SELECT column_name, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'risk_facts'
            ORDER BY ordinal_position
            """
        )
        columns = cur.fetchall()

    assert version == CURRENT_SCHEMA_VERSION == 22

    assert columns == [
        ("node_id", "NO"),
        ("node_type", "NO"),
        ("subject_id", "NO"),
        ("description", "NO"),
        ("as_of", "NO"),
    ]


def test_risk_facts_accepts_matching_risk_domain_node(con):
    _reset_schema(con)
    assert PostgreSQLMigrationManager(DSN).migrate() == CURRENT_SCHEMA_VERSION

    _insert_domain_node(
        con,
        node_id="risk:live:ok",
        node_type="Risk",
    )

    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_facts (
                node_id,
                subject_id,
                description,
                as_of
            )
            VALUES (
                %s,
                %s,
                %s,
                TIMESTAMPTZ '2026-09-30 12:00:00+00'
            )
            """,
            (
                "risk:live:ok",
                "security:ABC",
                "Supply-chain disruption",
            ),
        )

        cur.execute(
            """
            SELECT
                node_id,
                node_type,
                subject_id,
                description
            FROM risk_facts
            WHERE node_id = %s
            """,
            ("risk:live:ok",),
        )

        row = cur.fetchone()

    assert row == (
        "risk:live:ok",
        "Risk",
        "security:ABC",
        "Supply-chain disruption",
    )


def test_risk_facts_rejects_non_risk_domain_node(con):
    _reset_schema(con)
    assert PostgreSQLMigrationManager(DSN).migrate() == CURRENT_SCHEMA_VERSION

    _insert_domain_node(
        con,
        node_id="claim:live:not-risk",
        node_type="Claim",
    )

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk_facts (
                    node_id,
                    subject_id,
                    description,
                    as_of
                )
                VALUES (
                    %s,
                    %s,
                    %s,
                    TIMESTAMPTZ '2026-09-30 12:00:00+00'
                )
                """,
                (
                    "claim:live:not-risk",
                    "security:ABC",
                    "Wrong endpoint type",
                ),
            )

    con.rollback()


def test_risk_facts_rejects_explicit_wrong_projection_type(con):
    _reset_schema(con)
    assert PostgreSQLMigrationManager(DSN).migrate() == CURRENT_SCHEMA_VERSION

    _insert_domain_node(
        con,
        node_id="risk:live:wrong-projection-type",
        node_type="Risk",
    )

    with pytest.raises(psycopg.errors.CheckViolation):
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO risk_facts (
                    node_id,
                    node_type,
                    subject_id,
                    description,
                    as_of
                )
                VALUES (
                    %s,
                    'Claim',
                    %s,
                    %s,
                    TIMESTAMPTZ '2026-09-30 12:00:00+00'
                )
                """,
                (
                    "risk:live:wrong-projection-type",
                    "security:ABC",
                    "Wrong projection type",
                ),
            )

    con.rollback()


def test_risk_domain_node_delete_is_restricted(con):
    _reset_schema(con)
    assert PostgreSQLMigrationManager(DSN).migrate() == CURRENT_SCHEMA_VERSION

    _insert_domain_node(
        con,
        node_id="risk:live:restrict",
        node_type="Risk",
    )

    with con.cursor() as cur:
        cur.execute(
            """
            INSERT INTO risk_facts (
                node_id,
                subject_id,
                description,
                as_of
            )
            VALUES (
                %s,
                %s,
                %s,
                TIMESTAMPTZ '2026-09-30 12:00:00+00'
            )
            """,
            (
                "risk:live:restrict",
                "security:ABC",
                "Deletion must be restricted",
            ),
        )
    con.commit()

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with con.cursor() as cur:
            cur.execute(
                """
                DELETE FROM domain_nodes
                WHERE id = %s
                """,
                ("risk:live:restrict",),
            )

    con.rollback()

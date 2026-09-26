from __future__ import annotations

import os

import pytest
from psycopg.errors import CheckViolation

from investment_domain.postgres_migrations import (
    ADD_METRIC_EVIDENCE_EDGES,
    EDGE_CREATED_AT,
    EDGE_ENDPOINT_TYPES,
    INITIAL_SCHEMA,
    METRIC_FACTS,
    MIGRATION_HISTORY_TABLE,
    Migration,
    PostgreSQLMigrationManager,
)


DSN = os.getenv("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN not configured",
)


def connect():
    import psycopg
    return psycopg.connect(DSN)


def reset_database():
    with connect() as con:
        with con.transaction():
            con.execute("DROP TABLE IF EXISTS domain_edges CASCADE")
            con.execute("DROP TABLE IF EXISTS forecast_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS valuation_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS estimate_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS calculation_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS metric_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS claim_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS evidence_facts CASCADE")
            con.execute("DROP TABLE IF EXISTS domain_nodes CASCADE")
            con.execute(
                f"DROP TABLE IF EXISTS {MIGRATION_HISTORY_TABLE} CASCADE"
            )


def _insert_node(con, node_id: str, node_type: str):
    con.execute(
        """
        INSERT INTO domain_nodes
            (id, node_type, canonical_payload, payload_hash)
        VALUES (%s, %s, %s::jsonb, %s)
        """,
        (
            node_id,
            node_type,
            "{}",
            "a" * 64,
        ),
    )


def _constraint_names():
    with connect() as con:
        return {
            row[0]
            for row in con.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'domain_edges'::regclass
                """
            ).fetchall()
        }


def test_live_v4_to_v5_preserves_existing_claim_edges():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    assert manager.migrate(target_version=4) == 4

    with connect() as con:
        with con.transaction():
            _insert_node(con, "claim:legacy", "Claim")
            _insert_node(con, "evidence:support", "Evidence")
            _insert_node(con, "evidence:contradict", "Evidence")

            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    edge_type,
                    target_id,
                    created_at,
                    source_type,
                    target_type
                )
                VALUES
                    (
                        'claim:legacy',
                        'SUPPORTED_BY',
                        'evidence:support',
                        CURRENT_TIMESTAMP,
                        'Claim',
                        'Evidence'
                    ),
                    (
                        'claim:legacy',
                        'CONTRADICTED_BY',
                        'evidence:contradict',
                        CURRENT_TIMESTAMP,
                        'Claim',
                        'Evidence'
                    )
                """
            )

    assert manager.migrate(target_version=5) == 5

    with connect() as con:
        rows = con.execute(
            """
            SELECT
                source_id,
                source_type,
                edge_type,
                target_id,
                target_type
            FROM domain_edges
            ORDER BY edge_type, target_id
            """
        ).fetchall()

    assert rows == [
        (
            "claim:legacy",
            "Claim",
            "CONTRADICTED_BY",
            "evidence:contradict",
            "Evidence",
        ),
        (
            "claim:legacy",
            "Claim",
            "SUPPORTED_BY",
            "evidence:support",
            "Evidence",
        ),
    ]


def test_live_v5_preserves_typed_foreign_keys_and_installs_semantic_check():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    assert manager.migrate(target_version=5) == 5

    constraints = _constraint_names()

    assert "domain_edges_source_typed_fk" in constraints
    assert "domain_edges_target_typed_fk" in constraints
    assert "domain_edges_target_type" in constraints
    assert "domain_edges_source_relation_target_type" in constraints

    # v3's Claim-only source check must have been replaced.
    assert "domain_edges_source_type" not in constraints


def test_live_v5_history_is_recorded_exactly_once():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)

    assert manager.migrate(target_version=4) == 4
    assert manager.migrate(target_version=5) == 5
    assert manager.migrate(target_version=5) == 5

    with connect() as con:
        rows = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            WHERE version = 5
            """
        ).fetchall()

    assert rows == [
        (
            5,
            ADD_METRIC_EVIDENCE_EDGES.name,
            ADD_METRIC_EVIDENCE_EDGES.checksum,
        )
    ]


def test_live_v5_constraint_rejects_metric_contradicted_by():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    assert manager.migrate(target_version=5) == 5

    with connect() as con:
        with con.transaction():
            _insert_node(con, "metric:m1", "Metric")
            _insert_node(con, "evidence:e1", "Evidence")

    with pytest.raises(CheckViolation):
        with connect() as con:
            with con.transaction():
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
                        'metric:m1',
                        'Metric',
                        'CONTRADICTED_BY',
                        'evidence:e1',
                        'Evidence',
                        CURRENT_TIMESTAMP
                    )
                    """
                )


def test_live_v5_multi_statement_failure_rolls_back_atomically():
    reset_database()

    base = PostgreSQLMigrationManager(DSN)
    assert base.migrate(target_version=4) == 4

    failing_v5 = Migration(
        version=5,
        name="test_atomic_v5_failure",
        statements=(
            """
            ALTER TABLE domain_edges
                DROP CONSTRAINT domain_edges_source_type
            """.strip(),
            """
            ALTER TABLE domain_edges
                ADD CONSTRAINT deliberately_invalid_v5_check
                    CHECK (definitely_missing_column = 'x')
            """.strip(),
        ),
    )

    manager = PostgreSQLMigrationManager(
        DSN,
        migrations=(
            INITIAL_SCHEMA,
            EDGE_CREATED_AT,
            EDGE_ENDPOINT_TYPES,
            METRIC_FACTS,
            failing_v5,
        ),
    )

    with pytest.raises(Exception):
        manager.migrate()

    constraints = _constraint_names()

    # Statement 1 completed before statement 2 failed.
    # PostgreSQL transaction rollback must restore statement 1.
    assert "domain_edges_source_type" in constraints
    assert "deliberately_invalid_v5_check" not in constraints

    with connect() as con:
        version_5_count = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {MIGRATION_HISTORY_TABLE}
            WHERE version = 5
            """
        ).fetchone()[0]

    assert version_5_count == 0

from __future__ import annotations

import os

import psycopg
import pytest

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    PostgreSQLMigrationManager,
)


DSN = os.getenv("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN not configured",
)


def _reset_database() -> None:
    assert DSN is not None

    with psycopg.connect(DSN, autocommit=True) as con:
        with con.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS recommendation_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS catalyst_impact_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS catalyst_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS forecast_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS valuation_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS estimate_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS calculation_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS metric_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS domain_edges CASCADE")
            cur.execute("DROP TABLE IF EXISTS claim_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS evidence_facts CASCADE")
            cur.execute("DROP TABLE IF EXISTS domain_nodes CASCADE")

            # Migration manager owns its history table.
            # Drop any remaining user table that records migrations.
            cur.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = current_schema()
                  AND tablename LIKE '%migration%'
                """
            )

            for (table_name,) in cur.fetchall():
                cur.execute(
                    psycopg.sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
                        psycopg.sql.Identifier(table_name)
                    )
                )


def _insert_node(
    cur,
    node_id: str,
    node_type: str,
) -> None:
    cur.execute(
        """
        INSERT INTO domain_nodes (
            id,
            node_type,
            canonical_payload,
            payload_hash
        )
        VALUES (
            %s,
            %s,
            '{}'::jsonb,
            %s
        )
        """,
        (
            node_id,
            node_type,
            "0" * 64,
        ),
    )


def test_live_recommendation_dependency_edge_constraint():
    assert DSN is not None

    _reset_database()

    manager = PostgreSQLMigrationManager(DSN)

    assert manager.migrate() == CURRENT_SCHEMA_VERSION
    assert CURRENT_SCHEMA_VERSION == 20

    legal_targets = (
        ("valuation-1", "Valuation"),
        ("claim-1", "Claim"),
        ("risk-1", "Risk"),
        ("catalyst-1", "Catalyst"),
    )

    with psycopg.connect(DSN) as con:
        with con.cursor() as cur:
            _insert_node(
                cur,
                "recommendation-1",
                "Recommendation",
            )

            for node_id, node_type in legal_targets:
                _insert_node(
                    cur,
                    node_id,
                    node_type,
                )

            _insert_node(
                cur,
                "forecast-illegal",
                "Forecast",
            )

            for node_id, node_type in legal_targets:
                cur.execute(
                    """
                    INSERT INTO domain_edges (
                        source_id,
                        edge_type,
                        target_id,
                        created_at,
                        source_type,
                        target_type
                    )
                    VALUES (
                        %s,
                        'DEPENDS_ON',
                        %s,
                        CURRENT_TIMESTAMP,
                        'Recommendation',
                        %s
                    )
                    """,
                    (
                        "recommendation-1",
                        node_id,
                        node_type,
                    ),
                )

        con.commit()

    with psycopg.connect(DSN) as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT target_id, target_type
                FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Recommendation'
                  AND edge_type = 'DEPENDS_ON'
                ORDER BY target_id
                """,
                ("recommendation-1",),
            )

            rows = cur.fetchall()

    assert rows == sorted(legal_targets)

    with pytest.raises(
        psycopg.errors.CheckViolation
    ):
        with psycopg.connect(DSN) as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO domain_edges (
                        source_id,
                        edge_type,
                        target_id,
                        created_at,
                        source_type,
                        target_type
                    )
                    VALUES (
                        %s,
                        'DEPENDS_ON',
                        %s,
                        CURRENT_TIMESTAMP,
                        'Recommendation',
                        'Forecast'
                    )
                    """,
                    (
                        "recommendation-1",
                        "forecast-illegal",
                    ),
                )

    with psycopg.connect(DSN) as con:
        with con.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*)
                FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Recommendation'
                  AND edge_type = 'DEPENDS_ON'
                """,
                ("recommendation-1",),
            )

            count = cur.fetchone()[0]

    assert count == 4

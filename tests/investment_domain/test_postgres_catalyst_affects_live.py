from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from investment_domain.postgres_migrations import (
    CATALYST_AFFECTS_EDGES,
    MIGRATION_HISTORY_TABLE,
    PostgreSQLMigrationManager,
)


DSN = os.getenv("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN not configured",
)

UTC = timezone.utc


def connect():
    return psycopg.connect(DSN)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    PostgreSQLMigrationManager(DSN).migrate()


@pytest.fixture(autouse=True)
def clean_test_edges_and_nodes():
    with connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id LIKE 'v16:%'
                   OR target_id LIKE 'v16:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v16:%'
                """
            )

    yield

    with connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id LIKE 'v16:%'
                   OR target_id LIKE 'v16:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v16:%'
                """
            )


def _insert_node(con, node_id: str, node_type: str) -> None:
    con.execute(
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
            repeat('0', 64)
        )
        """,
        (node_id, node_type),
    )


def _insert_edge(
    con,
    *,
    source_id: str,
    source_type: str,
    edge_type: str,
    target_id: str,
    target_type: str,
) -> None:
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
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            source_id,
            source_type,
            edge_type,
            target_id,
            target_type,
            datetime(2026, 9, 26, 12, 0, tzinfo=UTC),
        ),
    )


def _assert_relation_matrix_rejects(
    *,
    source_type: str,
    edge_type: str,
    target_type: str,
) -> None:
    source_id = (
        f"v16:source:{source_type.lower()}:"
        f"{edge_type.lower()}:{target_type.lower()}"
    )
    target_id = (
        f"v16:target:{target_type.lower()}:"
        f"{source_type.lower()}:{edge_type.lower()}"
    )

    with connect() as con:
        with con.transaction():
            _insert_node(con, source_id, source_type)
            _insert_node(con, target_id, target_type)

    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        with connect() as con:
            with con.transaction():
                _insert_edge(
                    con,
                    source_id=source_id,
                    source_type=source_type,
                    edge_type=edge_type,
                    target_id=target_id,
                    target_type=target_type,
                )

    error = exc_info.value

    assert error.sqlstate == "23514"
    assert (
        error.diag.constraint_name
        == "domain_edges_source_relation_target_type"
    )


def test_v16_migration_history_is_persisted():
    with connect() as con:
        row = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            WHERE version = 16
            """
        ).fetchone()

    assert row == (
        16,
        CATALYST_AFFECTS_EDGES.name,
        CATALYST_AFFECTS_EDGES.checksum,
    )


def test_v16_final_edge_constraints_exist():
    with connect() as con:
        names = {
            row[0]
            for row in con.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = 'domain_edges'::regclass
                """
            ).fetchall()
        }

    assert "domain_edges_type" in names
    assert "domain_edges_source_relation_target_type" in names


@pytest.mark.parametrize(
    ("target_type", "suffix"),
    [
        ("Claim", "claim"),
        ("Forecast", "forecast"),
    ],
)
def test_database_accepts_catalyst_affects_supported_targets(
    target_type,
    suffix,
):
    source_id = f"v16:catalyst:{suffix}"
    target_id = f"v16:{suffix}:target"

    with connect() as con:
        with con.transaction():
            _insert_node(con, source_id, "Catalyst")
            _insert_node(con, target_id, target_type)

            _insert_edge(
                con,
                source_id=source_id,
                source_type="Catalyst",
                edge_type="AFFECTS",
                target_id=target_id,
                target_type=target_type,
            )

    with connect() as con:
        row = con.execute(
            """
            SELECT
                source_type,
                edge_type,
                target_type
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = 'AFFECTS'
              AND target_id = %s
            """,
            (source_id, target_id),
        ).fetchone()

    assert row == (
        "Catalyst",
        "AFFECTS",
        target_type,
    )


def test_database_rejects_catalyst_affects_valuation():
    _assert_relation_matrix_rejects(
        source_type="Catalyst",
        edge_type="AFFECTS",
        target_type="Valuation",
    )


def test_database_rejects_claim_affects_forecast():
    _assert_relation_matrix_rejects(
        source_type="Claim",
        edge_type="AFFECTS",
        target_type="Forecast",
    )


def test_database_rejects_catalyst_contains_forecast():
    _assert_relation_matrix_rejects(
        source_type="Catalyst",
        edge_type="CONTAINS",
        target_type="Forecast",
    )

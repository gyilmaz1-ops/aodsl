from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from investment_domain.postgres_migrations import (
    RISK_AFFECTS_EDGES,
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
                WHERE source_id LIKE 'v22:%'
                   OR target_id LIKE 'v22:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v22:%'
                """
            )

    yield

    with connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id LIKE 'v22:%'
                   OR target_id LIKE 'v22:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v22:%'
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
        f"v22:source:{source_type.lower()}:"
        f"{edge_type.lower()}:{target_type.lower()}"
    )
    target_id = (
        f"v22:target:{target_type.lower()}:"
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


def test_v22_migration_history_is_persisted():
    with connect() as con:
        row = con.execute(
            f"SELECT name, checksum FROM {MIGRATION_HISTORY_TABLE} WHERE version = %s",
            (22,),
        ).fetchone()

    assert row == (
        RISK_AFFECTS_EDGES.name,
        RISK_AFFECTS_EDGES.checksum,
    )


@pytest.mark.parametrize(
    "target_type",
    ["Claim", "Forecast", "Valuation"],
)
def test_database_accepts_risk_affects_supported_targets(target_type):
    source_id = f"v22:risk:{target_type.lower()}"
    target_id = f"v22:{target_type.lower()}:target"

    with connect() as con:
        with con.transaction():
            _insert_node(con, source_id, "Risk")
            _insert_node(con, target_id, target_type)
            _insert_edge(
                con,
                source_id=source_id,
                source_type="Risk",
                edge_type="AFFECTS",
                target_id=target_id,
                target_type=target_type,
            )

    with connect() as con:
        row = con.execute(
            """
            SELECT source_type, edge_type, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = 'AFFECTS'
              AND target_id = %s
            """,
            (source_id, target_id),
        ).fetchone()

    assert row == ("Risk", "AFFECTS", target_type)


@pytest.mark.parametrize(
    "target_type",
    ["Catalyst", "Risk"],
)
def test_database_rejects_risk_affects_unsupported_targets(target_type):
    _assert_relation_matrix_rejects(
        source_type="Risk",
        edge_type="AFFECTS",
        target_type=target_type,
    )


def test_database_rejects_claim_affects_forecast():
    _assert_relation_matrix_rejects(
        source_type="Claim",
        edge_type="AFFECTS",
        target_type="Forecast",
    )


@pytest.mark.parametrize(
    "target_type",
    ["Claim", "Forecast"],
)
def test_database_preserves_catalyst_affects_supported_targets(target_type):
    source_id = f"v22:catalyst:{target_type.lower()}"
    target_id = f"v22:catalyst-target:{target_type.lower()}"

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


def test_database_preserves_recommendation_depends_on_risk():
    source_id = "v22:recommendation:risk"
    target_id = "v22:risk:recommendation-target"

    with connect() as con:
        with con.transaction():
            _insert_node(con, source_id, "Recommendation")
            _insert_node(con, target_id, "Risk")
            _insert_edge(
                con,
                source_id=source_id,
                source_type="Recommendation",
                edge_type="DEPENDS_ON",
                target_id=target_id,
                target_type="Risk",
            )

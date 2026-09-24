import os

import pytest

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    PostgreSQLMigrationManager,
)


DSN = os.environ["IDM_TEST_DSN"]


@pytest.fixture
def con():
    PostgreSQLMigrationManager(DSN).migrate()

    import psycopg

    with psycopg.connect(DSN) as connection:
        yield connection


def _constraint_names(con):
    rows = con.execute(
        """
        SELECT c.conname
        FROM pg_constraint AS c
        JOIN pg_class AS t
          ON t.oid = c.conrelid
        WHERE t.relname = 'metric_facts'
        ORDER BY c.conname
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def _index_names(con):
    rows = con.execute(
        """
        SELECT indexname
        FROM pg_indexes
        WHERE tablename = 'metric_facts'
        ORDER BY indexname
        """
    ).fetchall()
    return {str(row[0]) for row in rows}


def test_metric_revision_constraints_are_schema_v6(con):
    assert CURRENT_SCHEMA_VERSION == 6

    constraints = _constraint_names(con)
    indexes = _index_names(con)

    assert "metric_supersedes_fk" in constraints
    assert "metric_no_self_supersession" in constraints
    assert "metric_one_successor_per_predecessor" in indexes


def test_metric_supersedes_fk_targets_metric_facts(con):
    row = con.execute(
        """
        SELECT
            source.relname,
            target.relname,
            c.confdeltype
        FROM pg_constraint AS c
        JOIN pg_class AS source
          ON source.oid = c.conrelid
        JOIN pg_class AS target
          ON target.oid = c.confrelid
        WHERE c.conname = 'metric_supersedes_fk'
        """
    ).fetchone()

    assert row is not None
    assert row[0] == "metric_facts"
    assert row[1] == "metric_facts"
    assert row[2] == "r"


def test_metric_no_self_supersession_is_enforced(con):
    definition = con.execute(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint AS c
        JOIN pg_class AS t
          ON t.oid = c.conrelid
        WHERE t.relname = 'metric_facts'
          AND c.conname = 'metric_no_self_supersession'
        """
    ).fetchone()

    assert definition is not None
    assert "supersedes_id" in definition[0]
    assert "node_id" in definition[0]


def test_metric_one_successor_index_is_unique_and_partial(con):
    row = con.execute(
        """
        SELECT indexdef
        FROM pg_indexes
        WHERE tablename = 'metric_facts'
          AND indexname = 'metric_one_successor_per_predecessor'
        """
    ).fetchone()

    assert row is not None

    definition = row[0].upper()

    assert "CREATE UNIQUE INDEX" in definition
    assert "SUPERSEDES_ID" in definition
    assert "WHERE" in definition
    assert "IS NOT NULL" in definition


def _insert_metric_fact(
    con,
    *,
    node_id,
    supersedes_id=None,
):
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
            'Metric',
            '{}'::jsonb,
            '0000000000000000000000000000000000000000000000000000000000000000'
        )
        """,
        (node_id,),
    )

    con.execute(
        """
        INSERT INTO metric_facts (
            node_id,
            node_type,
            subject_id,
            name,
            value,
            unit,
            currency,
            period_start,
            period_end,
            effective_at,
            observed_at,
            published_at,
            ingested_at,
            source_id,
            source_version,
            supersedes_id
        )
        VALUES (
            %s,
            'Metric',
            'company:005e-db',
            'financial.revenue',
            100,
            'currency',
            'USD',
            TIMESTAMPTZ '2026-01-01 00:00:00+00',
            TIMESTAMPTZ '2026-06-30 00:00:00+00',
            TIMESTAMPTZ '2026-06-30 00:00:00+00',
            TIMESTAMPTZ '2026-07-01 00:00:00+00',
            TIMESTAMPTZ '2026-07-02 00:00:00+00',
            TIMESTAMPTZ '2026-07-03 00:00:00+00',
            'issuer:005e-db',
            'v1',
            %s
        )
        """,
        (node_id, supersedes_id),
    )


def test_database_rejects_missing_metric_revision_predecessor(con):
    with pytest.raises(Exception) as exc_info:
        with con.transaction():
            _insert_metric_fact(
                con,
                node_id="metric:005e-db-orphan-child",
                supersedes_id="metric:005e-db-missing-parent",
            )

    assert getattr(exc_info.value, "sqlstate", None) == "23503"
    assert (
        exc_info.value.diag.constraint_name
        == "metric_supersedes_fk"
    )


def test_database_rejects_metric_self_supersession(con):
    with pytest.raises(Exception) as exc_info:
        with con.transaction():
            _insert_metric_fact(
                con,
                node_id="metric:005e-db-self",
                supersedes_id="metric:005e-db-self",
            )

    assert getattr(exc_info.value, "sqlstate", None) == "23514"
    assert (
        exc_info.value.diag.constraint_name
        == "metric_no_self_supersession"
    )


def test_database_rejects_second_metric_revision_successor(con):
    with con.transaction():
        _insert_metric_fact(
            con,
            node_id="metric:005e-db-root",
        )
        _insert_metric_fact(
            con,
            node_id="metric:005e-db-child-1",
            supersedes_id="metric:005e-db-root",
        )

    with pytest.raises(Exception) as exc_info:
        with con.transaction():
            _insert_metric_fact(
                con,
                node_id="metric:005e-db-child-2",
                supersedes_id="metric:005e-db-root",
            )

    assert getattr(exc_info.value, "sqlstate", None) == "23505"
    assert (
        exc_info.value.diag.constraint_name
        == "metric_one_successor_per_predecessor"
    )

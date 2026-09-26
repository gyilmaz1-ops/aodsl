from __future__ import annotations

import os
import threading
import time

import pytest

from investment_domain.postgres_migrations import (
    ADD_METRIC_EVIDENCE_EDGES,
    CALCULATION_FACTS,
    CALCULATION_INPUT_PROVENANCE,
    CURRENT_SCHEMA_VERSION,
    EDGE_CREATED_AT,
    EDGE_ENDPOINT_TYPES,
    ESTIMATE_FACTS,
    ESTIMATE_INPUT_PROVENANCE,
    INITIAL_SCHEMA,
    VALUATION_FACTS,
    VALUATION_DEPENDENCIES,
    MIGRATION_HISTORY_TABLE,
    METRIC_FACTS,
    METRIC_REVISION_CONSTRAINTS,
    Migration,
    MigrationError,
    PostgreSQLMigrationManager,
    advisory_lock_key,
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


def test_live_initial_migration_and_idempotent_restart():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)

    assert manager.migrate() == CURRENT_SCHEMA_VERSION
    assert manager.migrate() == CURRENT_SCHEMA_VERSION

    with connect() as con:
        tables = {
            row[0]
            for row in con.execute(
                """
                SELECT tablename
                FROM pg_tables
                WHERE schemaname = current_schema()
                """
            ).fetchall()
        }

        assert {
            "domain_nodes",
            "evidence_facts",
            "claim_facts",
            "domain_edges",
            "metric_facts",
            "calculation_facts",
            "estimate_facts",
            "valuation_facts",
            MIGRATION_HISTORY_TABLE,
        } <= tables

        rows = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            ORDER BY version
            """
        ).fetchall()

        assert rows == [
            (
                1,
                INITIAL_SCHEMA.name,
                INITIAL_SCHEMA.checksum,
            ),
            (
                2,
                EDGE_CREATED_AT.name,
                EDGE_CREATED_AT.checksum,
            ),
            (
                3,
                EDGE_ENDPOINT_TYPES.name,
                EDGE_ENDPOINT_TYPES.checksum,
            ),
            (
                4,
                METRIC_FACTS.name,
                METRIC_FACTS.checksum,
            ),
            (
                5,
                ADD_METRIC_EVIDENCE_EDGES.name,
                ADD_METRIC_EVIDENCE_EDGES.checksum,
            ),
            (
                6,
                METRIC_REVISION_CONSTRAINTS.name,
                METRIC_REVISION_CONSTRAINTS.checksum,
            ),
            (
                7,
                CALCULATION_FACTS.name,
                CALCULATION_FACTS.checksum,
            ),
            (
                8,
                CALCULATION_INPUT_PROVENANCE.name,
                CALCULATION_INPUT_PROVENANCE.checksum,
            ),
            (
                9,
                ESTIMATE_FACTS.name,
                ESTIMATE_FACTS.checksum,
            ),
            (
                10,
                ESTIMATE_INPUT_PROVENANCE.name,
                ESTIMATE_INPUT_PROVENANCE.checksum,
            ),
            (
                11,
                VALUATION_FACTS.name,
                VALUATION_FACTS.checksum,
            ),
            (
                12,
                VALUATION_DEPENDENCIES.name,
                VALUATION_DEPENDENCIES.checksum,
            ),
        ]


def test_live_checksum_tamper_fails_closed():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with connect() as con:
        with con.transaction():
            con.execute(
                f"""
                UPDATE {MIGRATION_HISTORY_TABLE}
                SET checksum = %s
                WHERE version = 1
                """,
                ("0" * 64,),
            )

    with pytest.raises(
        MigrationError,
        match="IDM-M412: MIGRATION_CHECKSUM_MISMATCH",
    ):
        manager.migrate()


def test_live_newer_schema_fails_closed():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with connect() as con:
        with con.transaction():
            con.execute(
                f"""
                INSERT INTO {MIGRATION_HISTORY_TABLE}
                    (version, name, checksum)
                VALUES (%s, %s, %s)
                """,
                (CURRENT_SCHEMA_VERSION + 1, "future_schema", "f" * 64),
            )

    with pytest.raises(
        MigrationError,
        match="IDM-M410: DATABASE_SCHEMA_NEWER_THAN_RUNTIME",
    ):
        manager.migrate()


def test_live_downgrade_fails_closed():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)
    manager.migrate()

    with pytest.raises(
        MigrationError,
        match="IDM-M413: DOWNGRADE_NOT_SUPPORTED",
    ):
        manager.migrate(target_version=0)


def test_live_advisory_lock_timeout_fails_closed():
    reset_database()

    blocker_ready = threading.Event()
    release_blocker = threading.Event()
    blocker_error = []

    def blocker():
        try:
            with connect() as con:
                with con.transaction():
                    con.execute(
                        "SELECT pg_advisory_xact_lock(%s)",
                        (advisory_lock_key(),),
                    )
                    blocker_ready.set()
                    release_blocker.wait(timeout=10)
        except Exception as exc:
            blocker_error.append(exc)
            blocker_ready.set()

    thread = threading.Thread(target=blocker)
    thread.start()

    assert blocker_ready.wait(timeout=5)
    assert not blocker_error

    manager = PostgreSQLMigrationManager(
        DSN,
        lock_timeout_ms=250,
    )

    started = time.monotonic()

    try:
        with pytest.raises(Exception) as exc_info:
            manager.migrate()

        elapsed = time.monotonic() - started

        assert elapsed < 3
        assert "lock timeout" in str(exc_info.value).lower()
    finally:
        release_blocker.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert not blocker_error


def test_live_concurrent_startup_serializes_migration():
    reset_database()

    barrier = threading.Barrier(2)
    results = []
    errors = []

    def runner():
        try:
            manager = PostgreSQLMigrationManager(
                DSN,
                lock_timeout_ms=5000,
            )
            barrier.wait(timeout=5)
            results.append(manager.migrate())
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=runner)
    second = threading.Thread(target=runner)

    first.start()
    second.start()

    first.join(timeout=10)
    second.join(timeout=10)

    assert not first.is_alive()
    assert not second.is_alive()
    assert not errors
    assert sorted(results) == [
        CURRENT_SCHEMA_VERSION,
        CURRENT_SCHEMA_VERSION,
    ]

    with connect() as con:
        count = con.execute(
            f"""
            SELECT COUNT(*)
            FROM {MIGRATION_HISTORY_TABLE}
            """
        ).fetchone()[0]

        assert count == len(PostgreSQLMigrationManager(DSN).migrations)


def _insert_domain_node(con, node_id, node_type):
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


def test_live_projection_node_type_integrity():
    reset_database()
    PostgreSQLMigrationManager(DSN).migrate()

    with connect() as con:
        _insert_domain_node(con, "claim:test", "Claim")

    with pytest.raises(Exception):
        with connect() as con:
            con.execute(
                """
                INSERT INTO evidence_facts (
                    node_id,
                    source_id,
                    source_version,
                    content_hash,
                    effective_at,
                    observed_at,
                    published_at,
                    ingested_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP
                )
                """,
                (
                    "claim:test",
                    "source",
                    "v1",
                    "b" * 64,
                ),
            )


def test_live_revision_branching_rejected():
    reset_database()
    PostgreSQLMigrationManager(DSN).migrate()

    with connect() as con:
        for node_id in (
            "evidence:root",
            "evidence:child1",
            "evidence:child2",
        ):
            _insert_domain_node(con, node_id, "Evidence")

        for node_id, supersedes_id in (
            ("evidence:root", None),
            ("evidence:child1", "evidence:root"),
        ):
            con.execute(
                """
                INSERT INTO evidence_facts (
                    node_id,
                    source_id,
                    source_version,
                    content_hash,
                    effective_at,
                    observed_at,
                    published_at,
                    ingested_at,
                    supersedes_id
                )
                VALUES (
                    %s, %s, %s, %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    %s
                )
                """,
                (
                    node_id,
                    "source",
                    node_id,
                    "b" * 64,
                    supersedes_id,
                ),
            )

    with pytest.raises(Exception):
        with connect() as con:
            con.execute(
                """
                INSERT INTO evidence_facts (
                    node_id,
                    source_id,
                    source_version,
                    content_hash,
                    effective_at,
                    observed_at,
                    published_at,
                    ingested_at,
                    supersedes_id
                )
                VALUES (
                    %s, %s, %s, %s,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    CURRENT_TIMESTAMP,
                    %s
                )
                """,
                (
                    "evidence:child2",
                    "source",
                    "child2",
                    "c" * 64,
                    "evidence:root",
                ),
            )


def test_live_v1_to_v2_upgrade_backfills_edge_created_at():
    reset_database()

    manager = PostgreSQLMigrationManager(DSN)

    # Build a genuine v1 database first.
    assert manager.migrate(target_version=1) == 1

    with connect() as con:
        _insert_domain_node(con, "claim:legacy", "Claim")
        _insert_domain_node(con, "evidence:legacy", "Evidence")

        con.execute(
            """
            INSERT INTO domain_edges (
                source_id,
                edge_type,
                target_id
            )
            VALUES (%s, %s, %s)
            """,
            (
                "claim:legacy",
                "SUPPORTED_BY",
                "evidence:legacy",
            ),
        )

        legacy_stored_at = con.execute(
            """
            SELECT stored_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                "claim:legacy",
                "SUPPORTED_BY",
                "evidence:legacy",
            ),
        ).fetchone()[0]

    # Upgrade the real v1 database using migration v2.
    assert manager.migrate(target_version=2) == 2

    with connect() as con:
        row = con.execute(
            """
            SELECT stored_at, created_at
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                "claim:legacy",
                "SUPPORTED_BY",
                "evidence:legacy",
            ),
        ).fetchone()

        assert row is not None
        assert row[0] == legacy_stored_at
        assert row[1] == legacy_stored_at

        nullable = con.execute(
            """
            SELECT is_nullable
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'domain_edges'
              AND column_name = 'created_at'
            """
        ).fetchone()

        assert nullable == ("NO",)

        history = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            ORDER BY version
            """
        ).fetchall()

        assert history == [
            (
                1,
                INITIAL_SCHEMA.name,
                INITIAL_SCHEMA.checksum,
            ),
            (
                2,
                EDGE_CREATED_AT.name,
                EDGE_CREATED_AT.checksum,
            ),
        ]

    # Restart at v2 must remain idempotent.
    assert manager.migrate(target_version=2) == 2


def test_live_failed_migration_rolls_back_ddl_and_history():
    reset_database()

    failing = Migration(
        version=1,
        name="transactional_ddl_failure_probe",
        statements=(
            """
            CREATE TABLE idm_transaction_rollback_probe (
                id INTEGER PRIMARY KEY
            )
            """.strip(),
            """
            THIS IS INTENTIONALLY INVALID SQL
            """.strip(),
        ),
    )

    manager = PostgreSQLMigrationManager(
        DSN,
        migrations=(failing,),
    )

    with pytest.raises(Exception):
        manager.migrate()

    with connect() as con:
        probe_exists = con.execute(
            """
            SELECT to_regclass(
                current_schema() || '.idm_transaction_rollback_probe'
            )
            """
        ).fetchone()[0]

        assert probe_exists is None

        history_exists = con.execute(
            """
            SELECT to_regclass(
                current_schema() || %s
            )
            """,
            (f".{MIGRATION_HISTORY_TABLE}",),
        ).fetchone()[0]

        if history_exists is not None:
            history_count = con.execute(
                f"""
                SELECT COUNT(*)
                FROM {MIGRATION_HISTORY_TABLE}
                """
            ).fetchone()[0]

            assert history_count == 0


def test_live_v2_to_v3_upgrade_backfills_edge_endpoint_types():
    reset_database()
    manager = PostgreSQLMigrationManager(DSN)

    # Build a genuine v2 database.
    assert manager.migrate(target_version=2) == 2

    with connect() as con:
        _insert_domain_node(con, "claim:legacy-v2", "Claim")
        _insert_domain_node(con, "evidence:legacy-v2", "Evidence")

        con.execute(
            """
            INSERT INTO domain_edges (
                source_id,
                edge_type,
                target_id,
                created_at
            )
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                "claim:legacy-v2",
                "SUPPORTED_BY",
                "evidence:legacy-v2",
            ),
        )

    # Upgrade the real v2 database to v3.
    assert manager.migrate(target_version=3) == 3

    with connect() as con:
        row = con.execute(
            """
            SELECT source_type, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                "claim:legacy-v2",
                "SUPPORTED_BY",
                "evidence:legacy-v2",
            ),
        ).fetchone()

        assert row == ("Claim", "Evidence")

        columns = dict(
            con.execute(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'domain_edges'
                  AND column_name IN ('source_type', 'target_type')
                ORDER BY column_name
                """
            ).fetchall()
        )

        assert columns == {
            "source_type": "NO",
            "target_type": "NO",
        }

        history = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            ORDER BY version
            """
        ).fetchall()

        assert history == [
            (
                1,
                INITIAL_SCHEMA.name,
                INITIAL_SCHEMA.checksum,
            ),
            (
                2,
                EDGE_CREATED_AT.name,
                EDGE_CREATED_AT.checksum,
            ),
            (
                3,
                EDGE_ENDPOINT_TYPES.name,
                EDGE_ENDPOINT_TYPES.checksum,
            ),
        ]

    # Restart must remain idempotent.
    assert manager.migrate(target_version=3) == 3


def test_live_v2_to_v3_reversed_edge_fails_closed_atomically():
    import psycopg

    reset_database()
    manager = PostgreSQLMigrationManager(DSN)

    # Build a genuine v2 database.
    assert manager.migrate(target_version=2) == 2

    with connect() as con:
        _insert_domain_node(con, "claim:corrupt-v2", "Claim")
        _insert_domain_node(con, "evidence:corrupt-v2", "Evidence")

        # Valid under v2 physical schema, semantically reversed.
        con.execute(
            """
            INSERT INTO domain_edges (
                source_id,
                edge_type,
                target_id,
                created_at
            )
            VALUES (%s, %s, %s, CURRENT_TIMESTAMP)
            """,
            (
                "evidence:corrupt-v2",
                "SUPPORTED_BY",
                "claim:corrupt-v2",
            ),
        )

    with pytest.raises(
        (
            psycopg.errors.ForeignKeyViolation,
            psycopg.errors.CheckViolation,
        )
    ):
        manager.migrate()

    # Entire v3 migration must have rolled back.
    with connect() as con:
        version_3 = con.execute(
            f"""
            SELECT version
            FROM {MIGRATION_HISTORY_TABLE}
            WHERE version = 3
            """
        ).fetchone()

        assert version_3 is None

        columns = {
            row[0]
            for row in con.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'domain_edges'
                  AND column_name IN ('source_type', 'target_type')
                """
            ).fetchall()
        }

        assert columns == set()

        edge = con.execute(
            """
            SELECT source_id, edge_type, target_id
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = %s
              AND target_id = %s
            """,
            (
                "evidence:corrupt-v2",
                "SUPPORTED_BY",
                "claim:corrupt-v2",
            ),
        ).fetchone()

        assert edge == (
            "evidence:corrupt-v2",
            "SUPPORTED_BY",
            "claim:corrupt-v2",
        )

        history = con.execute(
            f"""
            SELECT version
            FROM {MIGRATION_HISTORY_TABLE}
            ORDER BY version
            """
        ).fetchall()

        assert history == [(1,), (2,)]

    # This test intentionally leaves a semantically invalid v2 database
    # after proving that the v3 migration fails closed atomically.
    # Do not leak that state into subsequent live-test modules.
    reset_database()

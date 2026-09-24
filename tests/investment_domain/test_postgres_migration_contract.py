import pytest

from investment_domain.postgres_migrations import (
    MIGRATION_HISTORY_TABLE,
    MIGRATION_LOCK_NAMESPACE,
    Migration,
    validate_migrations,
)


def test_investment_domain_has_independent_migration_namespace():
    assert MIGRATION_HISTORY_TABLE == (
        "investment_domain_schema_migrations"
    )
    assert MIGRATION_HISTORY_TABLE != "schema_migrations"

    assert MIGRATION_LOCK_NAMESPACE == (
        "investment_domain_schema_migrations:v1"
    )


def test_migration_checksum_is_deterministic_and_content_bound():
    first = Migration(
        1,
        "base",
        ("CREATE TABLE example(id TEXT PRIMARY KEY)",),
    )
    same = Migration(
        1,
        "base",
        ("CREATE TABLE example(id TEXT PRIMARY KEY)",),
    )
    changed = Migration(
        1,
        "base",
        ("CREATE TABLE example(id BIGINT PRIMARY KEY)",),
    )

    assert first.checksum == same.checksum
    assert first.checksum != changed.checksum


def test_migration_versions_must_be_ordered():
    migrations = (
        Migration(2, "second", ("SELECT 2",)),
        Migration(1, "first", ("SELECT 1",)),
    )

    with pytest.raises(
        ValueError,
        match="IDM-M400: MIGRATIONS_NOT_ORDERED",
    ):
        validate_migrations(migrations)


def test_migration_versions_must_be_unique():
    migrations = (
        Migration(1, "first", ("SELECT 1",)),
        Migration(1, "duplicate", ("SELECT 2",)),
    )

    with pytest.raises(
        ValueError,
        match="IDM-M401: DUPLICATE_MIGRATION_VERSION",
    ):
        validate_migrations(migrations)


def test_migration_versions_must_be_contiguous_from_one():
    migrations = (
        Migration(1, "first", ("SELECT 1",)),
        Migration(3, "third", ("SELECT 3",)),
    )

    with pytest.raises(
        ValueError,
        match="IDM-M402: MIGRATION_VERSION_GAP",
    ):
        validate_migrations(migrations)


def test_empty_migration_set_is_valid_before_first_schema_revision():
    validate_migrations(())


def test_initial_schema_is_version_one_and_valid():
    from investment_domain.postgres_migrations import (
        CURRENT_SCHEMA_VERSION,
        EDGE_CREATED_AT,
        EDGE_ENDPOINT_TYPES,
        INITIAL_SCHEMA,
        METRIC_FACTS,
        ADD_METRIC_EVIDENCE_EDGES,
        METRIC_REVISION_CONSTRAINTS,
        MIGRATIONS,
    )

    assert INITIAL_SCHEMA.version == 1
    assert INITIAL_SCHEMA.name == "initial_evidence_store"
    assert EDGE_CREATED_AT.version == 2
    assert MIGRATIONS == (
        INITIAL_SCHEMA,
        EDGE_CREATED_AT,
        EDGE_ENDPOINT_TYPES,
        METRIC_FACTS,
        ADD_METRIC_EVIDENCE_EDGES,
        METRIC_REVISION_CONSTRAINTS,
    )
    assert CURRENT_SCHEMA_VERSION == 6

    validate_migrations(MIGRATIONS)


def test_initial_schema_has_required_relational_projection():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    for table in (
        "domain_nodes",
        "evidence_facts",
        "claim_facts",
        "domain_edges",
    ):
        assert f"CREATE TABLE {table}" in sql


def test_evidence_visibility_uses_publication_and_ingestion_time():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    assert "published_at TIMESTAMPTZ NOT NULL" in sql
    assert "ingested_at TIMESTAMPTZ NOT NULL" in sql
    assert "observed_at <= published_at" in sql
    assert "published_at <= ingested_at" in sql

    # effective_at is economic applicability, not a visibility gate.
    assert "effective_at <= observed_at" not in sql


def test_revision_lineage_is_explicit_and_not_source_identity():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    assert "supersedes_id TEXT NULL" in sql
    assert "REFERENCES evidence_facts(node_id)" in sql
    assert "supersedes_id <> node_id" in sql


def test_claim_evidence_edges_are_restricted_to_canonical_relations():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    assert "'SUPPORTED_BY'" in sql
    assert "'CONTRADICTED_BY'" in sql
    assert "CHECK (source_id <> target_id)" in sql


def test_schema_does_not_share_aodsl_runtime_tables():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    for runtime_table in (
        "graph_state",
        "event_log",
        "outbox",
        "audit_log",
        "schema_migrations",
    ):
        assert f"CREATE TABLE {runtime_table}" not in sql


def test_advisory_lock_key_is_deterministic_signed_bigint():
    from investment_domain.postgres_migrations import advisory_lock_key

    first = advisory_lock_key()
    second = advisory_lock_key()

    assert first == second
    assert -(2**63) <= first <= (2**63 - 1)


def test_postgres_manager_uses_transaction_scoped_advisory_lock():
    import inspect

    from investment_domain.postgres_migrations import (
        PostgreSQLMigrationManager,
    )

    source = inspect.getsource(PostgreSQLMigrationManager.migrate)

    assert "pg_advisory_xact_lock" in source
    assert "pg_advisory_lock(" not in source


def test_postgres_manager_uses_independent_history_table():
    import inspect

    from investment_domain.postgres_migrations import (
        PostgreSQLMigrationManager,
    )

    source = inspect.getsource(PostgreSQLMigrationManager)

    assert "MIGRATION_HISTORY_TABLE" in source


def test_postgres_manager_rejects_newer_database_schema():
    from investment_domain.postgres_migrations import (
        CURRENT_SCHEMA_VERSION,
        MigrationError,
        PostgreSQLMigrationManager,
    )

    manager = PostgreSQLMigrationManager("unused")

    rows = {
        migration.version: (
            migration.name,
            migration.checksum,
        )
        for migration in manager.migrations
    }
    rows[CURRENT_SCHEMA_VERSION + 1] = (
        "future_migration",
        "future_checksum",
    )

    with pytest.raises(
        MigrationError,
        match="IDM-M410: DATABASE_SCHEMA_NEWER_THAN_RUNTIME",
    ):
        manager._validate_history(rows)


def test_postgres_manager_rejects_checksum_drift():
    from investment_domain.postgres_migrations import (
        MigrationError,
        PostgreSQLMigrationManager,
    )

    manager = PostgreSQLMigrationManager("unused")

    with pytest.raises(
        MigrationError,
        match="IDM-M412: MIGRATION_CHECKSUM_MISMATCH",
    ):
        manager._validate_history(
            {
                1: ("initial_evidence_store", "deadbeef"),
            }
        )


def test_postgres_manager_has_bounded_lock_wait():
    import inspect

    from investment_domain.postgres_migrations import (
        PostgreSQLMigrationManager,
    )

    source = inspect.getsource(PostgreSQLMigrationManager.migrate)

    assert "lock_timeout" in source
    assert "set_config" in source
    assert "self.lock_timeout_ms" in source


def test_postgres_manager_default_lock_timeout_is_bounded():
    from investment_domain.postgres_migrations import (
        PostgreSQLMigrationManager,
    )

    manager = PostgreSQLMigrationManager("unused")

    assert manager.lock_timeout_ms == 5000


def test_postgres_manager_rejects_nonpositive_lock_timeout():
    from investment_domain.postgres_migrations import (
        PostgreSQLMigrationManager,
    )

    with pytest.raises(
        ValueError,
        match="IDM-M403: LOCK_TIMEOUT_MUST_BE_POSITIVE",
    ):
        PostgreSQLMigrationManager(
            "unused",
            lock_timeout_ms=0,
        )


def test_initial_schema_restricts_canonical_node_types():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    for node_type in (
        "Company",
        "Security",
        "Metric",
        "Claim",
        "Evidence",
        "Calculation",
        "Estimate",
        "Forecast",
        "Catalyst",
        "Risk",
        "Valuation",
        "Recommendation",
    ):
        assert f"'{node_type}'" in sql


def test_initial_schema_enforces_projection_node_types():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    assert "UNIQUE (id, node_type)" in sql
    assert "CHECK (node_type = 'Evidence')" in sql
    assert "CHECK (node_type = 'Claim')" in sql
    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql


def test_initial_schema_prevents_revision_branching():
    from investment_domain.postgres_migrations import INITIAL_SCHEMA

    sql = "\n".join(INITIAL_SCHEMA.statements)

    assert "CREATE UNIQUE INDEX idx_evidence_supersedes" in sql


def test_postgres_manager_rejects_applied_history_gap():
    from investment_domain.postgres_migrations import (
        Migration,
        MigrationError,
        PostgreSQLMigrationManager,
    )

    migrations = (
        Migration(1, "one", ("SELECT 1",)),
        Migration(2, "two", ("SELECT 2",)),
        Migration(3, "three", ("SELECT 3",)),
    )

    manager = PostgreSQLMigrationManager(
        "unused",
        migrations=migrations,
    )

    rows = {
        1: ("one", migrations[0].checksum),
        3: ("three", migrations[2].checksum),
    }

    with pytest.raises(
        MigrationError,
        match="IDM-M414: APPLIED_MIGRATION_HISTORY_GAP",
    ):
        manager._validate_history(rows)


def test_edge_created_at_migration_is_version_two():
    from investment_domain.postgres_migrations import (
        CURRENT_SCHEMA_VERSION,
        EDGE_CREATED_AT,
        EDGE_ENDPOINT_TYPES,
        INITIAL_SCHEMA,
        METRIC_FACTS,
        ADD_METRIC_EVIDENCE_EDGES,
        METRIC_REVISION_CONSTRAINTS,
        MIGRATIONS,
    )

    assert EDGE_CREATED_AT.version == 2
    assert EDGE_CREATED_AT.name == "add_edge_created_at"
    assert MIGRATIONS == (
        INITIAL_SCHEMA,
        EDGE_CREATED_AT,
        EDGE_ENDPOINT_TYPES,
        METRIC_FACTS,
        ADD_METRIC_EVIDENCE_EDGES,
        METRIC_REVISION_CONSTRAINTS,
    )
    assert CURRENT_SCHEMA_VERSION == 6


def test_edge_created_at_migration_preserves_v1_schema():
    from investment_domain.postgres_migrations import (
        EDGE_CREATED_AT,
        INITIAL_SCHEMA,
    )

    initial_sql = "\n".join(INITIAL_SCHEMA.statements)
    migration_sql = "\n".join(EDGE_CREATED_AT.statements)

    assert "created_at" not in initial_sql
    assert "ADD COLUMN created_at TIMESTAMPTZ NULL" in migration_sql
    assert "SET created_at = stored_at" in migration_sql
    assert "ALTER COLUMN created_at SET NOT NULL" in migration_sql


def test_edge_created_at_migration_checksum_is_content_bound():
    from investment_domain.postgres_migrations import (
        EDGE_CREATED_AT,
        Migration,
    )

    changed = Migration(
        EDGE_CREATED_AT.version,
        EDGE_CREATED_AT.name,
        EDGE_CREATED_AT.statements + ("SELECT 1",),
    )

    assert changed.checksum != EDGE_CREATED_AT.checksum

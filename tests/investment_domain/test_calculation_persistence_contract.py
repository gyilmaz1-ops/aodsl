from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)


def test_calculation_persistence_is_schema_version_8():
    assert CURRENT_SCHEMA_VERSION == 8


def test_schema_contains_calculation_facts_migration():
    migration = next(
        migration
        for migration in MIGRATIONS
        if migration.version == 7
    )

    sql = "\n".join(migration.statements)

    assert "CREATE TABLE calculation_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Calculation'" in sql
    assert "subject_id TEXT NOT NULL" in sql
    assert "formula TEXT NOT NULL" in sql
    assert "input_ids TEXT[] NOT NULL" in sql
    assert "value NUMERIC NOT NULL" in sql
    assert "unit TEXT NOT NULL" in sql
    assert "currency TEXT NULL" in sql
    assert "model_version TEXT NOT NULL" in sql

    assert "CHECK (node_type = 'Calculation')" in sql
    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository


def _migration_sql(version: int) -> str:
    migration = next(m for m in MIGRATIONS if m.version == version)
    return "\n".join(migration.statements)


def test_forecast_persistence_migration_is_schema_v13():
    from investment_domain.postgres_migrations import FORECAST_FACTS

    assert FORECAST_FACTS.version == 13
    assert CURRENT_SCHEMA_VERSION >= FORECAST_FACTS.version


def test_repository_protocol_exposes_add_forecast():
    assert hasattr(EvidenceRepository, "add_forecast")


def test_schema_contains_forecast_facts_migration():
    sql = _migration_sql(13)

    assert "CREATE TABLE forecast_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Forecast'" in sql
    assert "subject_id TEXT NOT NULL" in sql
    assert "scenario TEXT NOT NULL" in sql
    assert "as_of TIMESTAMPTZ NOT NULL" in sql
    assert "model_version TEXT NOT NULL" in sql


def test_forecast_facts_enforces_typed_domain_node_fk():
    sql = _migration_sql(13)

    assert "CHECK (node_type = 'Forecast')" in sql
    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql


def test_v13_is_append_only_after_existing_history():
    history_through_v13 = [
        migration
        for migration in MIGRATIONS
        if migration.version <= 13
    ]

    assert [
        migration.version
        for migration in history_through_v13
    ] == list(range(1, 14))
    assert history_through_v13[-1].version == 13
    assert history_through_v13[-1].name == "add_forecast_facts"


def test_forecast_schema_does_not_duplicate_domain_vocabulary():
    sql = _migration_sql(13)

    assert "'BASE'" not in sql
    assert "'BULL'" not in sql
    assert "'BEAR'" not in sql

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    ESTIMATE_FACTS,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository


def _migration_sql(version: int) -> str:
    migration = next(m for m in MIGRATIONS if m.version == version)
    return "\n".join(migration.statements)


def test_estimate_persistence_migration_is_schema_v9():
    assert ESTIMATE_FACTS.version == 9
    assert CURRENT_SCHEMA_VERSION >= ESTIMATE_FACTS.version


def test_repository_protocol_exposes_add_estimate():
    assert hasattr(EvidenceRepository, "add_estimate")


def test_schema_contains_estimate_facts_migration():
    sql = _migration_sql(9)

    assert "CREATE TABLE estimate_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Estimate'" in sql

    assert "subject_id TEXT NOT NULL" in sql
    assert "metric_name TEXT NOT NULL" in sql
    assert "period_end TIMESTAMPTZ NOT NULL" in sql
    assert "value NUMERIC NOT NULL" in sql
    assert "unit TEXT NOT NULL" in sql
    assert "scenario TEXT NOT NULL" in sql
    assert "model_version TEXT NOT NULL" in sql
    assert "as_of TIMESTAMPTZ NOT NULL" in sql
    assert "currency TEXT NULL" in sql


def test_estimate_facts_enforces_typed_domain_node_fk():
    sql = _migration_sql(9)

    assert "CHECK (node_type = 'Estimate')" in sql
    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql

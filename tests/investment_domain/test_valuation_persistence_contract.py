from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository


def _migration_sql(version: int) -> str:
    migration = next(m for m in MIGRATIONS if m.version == version)
    return "\n".join(migration.statements)


def test_valuation_persistence_migration_is_schema_v11():
    from investment_domain.postgres_migrations import VALUATION_FACTS

    assert VALUATION_FACTS.version == 11
    assert CURRENT_SCHEMA_VERSION >= VALUATION_FACTS.version


def test_repository_protocol_exposes_add_valuation():
    assert hasattr(EvidenceRepository, "add_valuation")


def test_schema_contains_valuation_facts_migration():
    sql = _migration_sql(11)

    assert "CREATE TABLE valuation_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Valuation'" in sql
    assert "security_id TEXT NOT NULL" in sql
    assert "method TEXT NOT NULL" in sql
    assert "value NUMERIC NOT NULL" in sql
    assert "currency TEXT NOT NULL" in sql
    assert "as_of TIMESTAMPTZ NOT NULL" in sql
    assert "model_version TEXT NOT NULL" in sql
    assert "scenario TEXT NOT NULL" in sql


def test_valuation_facts_enforces_typed_domain_node_fk():
    sql = _migration_sql(11)

    assert "CHECK (node_type = 'Valuation')" in sql
    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql


def test_v11_is_append_only_after_existing_history():
    history_through_v11 = [
        migration
        for migration in MIGRATIONS
        if migration.version <= 11
    ]

    assert [
        migration.version
        for migration in history_through_v11
    ] == list(range(1, 12))

    assert history_through_v11[-1].version == 11
    assert history_through_v11[-1].name == "add_valuation_facts"

    assert MIGRATIONS[8].name == "add_estimate_facts"
    assert MIGRATIONS[9].name == "add_estimate_input_provenance"
    assert MIGRATIONS[10].name == "add_valuation_facts"


def test_valuation_schema_does_not_duplicate_domain_vocabulary():
    sql = _migration_sql(11)

    assert "EV_EBITDA" not in sql
    assert "'DCF'" not in sql
    assert "'PE'" not in sql
    assert "'BASE'" not in sql
    assert "'BULL'" not in sql
    assert "'BEAR'" not in sql

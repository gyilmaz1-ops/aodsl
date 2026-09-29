from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)


def test_recommendation_facts_is_schema_version_19():
    assert CURRENT_SCHEMA_VERSION == 19

    migration = MIGRATIONS[-1]

    assert migration.version == 19
    assert migration.name == "add_recommendation_facts"


def test_recommendation_facts_projection_contract():
    migration = MIGRATIONS[-1]

    sql = "\n".join(migration.statements)

    assert "CREATE TABLE recommendation_facts" in sql

    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Recommendation'" in sql
    assert "security_id TEXT NOT NULL" in sql
    assert "action TEXT NOT NULL" in sql
    assert "as_of TIMESTAMPTZ NOT NULL" in sql
    assert "created_by TEXT NOT NULL" in sql
    assert "rationale_claim_ids TEXT[] NOT NULL" in sql

    assert "CHECK (node_type = 'Recommendation')" in sql

    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql


def test_recommendation_metadata_is_not_duplicated_in_projection():
    migration = MIGRATIONS[-1]

    sql = "\n".join(migration.statements)

    assert "metadata" not in sql.lower()

from investment_domain import postgres_migrations


def test_risk_facts_is_schema_version_21():
    assert postgres_migrations.CURRENT_SCHEMA_VERSION == 21

    migration = postgres_migrations.RISK_FACTS

    assert migration.version == 21
    assert migration.name == "add_risk_facts"
    assert postgres_migrations.MIGRATIONS[-1] is migration


def test_risk_facts_creates_exact_projection_contract():
    sql = "\n".join(
        postgres_migrations.RISK_FACTS.statements
    )

    assert "CREATE TABLE risk_facts" in sql

    assert "node_id TEXT PRIMARY KEY" in sql
    assert (
        "node_type TEXT NOT NULL DEFAULT 'Risk'"
        in sql
    )
    assert "subject_id TEXT NOT NULL" in sql
    assert "description TEXT NOT NULL" in sql
    assert "as_of TIMESTAMPTZ NOT NULL" in sql

    assert "CONSTRAINT risk_node_type" in sql
    assert "CHECK (node_type = 'Risk')" in sql

    assert "CONSTRAINT risk_domain_node_fk" in sql
    assert (
        "FOREIGN KEY (node_id, node_type)"
        in sql
    )
    assert (
        "REFERENCES domain_nodes(id, node_type)"
        in sql
    )
    assert "ON DELETE RESTRICT" in sql


def test_risk_projection_contains_no_unfrozen_columns():
    sql = "\n".join(
        postgres_migrations.RISK_FACTS.statements
    )

    forbidden = (
        "created_by",
        "severity",
        "probability",
        "likelihood",
        "impact",
        "status",
        "metadata",
    )

    for column in forbidden:
        assert column not in sql

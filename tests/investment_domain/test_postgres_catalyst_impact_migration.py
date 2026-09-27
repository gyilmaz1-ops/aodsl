from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)


def _migration(version):
    return next(
        migration
        for migration in MIGRATIONS
        if migration.version == version
    )


def _sql(migration):
    return "\n".join(migration.statements)


def test_v17_is_current_schema_version():
    assert CURRENT_SCHEMA_VERSION == 17


def test_v17_migration_is_registered_once():
    matches = [
        migration
        for migration in MIGRATIONS
        if migration.version == 17
    ]

    assert len(matches) == 1
    assert matches[0].name == "add_catalyst_impact_facts"


def test_v17_creates_typed_catalyst_impact_projection():
    sql = _sql(_migration(17))

    assert "CREATE TABLE catalyst_impact_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert (
        "node_type TEXT NOT NULL DEFAULT 'CatalystImpact'"
        in sql
    )
    assert "CHECK (node_type = 'CatalystImpact')" in sql


def test_v17_projection_contains_full_domain_payload():
    sql = _sql(_migration(17))

    required_columns = (
        "catalyst_id TEXT NOT NULL",
        "target_id TEXT NOT NULL",
        "direction TEXT NOT NULL",
        "magnitude TEXT NOT NULL",
        "probability NUMERIC NOT NULL",
        "confidence NUMERIC NOT NULL",
        "horizon TEXT NOT NULL",
        "rationale TEXT NOT NULL",
        "as_of TIMESTAMPTZ NOT NULL",
        "created_by TEXT NOT NULL",
    )

    for column in required_columns:
        assert column in sql


def test_v17_projection_references_typed_domain_node():
    sql = _sql(_migration(17))

    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql
    assert "ON DELETE RESTRICT" in sql


def test_v17_database_enforces_controlled_vocabularies():
    sql = _sql(_migration(17))

    assert (
        "direction IN ('POSITIVE', 'NEGATIVE', 'NEUTRAL')"
        in sql
    )
    assert (
        "magnitude IN ('LOW', 'MEDIUM', 'HIGH')"
        in sql
    )
    assert (
        "horizon IN "
        "('NEAR_TERM', 'MEDIUM_TERM', 'LONG_TERM')"
        in sql
    )


def test_v17_database_enforces_score_ranges():
    sql = _sql(_migration(17))

    assert "probability >= 0" in sql
    assert "probability <= 1" in sql
    assert "confidence >= 0" in sql
    assert "confidence <= 1" in sql


def test_v17_does_not_collapse_assessments_by_relation():
    sql = _sql(_migration(17))

    normalized = " ".join(sql.upper().split())

    assert "UNIQUE (CATALYST_ID, TARGET_ID)" not in normalized
    assert "UNIQUE(CATALYST_ID, TARGET_ID)" not in normalized


def test_v17_does_not_store_impact_as_edge_metadata():
    sql = _sql(_migration(17))

    assert "ALTER TABLE domain_edges ADD COLUMN" not in sql
    assert "impact_score" not in sql.lower()


def test_v17_extends_domain_node_type_constraint_for_catalyst_impact():
    sql = _sql(_migration(17)).upper()

    assert "DROP CONSTRAINT DOMAIN_NODES_NODE_TYPE" in sql
    assert "ADD CONSTRAINT DOMAIN_NODES_NODE_TYPE" in sql
    assert "'CATALYSTIMPACT'" in sql


def test_v17_preserves_existing_domain_node_types_when_adding_catalyst_impact():
    sql = _sql(_migration(17))

    expected_node_types = {
        "Company",
        "Security",
        "Metric",
        "Claim",
        "Evidence",
        "Calculation",
        "Estimate",
        "Forecast",
        "Catalyst",
        "CatalystImpact",
        "Risk",
        "Valuation",
        "Recommendation",
    }

    for node_type in expected_node_types:
        assert f"'{node_type}'" in sql

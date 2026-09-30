from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)


def _migration_20():
    return next(
        migration
        for migration in MIGRATIONS
        if migration.version == 20
    )


def test_recommendation_dependency_constraint_is_schema_version_20():

    migration = _migration_20()

    assert migration.name == "add_recommendation_dependencies"


def test_recommendation_dependency_migration_extends_edge_constraint():
    migration = _migration_20()
    sql = "\n".join(migration.statements)

    assert (
        "DROP CONSTRAINT domain_edges_target_type"
        in sql
    )
    assert (
        "ADD CONSTRAINT domain_edges_target_type"
        in sql
    )
    assert (
        "DROP CONSTRAINT domain_edges_source_relation_target_type"
        in sql
    )
    assert (
        "ADD CONSTRAINT domain_edges_source_relation_target_type"
        in sql
    )

    assert "source_type = 'Recommendation'" in sql
    assert "edge_type = 'DEPENDS_ON'" in sql

    recommendation_targets = (
        "'Valuation'",
        "'Claim'",
        "'Risk'",
        "'Catalyst'",
    )

    for target_type in recommendation_targets:
        assert target_type in sql


def test_recommendation_dependency_migration_preserves_existing_edge_contract():
    migration = _migration_20()
    sql = "\n".join(migration.statements)

    existing_source_contract = (
        "source_type = 'Claim'",
        "source_type = 'Metric'",
        "source_type = 'Calculation'",
        "source_type = 'Estimate'",
        "source_type = 'Valuation'",
        "source_type = 'Forecast'",
        "source_type = 'Catalyst'",
    )

    for fragment in existing_source_contract:
        assert fragment in sql

    existing_relation_contract = (
        "'SUPPORTED_BY'",
        "'CONTRADICTED_BY'",
        "'DERIVED_FROM'",
        "'DEPENDS_ON'",
        "'CONTAINS'",
        "'AFFECTS'",
    )

    for fragment in existing_relation_contract:
        assert fragment in sql


def test_recommendation_dependency_target_types_are_schema_legal():
    migration = _migration_20()
    sql = "\n".join(migration.statements)

    assert "ADD CONSTRAINT domain_edges_target_type" in sql

    for target_type in (
        "'Valuation'",
        "'Claim'",
        "'Risk'",
        "'Catalyst'",
    ):
        assert target_type in sql

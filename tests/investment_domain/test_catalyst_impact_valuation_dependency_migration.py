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


def test_v18_migration_remains_registered():
    migration = next(
        migration for migration in MIGRATIONS
        if migration.version == 18
    )

    assert migration.version == 18


def test_v18_is_registered_once():
    matches = [
        migration
        for migration in MIGRATIONS
        if migration.version == 18
    ]

    assert len(matches) == 1
    assert (
        matches[0].name
        == "extend_valuation_dependencies_with_catalyst_impact"
    )


def test_v18_is_appended_after_v17():
    versions = [migration.version for migration in MIGRATIONS]
    v17_index = versions.index(17)

    assert versions[v17_index : v17_index + 2] == [17, 18]


def test_v18_extends_domain_edge_target_type_for_catalyst_impact():
    sql = _sql(_migration(18))

    assert "DROP CONSTRAINT domain_edges_target_type" in sql
    assert "ADD CONSTRAINT domain_edges_target_type" in sql
    assert "'CatalystImpact'" in sql


def test_v18_allows_valuation_dependency_on_catalyst_impact():
    sql = _sql(_migration(18))

    assert (
        "DROP CONSTRAINT "
        "domain_edges_source_relation_target_type"
    ) in sql
    assert (
        "ADD CONSTRAINT "
        "domain_edges_source_relation_target_type"
    ) in sql

    assert "source_type = 'Valuation'" in sql
    assert "edge_type = 'DEPENDS_ON'" in sql
    assert "'CatalystImpact'" in sql


def test_v18_preserves_existing_valuation_dependency_targets():
    sql = _sql(_migration(18))

    for target_type in (
        "Forecast",
        "Estimate",
        "Metric",
        "Calculation",
        "CatalystImpact",
    ):
        assert f"'{target_type}'" in sql


def test_v18_does_not_create_parallel_dependency_table():
    sql = _sql(_migration(18)).upper()

    assert "CREATE TABLE" not in sql

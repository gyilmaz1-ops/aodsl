from investment_domain.edges import ALLOWED_EDGES, EdgeType
from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository
from investment_domain.types import NodeType


def _migration_v14():
    return next(
        migration
        for migration in MIGRATIONS
        if migration.version == 14
    )


def test_domain_allows_exact_forecast_composition_vocabulary():
    expected = {
        (
            NodeType.FORECAST,
            EdgeType.CONTAINS,
            NodeType.ESTIMATE,
        ),
    }

    actual = {
        edge
        for edge in ALLOWED_EDGES
        if edge[0] is NodeType.FORECAST
        and edge[1] is EdgeType.CONTAINS
    }

    assert actual == expected


def test_repository_protocol_exposes_forecast_estimate_materialization():
    assert hasattr(
        EvidenceRepository,
        "add_forecast_estimates",
    )


def test_forecast_composition_migration_is_v14():
    migration = _migration_v14()

    assert migration.name == "add_forecast_composition"
    assert CURRENT_SCHEMA_VERSION >= 14


def test_v14_extends_domain_edge_type_for_contains():
    sql = "\n".join(_migration_v14().statements)

    assert "domain_edges_type" in sql
    assert "'CONTAINS'" in sql


def test_v14_allows_exact_forecast_contains_estimate_relation():
    sql = "\n".join(_migration_v14().statements)

    assert "domain_edges_source_relation_target_type" in sql
    assert "source_type = 'Forecast'" in sql
    assert "edge_type = 'CONTAINS'" in sql
    assert "target_type = 'Estimate'" in sql


def test_v14_does_not_create_parallel_composition_table():
    sql = "\n".join(_migration_v14().statements).upper()

    assert "CREATE TABLE" not in sql


def test_migration_versions_remain_contiguous_through_v14():
    assert [
        migration.version
        for migration in MIGRATIONS
        if migration.version <= 14
    ] == list(range(1, 15))

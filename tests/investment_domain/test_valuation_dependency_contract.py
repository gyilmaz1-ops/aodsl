from investment_domain.edges import ALLOWED_EDGES, EdgeType
from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository
from investment_domain.types import NodeType


def test_domain_allows_exact_valuation_dependency_vocabulary():
    expected = {
        (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.FORECAST),
        (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.ESTIMATE),
        (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.METRIC),
        (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.CALCULATION),
        (NodeType.VALUATION, EdgeType.DEPENDS_ON, NodeType.CATALYST_IMPACT),
    }

    actual = {
        edge
        for edge in ALLOWED_EDGES
        if edge[0] is NodeType.VALUATION
        and edge[1] is EdgeType.DEPENDS_ON
    }

    assert actual == expected


def test_repository_protocol_exposes_valuation_dependency_materialization():
    assert hasattr(EvidenceRepository, "add_valuation_dependencies")


def test_valuation_dependency_migration_is_v12():
    migration = next(
        migration
        for migration in MIGRATIONS
        if migration.version == 12
    )

    assert migration.name == "add_valuation_dependencies"
    assert CURRENT_SCHEMA_VERSION >= 12


def test_v12_extends_domain_edge_target_types_for_forecast():
    migration = next(
        migration
        for migration in MIGRATIONS
        if migration.version == 12
    )
    sql = "\n".join(migration.statements)

    assert "domain_edges_target_type" in sql
    assert "'Forecast'" in sql


def test_v12_allows_exact_valuation_dependency_relation():
    migration = next(
        migration
        for migration in MIGRATIONS
        if migration.version == 12
    )
    sql = "\n".join(migration.statements)

    assert "domain_edges_source_relation_target_type" in sql
    assert "source_type = 'Valuation'" in sql
    assert "edge_type = 'DEPENDS_ON'" in sql

    for target_type in (
        "Forecast",
        "Estimate",
        "Metric",
        "Calculation",
    ):
        assert f"'{target_type}'" in sql


def test_v12_does_not_create_parallel_dependency_table():
    migration = next(
        migration
        for migration in MIGRATIONS
        if migration.version == 12
    )
    sql = "\n".join(migration.statements).upper()

    assert "CREATE TABLE" not in sql


def test_migration_versions_remain_contiguous_through_v12():
    assert [
        migration.version
        for migration in MIGRATIONS
        if migration.version <= 12
    ] == list(range(1, 13))

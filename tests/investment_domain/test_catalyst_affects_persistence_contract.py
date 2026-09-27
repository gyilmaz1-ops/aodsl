from investment_domain.edges import ALLOWED_EDGES, EdgeType
from investment_domain.postgres_migrations import MIGRATIONS
from investment_domain.types import NodeType


def _migration():
    matches = [
        migration
        for migration in MIGRATIONS
        if migration.version == 16
    ]
    assert len(matches) == 1
    return matches[0]


def _sql() -> str:
    return "\n".join(_migration().statements)


def test_v16_is_catalyst_affects_edges_migration():
    migration = _migration()

    assert migration.version == 16
    assert migration.name == "add_catalyst_affects_edges"


def test_v16_is_appended_after_v15():
    versions = [migration.version for migration in MIGRATIONS]

    v15_index = versions.index(15)
    assert versions[v15_index : v15_index + 2] == [15, 16]


def test_domain_semantics_allow_only_supported_catalyst_affects_targets():
    catalyst_affects_targets = {
        target
        for source, relation, target in ALLOWED_EDGES
        if source == NodeType.CATALYST
        and relation == EdgeType.AFFECTS
    }

    assert catalyst_affects_targets == {
        NodeType.CLAIM,
        NodeType.FORECAST,
    }


def test_v16_adds_affects_to_database_edge_type_constraint():
    sql = _sql()

    assert "domain_edges_type" in sql
    assert "'AFFECTS'" in sql


def test_v16_adds_catalyst_to_database_source_type_constraint():
    sql = _sql()

    assert "domain_edges_source_relation_target_type" in sql
    assert "source_type = 'Catalyst'" in sql


def test_v16_persists_exact_catalyst_affects_endpoint_matrix():
    sql = _sql()

    assert "source_type = 'Catalyst'" in sql
    assert "edge_type = 'AFFECTS'" in sql

    assert (
        "target_type IN ('Claim', 'Forecast')" in sql
        or
        "target_type IN (\n"
        "                    'Claim',\n"
        "                    'Forecast'\n"
        "                )" in sql
    )


def test_v16_does_not_create_parallel_edge_table():
    sql = _sql().lower()

    assert "create table catalyst_edges" not in sql
    assert "create table catalyst_affects" not in sql


def test_v16_does_not_add_unsupported_catalyst_affects_targets():
    sql = _sql()

    catalyst_section = sql.split(
        "source_type = 'Catalyst'",
        1,
    )[1]

    catalyst_section = catalyst_section.split(
        ")",
        3,
    )[0:3]

    catalyst_section = ")".join(catalyst_section)

    for unsupported in (
        "Evidence",
        "Metric",
        "Calculation",
        "Estimate",
        "Catalyst",
        "Valuation",
    ):
        assert f"'{unsupported}'" not in catalyst_section

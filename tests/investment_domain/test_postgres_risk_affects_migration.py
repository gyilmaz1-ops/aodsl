from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)


def _migration_22():
    matches = [m for m in MIGRATIONS if m.version == 22]
    assert len(matches) == 1
    return matches[0]


def _sql() -> str:
    return "\n".join(_migration_22().statements)


def test_risk_affects_migration_is_version_22():
    migration = _migration_22()

    assert migration.version == 22
    assert migration.name == "add_risk_affects_edges"
    assert CURRENT_SCHEMA_VERSION == 22


def test_risk_affects_migration_adds_exact_legal_relation():
    sql = _sql()

    assert "source_type = 'Risk'" in sql
    assert "edge_type = 'AFFECTS'" in sql
    assert (
        "target_type IN ('Claim', 'Forecast', 'Valuation')"
        in sql
    )


def test_risk_affects_migration_preserves_existing_edge_contracts():
    sql = _sql()

    assert "source_type = 'Claim'" in sql
    assert "source_type = 'Metric'" in sql
    assert "source_type = 'Calculation'" in sql
    assert "source_type = 'Estimate'" in sql
    assert "source_type = 'Valuation'" in sql
    assert "source_type = 'Forecast'" in sql
    assert "source_type = 'Catalyst'" in sql
    assert "source_type = 'Recommendation'" in sql

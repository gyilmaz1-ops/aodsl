from __future__ import annotations

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository


# CIP001
def test_repository_protocol_exposes_add_calculation_inputs():
    assert hasattr(EvidenceRepository, "add_calculation_inputs")


# CIP002 / schema-v8 append-only contract
def test_calculation_input_provenance_is_schema_v8():
    assert CURRENT_SCHEMA_VERSION == 8
    assert len(MIGRATIONS) >= 8
    assert MIGRATIONS[7].version == 8


# v1..v7 history must remain append-only.
def test_schema_v8_preserves_historical_migration_prefix():
    assert tuple(m.version for m in MIGRATIONS[:7]) == (
        1, 2, 3, 4, 5, 6, 7
    )


# v8 must widen DB semantic legality without introducing edge ordinal.
def test_schema_v8_adds_calculation_derived_from_semantics():
    migration = MIGRATIONS[7]
    sql = "\n".join(migration.statements)

    assert "Calculation" in sql
    assert "DERIVED_FROM" in sql
    assert "Metric" in sql
    assert "domain_edges" in sql
    assert "ordinal" not in sql.lower()

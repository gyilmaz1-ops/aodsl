from __future__ import annotations

from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    ESTIMATE_INPUT_PROVENANCE,
    MIGRATIONS,
)
from investment_domain.repository import EvidenceRepository


# EIP001
def test_repository_protocol_exposes_add_estimate_inputs():
    assert hasattr(EvidenceRepository, "add_estimate_inputs")


# EIP002 / schema-v10 append-only contract
def test_estimate_input_provenance_migration_is_schema_v10():
    assert ESTIMATE_INPUT_PROVENANCE.version == 10
    assert CURRENT_SCHEMA_VERSION >= ESTIMATE_INPUT_PROVENANCE.version
    assert len(MIGRATIONS) >= 10
    assert MIGRATIONS[9].version == 10


# v1..v9 history must remain append-only.
def test_schema_v10_preserves_historical_migration_prefix():
    assert tuple(m.version for m in MIGRATIONS[:9]) == (
        1, 2, 3, 4, 5, 6, 7, 8, 9
    )


# v10 widens graph legality; it must not create a second
# provenance persistence mechanism.
def test_schema_v10_adds_estimate_derived_from_semantics():
    migration = MIGRATIONS[9]
    sql = "\n".join(migration.statements)

    assert "domain_edges" in sql
    assert "Estimate" in sql
    assert "DERIVED_FROM" in sql
    assert "Metric" in sql
    assert "Calculation" in sql
    assert "Claim" in sql

    assert "CREATE TABLE" not in sql.upper()
    assert "ordinal" not in sql.lower()

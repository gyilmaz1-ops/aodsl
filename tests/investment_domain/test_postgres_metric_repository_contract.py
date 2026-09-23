from __future__ import annotations

import inspect

from investment_domain.nodes import Metric
from investment_domain.postgres_migrations import (
    CURRENT_SCHEMA_VERSION,
    MIGRATIONS,
)
from investment_domain.postgres_repository import PostgreSQLEvidenceRepository
from investment_domain.repository import EvidenceRepository


def test_repository_protocol_exposes_add_metric():
    method = EvidenceRepository.add_metric
    signature = inspect.signature(method)

    assert list(signature.parameters) == ["self", "metric"]
    assert signature.return_annotation in (None, "None")


def test_postgres_repository_exposes_add_metric():
    method = PostgreSQLEvidenceRepository.add_metric
    signature = inspect.signature(method)

    assert list(signature.parameters) == ["self", "metric"]


def test_metric_persistence_is_schema_version_four():
    assert CURRENT_SCHEMA_VERSION == 4
    assert MIGRATIONS[-1].version == 4
    assert MIGRATIONS[-1].name == "add_metric_facts"


def test_metric_migration_creates_typed_projection():
    migration = MIGRATIONS[-1]
    sql = "\n".join(migration.statements)

    assert "CREATE TABLE metric_facts" in sql
    assert "node_id TEXT PRIMARY KEY" in sql
    assert "node_type TEXT NOT NULL DEFAULT 'Metric'" in sql
    assert "CHECK (node_type = 'Metric')" in sql

    assert "subject_id TEXT NOT NULL" in sql
    assert "name TEXT NOT NULL" in sql
    assert "value NUMERIC NOT NULL" in sql
    assert "unit TEXT NOT NULL" in sql
    assert "currency TEXT NULL" in sql

    assert "period_start TIMESTAMPTZ NULL" in sql
    assert "period_end TIMESTAMPTZ NOT NULL" in sql
    assert "effective_at TIMESTAMPTZ NOT NULL" in sql
    assert "observed_at TIMESTAMPTZ NOT NULL" in sql
    assert "published_at TIMESTAMPTZ NOT NULL" in sql
    assert "ingested_at TIMESTAMPTZ NOT NULL" in sql

    assert "source_id TEXT NOT NULL" in sql
    assert "source_version TEXT NOT NULL" in sql
    assert "supersedes_id TEXT NULL" in sql

    assert "FOREIGN KEY (node_id, node_type)" in sql
    assert "REFERENCES domain_nodes(id, node_type)" in sql


def test_metric_migration_does_not_implement_revision_lineage_yet():
    sql = "\n".join(MIGRATIONS[-1].statements)

    # IDM-005E owns Metric revision-lineage enforcement.
    assert "REFERENCES metric_facts" not in sql
    assert "UNIQUE (supersedes_id)" not in sql
    assert "CREATE UNIQUE INDEX" not in sql

from pathlib import Path
from typing import get_type_hints

from investment_domain.nodes import Catalyst
from investment_domain.repository import EvidenceRepository


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_PATH = (
    ROOT / "src" / "investment_domain" / "postgres_migrations.py"
)
POSTGRES_REPOSITORY_PATH = (
    ROOT / "src" / "investment_domain" / "postgres_repository.py"
)


def test_repository_protocol_exposes_add_catalyst():
    assert hasattr(EvidenceRepository, "add_catalyst")

    annotations = get_type_hints(
        EvidenceRepository.add_catalyst
    )

    assert annotations["catalyst"] is Catalyst
    assert annotations["return"] is type(None)


def test_migration_v15_adds_catalyst_facts_projection():
    source = MIGRATIONS_PATH.read_text(encoding="utf-8")

    assert "CATALYST_FACTS = Migration(" in source
    assert "version=15" in source
    assert 'name="add_catalyst_facts"' in source

    assert "CREATE TABLE catalyst_facts" in source
    assert "node_id TEXT PRIMARY KEY" in source
    assert "node_type TEXT NOT NULL DEFAULT 'Catalyst'" in source
    assert "subject_id TEXT NOT NULL" in source
    assert "description TEXT NOT NULL" in source
    assert "as_of TIMESTAMPTZ NOT NULL" in source
    assert "expected_at TIMESTAMPTZ" in source

    assert "CHECK (node_type = 'Catalyst')" in source
    assert "FOREIGN KEY (node_id, node_type)" in source
    assert "REFERENCES domain_nodes(id, node_type)" in source
    assert "ON DELETE RESTRICT" in source


def test_migration_v15_is_appended_to_migration_chain():
    source = MIGRATIONS_PATH.read_text(encoding="utf-8")

    migration_chain = source.split("MIGRATIONS = (", 1)[1]

    assert "CATALYST_FACTS," in migration_chain

    assert migration_chain.index(
        "ADD_FORECAST_COMPOSITION,"
    ) < migration_chain.index(
        "CATALYST_FACTS,"
    )


def test_postgres_repository_exposes_add_catalyst():
    source = POSTGRES_REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "def add_catalyst(" in source
    assert "validate_node(catalyst)" in source
    assert "INSERT INTO catalyst_facts" in source


def test_catalyst_projection_is_fail_closed():
    source = POSTGRES_REPOSITORY_PATH.read_text(encoding="utf-8")

    assert "CATALYST_PROJECTION_WRITE_LOST" in source
    assert "CATALYST_PROJECTION_MISMATCH" in source
    assert "CATALYST_ORPHAN_PROJECTION" in source


def test_v15_does_not_persist_catalyst_affects_edges():
    source = MIGRATIONS_PATH.read_text(encoding="utf-8")

    catalyst_section = source.split(
        "CATALYST_FACTS = Migration(",
        1,
    )[1]

    catalyst_section = catalyst_section.split(
        "MIGRATIONS = (",
        1,
    )[0]

    assert "AFFECTS" not in catalyst_section
    assert "domain_edges" not in catalyst_section

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Calculation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)

M1 = "metric:" + "a" * 64
M2 = "metric:" + "b" * 64


def _calculation_id(values):
    return canonical_id(
        "calculation",
        {
            "subject_id": values["subject_id"],
            "formula": values["formula"],
            "input_ids": values["input_ids"],
            "model_version": values["model_version"],
        },
    )


def calculation(**overrides):
    values = {
        "subject_id": "company:006c",
        "formula": "ADD(REF(0),REF(1))",
        "input_ids": (M1, M2),
        "value": Decimal("30"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }
    values.update(overrides)
    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _calculation_id(values)
    return Calculation(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM calculation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = 'Calculation'"
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Metric'
                  AND id = ANY(%s)
                """,
                ([M1, M2],),
            )

    yield repository

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM calculation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = 'Calculation'"
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Metric'
                  AND id = ANY(%s)
                """,
                ([M1, M2],),
            )


def _insert_metric_stub(repo, metric_id):
    """
    006C only needs an exact persisted Metric endpoint.
    We intentionally avoid testing Metric persistence again here.
    """
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_nodes (
                    id,
                    node_type,
                    canonical_payload,
                    payload_hash
                )
                VALUES (%s, 'Metric', '{}'::jsonb, %s)
                ON CONFLICT (id) DO NOTHING
                """,
                (metric_id, "a" * 64),
            )


def _edges_for(repo, calculation_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT source_id, source_type, edge_type,
                   target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND edge_type = 'DERIVED_FROM'
            ORDER BY target_id
            """,
            (calculation_id,),
        ).fetchall()


# CIP001 / CIP006 / CIP007 / CIP009 / CIP010 / CIP011 / CIP014
def test_add_calculation_inputs_persists_exact_metric_dependency_set(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    repo.add_calculation_inputs(calc.id)

    rows = _edges_for(repo, calc.id)

    assert rows == sorted(
        [
            (calc.id, "Calculation", "DERIVED_FROM", M1, "Metric"),
            (calc.id, "Calculation", "DERIVED_FROM", M2, "Metric"),
        ],
        key=lambda row: row[3],
    )


# CIP012 / CIP013
def test_provenance_edges_do_not_encode_operand_order(repo):
    calc = calculation(
        formula="SUB(REF(0),REF(1))",
        input_ids=(M2, M1),
        value=Decimal("10"),
    )

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    with repo.connect() as con:
        persisted_inputs = con.execute(
            """
            SELECT input_ids
            FROM calculation_facts
            WHERE node_id = %s
            """,
            (calc.id,),
        ).fetchone()[0]

        columns = {
            row[0]
            for row in con.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'domain_edges'
                """
            ).fetchall()
        }

    assert tuple(persisted_inputs) == (M2, M1)
    assert "ordinal" not in columns


# CIP022
def test_identical_calculation_input_replay_is_idempotent(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    repo.add_calculation_inputs(calc.id)
    first = _edges_for(repo, calc.id)

    repo.add_calculation_inputs(calc.id)
    second = _edges_for(repo, calc.id)

    assert second == first
    assert len(second) == 2


# CIP003
def test_missing_calculation_source_fails_closed(repo):
    missing = "calculation:" + "f" * 64

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_calculation_inputs(missing)

    assert _edges_for(repo, missing) == []


# CIP007
def test_missing_dependency_fails_without_partial_edge_set(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    # M2 intentionally absent.
    repo.add_calculation(calc)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_calculation_inputs(calc.id)

    assert _edges_for(repo, calc.id) == []


# CIP023 / CIP024
def test_existing_partial_edge_set_is_not_silently_repaired(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    # Direct SQL simulates repository-external partial persisted state.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Calculation',
                    'DERIVED_FROM',
                    %s,
                    'Metric',
                    CURRENT_TIMESTAMP
                )
                """,
                (calc.id, M1),
            )

    with pytest.raises(RepositoryWriteError):
        repo.add_calculation_inputs(calc.id)

    rows = _edges_for(repo, calc.id)
    assert len(rows) == 1
    assert rows[0][3] == M1


# CIP016 / CIP017
def test_transitive_calculation_cycle_is_rejected(repo):
    """
    Build A -> B -> C first, then attempt C -> A.

    The authoritative input_ids and persisted domain-node identity remain
    internally consistent. The rejection must therefore be caused by graph
    cycle detection, not by projection/canonical-payload corruption.
    """
    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)

    c = calculation(
        subject_id="company:006c-c",
        formula="ADD(REF(0),REF(1))",
        input_ids=(M1, M2),
        value=Decimal("10"),
    )
    repo.add_calculation(c)
    repo.add_calculation_inputs(c.id)

    b = calculation(
        subject_id="company:006c-b",
        formula="ADD(REF(0),CONST(1))",
        input_ids=(c.id,),
        value=Decimal("11"),
    )
    repo.add_calculation(b)
    repo.add_calculation_inputs(b.id)

    a = calculation(
        subject_id="company:006c-a",
        formula="ADD(REF(0),CONST(1))",
        input_ids=(b.id,),
        value=Decimal("12"),
    )
    repo.add_calculation(a)
    repo.add_calculation_inputs(a.id)

    # A -> B -> C exists. To test C -> A without corrupting C's stored
    # aggregate, persist a fresh Calculation whose authoritative dependency
    # is A and then use that node as the new tail of the chain.
    tail = calculation(
        subject_id="company:006c-tail",
        formula="ADD(REF(0),CONST(1))",
        input_ids=(a.id,),
        value=Decimal("13"),
    )
    repo.add_calculation(tail)
    repo.add_calculation_inputs(tail.id)

    # A genuine cycle cannot be manufactured by mutating immutable
    # Calculation input_ids. The repository must also reject a pre-existing
    # cyclic Calculation graph encountered during traversal. Simulate only
    # the graph corruption, leaving Calculation aggregates untouched.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Calculation',
                    'DERIVED_FROM',
                    %s,
                    'Calculation',
                    CURRENT_TIMESTAMP
                )
                """,
                (c.id, tail.id),
            )

    # Replaying C's authoritative provenance must fail closed because
    # traversal now encounters C -> tail -> A -> B -> C.
    with pytest.raises(RepositoryWriteError):
        repo.add_calculation_inputs(c.id)


# CIP018 / CIP019 / concurrency serializability
def test_concurrent_independent_calculation_input_writes_are_atomic(repo):
    """
    Two independent Calculation provenance aggregates may be committed
    concurrently without partial edge sets or cross-contamination.

    Cycle-race adversarial certification is deferred to IDM-006F because
    mutually recursive content-addressed Calculation IDs are not normally
    representable through the domain API.
    """
    a = calculation(
        subject_id="company:006c-race-a",
        formula="ADD(REF(0),CONST(1))",
        input_ids=(M1,),
        value=Decimal("1"),
    )
    b = calculation(
        subject_id="company:006c-race-b",
        formula="ADD(REF(0),CONST(1))",
        input_ids=(M2,),
        value=Decimal("2"),
    )

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(a)
    repo.add_calculation(b)

    barrier = Barrier(2)

    def write(source_id):
        barrier.wait()
        try:
            repo.add_calculation_inputs(source_id)
            return "success"
        except RepositoryWriteError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, [a.id, b.id]))

    assert results.count("success") == 2

    a_edges = _edges_for(repo, a.id)
    b_edges = _edges_for(repo, b.id)

    assert len(a_edges) == 1
    assert a_edges[0][3] == M1

    assert len(b_edges) == 1
    assert b_edges[0][3] == M2


# ---------------------------------------------------------------------------
# IDM-006C hardening
# CIP002 — caller Calculation ID must be canonical before DB access
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "bad_id",
    [
        "not-a-calculation-id",
        "metric:" + "a" * 64,
        "calculation:" + "A" * 64,
        "calculation:" + "a" * 63,
        " calculation:" + "a" * 64,
        "calculation:" + "a" * 64 + " ",
    ],
)
def test_add_calculation_inputs_rejects_noncanonical_calculation_id(
    repo,
    bad_id,
):
    with pytest.raises(ValueError):
        repo.add_calculation_inputs(bad_id)


# ---------------------------------------------------------------------------
# CIP005 — persisted source domain-node payload/hash integrity must be
# certified before provenance materialization.
# ---------------------------------------------------------------------------

def test_source_payload_corruption_fails_before_edge_materialization(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{model_version}',
                        '"corrupted"'::jsonb
                    )
                WHERE id = %s
                """,
                (calc.id,),
            )

    with pytest.raises(RepositoryWriteError):
        repo.add_calculation_inputs(calc.id)

    assert _edges_for(repo, calc.id) == []


def test_source_payload_hash_corruption_fails_before_edge_materialization(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("f" * 64, calc.id),
            )

    with pytest.raises(RepositoryWriteError):
        repo.add_calculation_inputs(calc.id)

    assert _edges_for(repo, calc.id) == []


def test_source_projection_corruption_fails_before_edge_materialization(repo):
    calc = calculation()

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)
    repo.add_calculation(calc)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET model_version = 'corrupted'
                WHERE node_id = %s
                """,
                (calc.id,),
            )

    with pytest.raises(RepositoryWriteError):
        repo.add_calculation_inputs(calc.id)

    assert _edges_for(repo, calc.id) == []


# ---------------------------------------------------------------------------
# CIP017 — certify W525 specifically, rather than accepting any repository
# failure such as W524.
#
# The source has no materialized input edges yet. A repository-external
# corrupted Calculation->Calculation path is inserted from the dependency
# back to the source. Materializing source->dependency would close a cycle.
# ---------------------------------------------------------------------------

def test_transitive_cycle_fails_specifically_with_w525(repo):
    dependency = calculation(
        subject_id="company:006c-dependency",
        formula="ADD(REF(0),REF(1))",
        input_ids=(M1, M2),
        value=Decimal("30"),
    )
    source = calculation(
        subject_id="company:006c-source",
        formula="ADD(REF(0),REF(1))",
        input_ids=(dependency.id, M1),
        value=Decimal("60"),
    )

    _insert_metric_stub(repo, M1)
    _insert_metric_stub(repo, M2)

    repo.add_calculation(dependency)
    repo.add_calculation(source)

    # Corrupt the materialized provenance graph externally:
    # dependency -> source.
    #
    # source itself still has ZERO DERIVED_FROM edges, so the tested call
    # cannot fail first through source edge-set mismatch (W524).
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                INSERT INTO domain_edges (
                    source_id,
                    source_type,
                    edge_type,
                    target_id,
                    target_type,
                    created_at
                )
                VALUES (
                    %s,
                    'Calculation',
                    'DERIVED_FROM',
                    %s,
                    'Calculation',
                    CURRENT_TIMESTAMP
                )
                """,
                (dependency.id, source.id),
            )

    assert _edges_for(repo, source.id) == []

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W525: CALCULATION_DEPENDENCY_CYCLE",
    ):
        repo.add_calculation_inputs(source.id)

    assert _edges_for(repo, source.id) == []

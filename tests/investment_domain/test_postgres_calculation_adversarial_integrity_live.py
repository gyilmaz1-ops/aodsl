from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from psycopg.errors import CheckViolation, UniqueViolation

from investment_domain import canonical_id
from investment_domain.nodes import Calculation, Metric
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


UTC = timezone.utc
DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0, minute=0):
    return datetime(
        year,
        month,
        day,
        hour,
        minute,
        tzinfo=UTC,
    )


def metric(seed, value):
    values = {
        "subject_id": "company:006f",
        "name": "financial.revenue",
        "value": Decimal(value),
        "unit": "currency",
        "currency": "USD",
        "period_start": utc(2026, 4, 1),
        "period_end": utc(2026, 6, 30),
        "effective_at": utc(2026, 6, 30),
        "observed_at": utc(2026, 9, 1, 8),
        "published_at": utc(2026, 9, 1, 9),
        "ingested_at": utc(2026, 9, 1, 10),
        "source_id": f"source:{seed}",
        "source_version": "1",
        "supersedes_id": None,
    }

    values["id"] = canonical_id(
        "metric",
        {
            "subject_id": values["subject_id"],
            "name": values["name"],
            "period_start": values["period_start"],
            "period_end": values["period_end"],
            "effective_at": values["effective_at"],
            "observed_at": values["observed_at"],
            "published_at": values["published_at"],
            "source_id": values["source_id"],
            "source_version": values["source_version"],
        },
    )
    return Metric(**values)


def calculation(input_ids):
    values = {
        "subject_id": "company:006f",
        "formula": "SUB(REF(0),REF(1))",
        "input_ids": tuple(input_ids),
        "value": Decimal("7"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }

    values["id"] = canonical_id(
        "calculation",
        {
            "subject_id": values["subject_id"],
            "formula": values["formula"],
            "input_ids": values["input_ids"],
            "model_version": values["model_version"],
        },
    )
    return Calculation(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_database(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM forecast_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM calculation_facts")
            con.execute("DELETE FROM metric_facts")
            con.execute("DELETE FROM evidence_facts")
            con.execute("DELETE FROM claim_facts")
            con.execute("DELETE FROM domain_nodes")


def persisted_graph(repo):
    left = metric("left", "10")
    right = metric("right", "3")
    calc = calculation((left.id, right.id))

    repo.add_metric(left)
    repo.add_metric(right)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    return left, right, calc


def test_corrupt_calculation_canonical_payload_fails_closed(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s
                WHERE id = %s
                """,
                ('{"corrupt":true}', calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_corrupt_calculation_payload_hash_fails_closed(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_corrupt_calculation_formula_projection_fails_closed(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET formula = %s
                WHERE node_id = %s
                """,
                ("ADD(REF(0),REF(1))", calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_corrupt_calculation_input_ids_projection_fails_closed(repo):
    left, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET input_ids = %s
                WHERE node_id = %s
                """,
                ([left.id], calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_corrupt_metric_dependency_canonical_state_fails_closed(repo):
    left, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, left.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_extra_provenance_edge_fails_closed(repo):
    _, _, calc = persisted_graph(repo)

    extra = metric("extra", "5")
    repo.add_metric(extra)

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
                (calc.id, extra.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="input provenance set mismatch",
    ):
        repo.verify_calculation(calc.id)


def nested_graph(repo):
    left = metric("nested-left", "10")
    right = metric("nested-right", "3")

    child = calculation((left.id, right.id))

    parent_values = {
        "subject_id": "company:006f",
        "formula": "MUL(REF(0),CONST(2))",
        "input_ids": (child.id,),
        "value": Decimal("14"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }

    parent_values["id"] = canonical_id(
        "calculation",
        {
            "subject_id": parent_values["subject_id"],
            "formula": parent_values["formula"],
            "input_ids": parent_values["input_ids"],
            "model_version": parent_values["model_version"],
        },
    )
    parent = Calculation(**parent_values)

    repo.add_metric(left)
    repo.add_metric(right)

    repo.add_calculation(child)
    repo.add_calculation_inputs(child.id)

    repo.add_calculation(parent)
    repo.add_calculation_inputs(parent.id)

    return left, right, child, parent


def snapshot_calculation_state(repo):
    with repo.connect() as con:
        nodes = con.execute(
            """
            SELECT *
            FROM domain_nodes
            ORDER BY id
            """
        ).fetchall()

        calculations = con.execute(
            """
            SELECT *
            FROM calculation_facts
            ORDER BY node_id
            """
        ).fetchall()

        metrics = con.execute(
            """
            SELECT *
            FROM metric_facts
            ORDER BY node_id
            """
        ).fetchall()

        edges = con.execute(
            """
            SELECT *
            FROM domain_edges
            ORDER BY
                source_id,
                source_type,
                edge_type,
                target_id,
                target_type
            """
        ).fetchall()

    return nodes, calculations, metrics, edges


def test_nested_child_corruption_makes_parent_fail_closed(repo):
    _, _, child, parent = nested_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999"), child.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(parent.id)


def test_failed_verification_does_not_repair_corruption(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, calc.id),
            )

    before = snapshot_calculation_state(repo)

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)

    after = snapshot_calculation_state(repo)

    assert after == before


def test_repeated_corruption_failure_is_deterministic(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE ctid IN (
                    SELECT ctid
                    FROM domain_edges
                    WHERE source_id = %s
                      AND source_type = 'Calculation'
                      AND edge_type = 'DERIVED_FROM'
                    ORDER BY target_id
                    LIMIT 1
                )
                """,
                (calc.id,),
            )

    failures = []

    for _ in range(2):
        with pytest.raises(RepositoryReadError) as exc_info:
            repo.verify_calculation(calc.id)

        failures.append(str(exc_info.value))

    assert failures[0] == failures[1]


def test_write_replay_refuses_partial_provenance_without_repair(repo):
    _, right, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (calc.id, right.id),
            )

    before = snapshot_calculation_state(repo)

    from investment_domain.postgres_repository import RepositoryWriteError

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W524",
    ):
        repo.add_calculation_inputs(calc.id)

    after = snapshot_calculation_state(repo)

    assert after == before


def test_database_rejects_direct_calculation_self_edge(repo):
    _, _, calc = persisted_graph(repo)

    with pytest.raises(CheckViolation):
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
                    (calc.id, calc.id),
                )


def test_fake_cycle_is_rejected_as_provenance_corruption(repo):
    left = metric("cycle-left", "10")
    right = metric("cycle-right", "3")

    first = calculation((left.id, right.id))

    second_values = {
        "subject_id": "company:006f-cycle-second",
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (first.id,),
        "value": Decimal("8"),
        "unit": "currency",
        "currency": "USD",
        "model_version": "cel-v1",
    }

    second_values["id"] = canonical_id(
        "calculation",
        {
            "subject_id": second_values["subject_id"],
            "formula": second_values["formula"],
            "input_ids": second_values["input_ids"],
            "model_version": second_values["model_version"],
        },
    )
    second = Calculation(**second_values)

    repo.add_metric(left)
    repo.add_metric(right)

    repo.add_calculation(first)
    repo.add_calculation_inputs(first.id)

    repo.add_calculation(second)
    repo.add_calculation_inputs(second.id)

    # second -> first is canonical.
    #
    # Inject first -> second externally. This creates an edge-graph cycle,
    # but it is NOT a canonical Calculation cycle because first.input_ids
    # still authoritatively contains only left/right.
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
                (first.id, second.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="input provenance set mismatch",
    ):
        repo.verify_calculation(second.id)


def test_database_rejects_duplicate_calculation_provenance_edge(repo):
    left, _, calc = persisted_graph(repo)

    with pytest.raises(UniqueViolation):
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
                    (calc.id, left.id),
                )


def test_database_rejects_invalid_calculation_dependency_type(repo):
    left, _, calc = persisted_graph(repo)

    # The endpoint exists, but the edge deliberately declares an
    # illegal semantic target type for Calculation DERIVED_FROM.
    with pytest.raises(CheckViolation):
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
                        'Evidence',
                        CURRENT_TIMESTAMP
                    )
                    """,
                    (calc.id, left.id),
                )


def test_corrupt_metric_projection_makes_calculation_fail_closed(repo):
    left, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999"), left.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)


def test_unsupported_persisted_model_version_fails_closed(repo):
    _, _, calc = persisted_graph(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET model_version = %s
                WHERE node_id = %s
                """,
                ("hostile-v999", calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.verify_calculation(calc.id)

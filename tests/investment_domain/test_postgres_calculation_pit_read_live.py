from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

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


def _metric_id(values):
    return canonical_id(
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


def metric(seed="006d", **overrides):
    values = {
        "subject_id": f"company:{seed}",
        "name": "financial.revenue",
        "value": Decimal("100.00"),
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
    values.update(overrides)

    supplied_id = values.pop("id", None)
    values["id"] = supplied_id or _metric_id(values)
    return Metric(**values)


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


def calculation(input_id, seed="006d", **overrides):
    values = {
        "subject_id": f"company:{seed}",
        "formula": "ADD(REF(0),CONST(1))",
        "input_ids": (input_id,),
        "value": Decimal("101.00"),
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


def persist_calculation_with_metric(repo, metric_node):
    calc = calculation(
        metric_node.id,
        subject_id=metric_node.subject_id,
    )

    repo.add_metric(metric_node)
    repo.add_calculation(calc)
    repo.add_calculation_inputs(calc.id)

    # Test fixture normalization:
    # add_calculation_inputs() assigns CURRENT_TIMESTAMP because
    # created_at is repository-owned materialization metadata.
    # Pin it here so PIT assertions are independent of wall-clock time.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                """,
                (
                    utc(2026, 9, 1, 10, 30),
                    calc.id,
                ),
            )

    return calc



def restatement(predecessor, **overrides):
    values = {
        "subject_id": predecessor.subject_id,
        "name": predecessor.name,
        "value": Decimal("125.00"),
        "unit": predecessor.unit,
        "currency": predecessor.currency,
        "period_start": predecessor.period_start,
        "period_end": predecessor.period_end,
        "effective_at": predecessor.effective_at,
        "observed_at": utc(2026, 9, 1, 11),
        "published_at": utc(2026, 9, 1, 11, 15),
        "ingested_at": utc(2026, 9, 1, 11, 30),
        "source_id": predecessor.source_id,
        "source_version": "2",
        "supersedes_id": predecessor.id,
    }
    values.update(overrides)
    return metric(**values)


# CDR021 / CDR022
def test_exact_metric_dependency_does_not_substitute_visible_successor(repo):
    root = metric("exact-revision")
    calc = persist_calculation_with_metric(repo, root)

    successor = restatement(root)
    repo.add_metric(successor)

    # At this cutoff both exact root and its successor are available.
    # Calculation identity nevertheless names root.id and must not
    # traverse the Metric revision lineage to substitute successor.
    assert successor.id != root.id
    assert successor.supersedes_id == root.id

    result = repo.calculation_at(
        calc.id,
        utc(2026, 9, 1, 12),
    )

    assert result == calc
    assert result.input_ids == (root.id,)
    assert successor.id not in result.input_ids


# CDR028
def test_metric_visibility_uses_available_at_without_separate_effective_gate(repo):
    metric_node = metric(
        "effective-not-independent-gate",
        effective_at=utc(2026, 6, 30),
        observed_at=utc(2026, 9, 1, 8),
        published_at=utc(2026, 9, 1, 9),
        ingested_at=utc(2026, 9, 1, 10),
    )
    calc = persist_calculation_with_metric(repo, metric_node)

    cutoff = utc(2026, 9, 1, 12)

    # Preserve the Metric domain temporal invariant while proving that
    # Calculation PIT delegates information visibility to available_at().
    assert metric_node.effective_at <= metric_node.observed_at
    assert metric_node.published_at <= cutoff
    assert metric_node.ingested_at <= cutoff

    assert repo.calculation_at(calc.id, cutoff) == calc


# CDR002
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
def test_calculation_at_rejects_noncanonical_calculation_id(repo, bad_id):
    with pytest.raises(ValueError):
        repo.calculation_at(
            bad_id,
            utc(2026, 9, 1, 12),
        )


# CDR003
def test_calculation_at_rejects_naive_research_cutoff(repo):
    calculation_id = "calculation:" + "a" * 64

    with pytest.raises(ValueError):
        repo.calculation_at(
            calculation_id,
            datetime(2026, 9, 1, 12),
        )


# CDR005 / CDR019
def test_missing_calculation_fails_closed(repo):
    missing = "calculation:" + "f" * 64

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            missing,
            utc(2026, 9, 1, 12),
        )


# CDR006 / CDR007 / CDR010 / CDR011 / CDR028
def test_visible_exact_metric_dependency_returns_persisted_calculation(repo):
    metric_node = metric("visible")
    calc = persist_calculation_with_metric(repo, metric_node)

    result = repo.calculation_at(
        calc.id,
        utc(2026, 9, 1, 12),
    )

    assert result == calc


# CDR011 / CDR023
def test_future_published_metric_makes_calculation_unavailable(repo):
    metric_node = metric(
        "future-published",
        published_at=utc(2026, 9, 1, 13),
        ingested_at=utc(2026, 9, 1, 14),
    )
    calc = persist_calculation_with_metric(repo, metric_node)

    assert repo.calculation_at(
        calc.id,
        utc(2026, 9, 1, 12),
    ) is None


# CDR011 / CDR023
def test_future_ingested_metric_makes_calculation_unavailable(repo):
    metric_node = metric(
        "future-ingested",
        published_at=utc(2026, 9, 1, 9),
        ingested_at=utc(2026, 9, 1, 13),
    )
    calc = persist_calculation_with_metric(repo, metric_node)

    assert repo.calculation_at(
        calc.id,
        utc(2026, 9, 1, 12),
    ) is None


# CDR008 / CDR009 / CDR022
def test_future_derived_from_edge_makes_calculation_unavailable(repo):
    metric_node = metric("future-edge")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                  AND target_type = 'Metric'
                """,
                (
                    utc(2026, 9, 1, 13),
                    calc.id,
                    metric_node.id,
                ),
            )

    assert repo.calculation_at(
        calc.id,
        utc(2026, 9, 1, 12),
    ) is None


# ---------------------------------------------------------------------------
# IDM-006D Wave-2B — adversarial persistent read integrity
# ---------------------------------------------------------------------------


# CDR011 / CDR013
def test_calculation_corrupt_canonical_hash_fails_closed(repo):
    metric_node = metric("calc-hash-corruption")
    calc = persist_calculation_with_metric(repo, metric_node)

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
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR012 / CDR013
def test_calculation_projection_corruption_fails_closed(repo):
    metric_node = metric("calc-projection-corruption")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), calc.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR018 / CDR019
def test_missing_calculation_provenance_edge_fails_closed(repo):
    metric_node = metric("missing-edge")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                """,
                (calc.id,),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR024
def test_wrong_calculation_provenance_target_fails_closed(repo):
    expected_metric = metric("expected-target")
    calc = persist_calculation_with_metric(repo, expected_metric)

    wrong_metric = metric("wrong-target")
    repo.add_metric(wrong_metric)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET target_id = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (
                    wrong_metric.id,
                    calc.id,
                    expected_metric.id,
                ),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR024
def test_extra_calculation_provenance_edge_fails_closed(repo):
    expected_metric = metric("expected-extra")
    calc = persist_calculation_with_metric(repo, expected_metric)

    extra_metric = metric("extra-target")
    repo.add_metric(extra_metric)

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
                    %s
                )
                """,
                (
                    calc.id,
                    extra_metric.id,
                    utc(2026, 9, 1, 10, 30),
                ),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR024
def test_missing_exact_metric_projection_fails_closed(repo):
    metric_node = metric("missing-metric-projection")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM metric_facts
                WHERE node_id = %s
                """,
                (metric_node.id,),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR024
def test_metric_corrupt_canonical_hash_fails_closed(repo):
    metric_node = metric("metric-hash-corruption")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, metric_node.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# CDR024
def test_metric_projection_corruption_fails_closed(repo):
    metric_node = metric("metric-projection-corruption")
    calc = persist_calculation_with_metric(repo, metric_node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE metric_facts
                SET value = %s
                WHERE node_id = %s
                """,
                (Decimal("999.00"), metric_node.id),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            calc.id,
            utc(2026, 9, 1, 12),
        )


# ---------------------------------------------------------------------------
# IDM-006D Wave-3 — recursive Calculation PIT closure
# ---------------------------------------------------------------------------


def persist_nested_calculations(repo, seed="nested"):
    metric_node = metric(f"{seed}-metric")

    child = calculation(
        metric_node.id,
        seed=f"{seed}-child",
        subject_id=metric_node.subject_id,
        value=Decimal("101.00"),
    )

    parent = calculation(
        child.id,
        seed=f"{seed}-parent",
        subject_id=metric_node.subject_id,
        value=Decimal("102.00"),
    )

    repo.add_metric(metric_node)

    repo.add_calculation(child)
    repo.add_calculation_inputs(child.id)

    repo.add_calculation(parent)
    repo.add_calculation_inputs(parent.id)

    # Test-only historical materialization state.
    # Production remains repository-assigned CURRENT_TIMESTAMP.
    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = ANY(%s)
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                """,
                (
                    utc(2026, 9, 1, 10, 30),
                    [child.id, parent.id],
                ),
            )

    return metric_node, child, parent


# CDR029
def test_nested_calculation_dependency_is_visible_at_same_cutoff(repo):
    metric_node, child, parent = persist_nested_calculations(
        repo,
        "nested-visible",
    )

    cutoff = utc(2026, 9, 1, 12)

    result = repo.calculation_at(parent.id, cutoff)

    assert result == parent
    assert parent.input_ids == (child.id,)
    assert child.input_ids == (metric_node.id,)


# CDR029 / CDR030
def test_nested_child_provenance_after_cutoff_hides_parent(repo):
    metric_node, child, parent = persist_nested_calculations(
        repo,
        "nested-future-child-edge",
    )

    cutoff = utc(2026, 9, 1, 12)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (
                    utc(2026, 9, 1, 13),
                    child.id,
                    metric_node.id,
                ),
            )

    assert repo.calculation_at(parent.id, cutoff) is None


# CDR031
def test_persisted_calculation_cycle_fails_closed(repo):
    _, child, parent = persist_nested_calculations(
        repo,
        "nested-cycle",
    )

    # Hostile repository-external graph corruption:
    #
    # parent -> child already exists.
    # Add child -> parent without mutating immutable Calculation aggregates.
    #
    # This necessarily also corrupts child's authoritative provenance set.
    # CDR031 requires fail-closed behavior, not a specific diagnostic path.
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
                    %s
                )
                """,
                (
                    child.id,
                    parent.id,
                    utc(2026, 9, 1, 10, 30),
                ),
            )

    with pytest.raises(RepositoryReadError):
        repo.calculation_at(
            parent.id,
            utc(2026, 9, 1, 12),
        )


# CDR029 / CDR031
def test_shared_nested_calculation_dependency_is_not_treated_as_cycle(repo):
    metric_node = metric("shared-dag-metric")
    repo.add_metric(metric_node)

    shared = calculation(
        metric_node.id,
        seed="shared-dag-leaf",
        subject_id=metric_node.subject_id,
        value=Decimal("100.00"),
    )
    repo.add_calculation(shared)
    repo.add_calculation_inputs(shared.id)

    left = calculation(
        shared.id,
        seed="shared-dag-left",
        subject_id=metric_node.subject_id,
        value=Decimal("101.00"),
    )
    repo.add_calculation(left)
    repo.add_calculation_inputs(left.id)

    right = calculation(
        shared.id,
        seed="shared-dag-right",
        subject_id=metric_node.subject_id,
        formula="ADD(REF(0),CONST(2))",
        value=Decimal("102.00"),
    )
    repo.add_calculation(right)
    repo.add_calculation_inputs(right.id)

    parent = calculation(
        left.id,
        seed="shared-dag-parent",
        subject_id=metric_node.subject_id,
        input_ids=(left.id, right.id),
        formula="ADD(REF(0),REF(1))",
        value=Decimal("203.00"),
    )
    repo.add_calculation(parent)
    repo.add_calculation_inputs(parent.id)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = ANY(%s)
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                """,
                (
                    utc(2026, 9, 1, 10, 30),
                    [
                        shared.id,
                        left.id,
                        right.id,
                        parent.id,
                    ],
                ),
            )

    assert repo.calculation_at(
        parent.id,
        utc(2026, 9, 1, 12),
    ) == parent


# CDR024 / CDR025 / CDR029 / CDR030
def test_future_parent_edge_does_not_look_ahead_into_child_provenance(repo):
    metric_node, child, parent = persist_nested_calculations(
        repo,
        "nested-future-parent-edge-corrupt-child",
    )

    cutoff = utc(2026, 9, 1, 12)

    with repo.connect() as con:
        with con.transaction():
            # At the research cutoff the parent does not yet have this
            # Calculation dependency available.
            con.execute(
                """
                UPDATE domain_edges
                SET created_at = %s
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (
                    utc(2026, 9, 1, 13),
                    parent.id,
                    child.id,
                ),
            )

            # Hostile persistent corruption exists inside the child's
            # provenance closure, but lies behind the future parent edge.
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_id = %s
                  AND source_type = 'Calculation'
                  AND edge_type = 'DERIVED_FROM'
                  AND target_id = %s
                """,
                (
                    child.id,
                    metric_node.id,
                ),
            )

    assert repo.calculation_at(parent.id, cutoff) is None

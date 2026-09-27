import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import CatalystImpact, Estimate, Valuation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


UTC = timezone.utc
DSN = os.environ["IDM_TEST_POSTGRES_DSN"]


def _valuation() -> Valuation:
    payload = {
        "security_id": "security:nasdaq:nvda",
        "method": "DCF",
        "value": Decimal("250.00"),
        "currency": "USD",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "model_version": "1",
        "scenario": "BASE",
    }
    return Valuation(
        id=canonical_id("valuation", payload),
        **payload,
    )


def _catalyst_impact() -> CatalystImpact:
    payload = {
        "catalyst_id": "catalyst:nvda:blackwell-demand",
        "target_id": "security:nasdaq:nvda",
        "direction": "POSITIVE",
        "magnitude": "HIGH",
        "probability": Decimal("0.80"),
        "confidence": Decimal("0.90"),
        "horizon": "MEDIUM_TERM",
        "rationale": "Demand catalyst affects valuation assumptions.",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "created_by": "test-agent",
    }
    return CatalystImpact(
        id=canonical_id("catalyst_impact", payload),
        **payload,
    )


def _estimate() -> Estimate:
    payload = {
        "subject_id": "company:nvidia",
        "metric_name": "financial.revenue",
        "period_end": datetime(2027, 1, 31, tzinfo=UTC),
        "value": Decimal("200000000000"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "1",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "currency": "USD",
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM domain_edges
                WHERE source_type = 'Valuation'
                   OR target_type = 'Valuation'
                """
            )
            con.execute("DELETE FROM valuation_facts")
            con.execute("DELETE FROM estimate_facts")
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type IN (
                    'Valuation',
                    'Estimate',
                    'CatalystImpact'
                )
                """
            )

    return repository


def _persist_valuation_and_estimate(repo):
    valuation = _valuation()
    estimate = _estimate()

    repo.add_valuation(valuation)
    repo.add_estimate(estimate)

    return valuation, estimate


def test_valuation_dependency_on_estimate_is_persisted(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    repo.add_valuation_dependencies(
        valuation.id,
        (estimate.id,),
    )

    with repo.connect() as con:
        rows = con.execute(
            """
            SELECT source_type, edge_type, target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
            ORDER BY target_id
            """,
            (valuation.id,),
        ).fetchall()

    assert rows == [
        (
            "Valuation",
            "DEPENDS_ON",
            estimate.id,
            "Estimate",
        )
    ]


def test_identical_valuation_dependency_replay_is_idempotent(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    dependencies = (estimate.id,)

    repo.add_valuation_dependencies(
        valuation.id,
        dependencies,
    )
    repo.add_valuation_dependencies(
        valuation.id,
        dependencies,
    )

    with repo.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            """,
            (valuation.id,),
        ).fetchone()[0]

    assert count == 1


def test_missing_valuation_dependency_target_fails_closed(repo):
    valuation = _valuation()
    repo.add_valuation(valuation)

    missing = canonical_id(
        "estimate",
        {
            "subject_id": "company:nvidia",
            "metric_name": "financial.revenue",
            "period_end": datetime(2028, 1, 31, tzinfo=UTC),
            "value": Decimal("300000000000"),
            "unit": "currency",
            "scenario": "BASE",
            "model_version": "1",
            "as_of": datetime(2026, 9, 26, tzinfo=UTC),
            "currency": "USD",
        },
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (missing,),
        )


def test_wrong_valuation_dependency_target_type_fails_closed(repo):
    valuation = _valuation()
    repo.add_valuation(valuation)

    evidence_id = "evidence:test-valuation-dependency"

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
                VALUES (
                    %s,
                    'Evidence',
                    '{}'::jsonb,
                    repeat('0', 64)
                )
                """,
                (evidence_id,),
            )

    try:
        with pytest.raises(
            RepositoryWriteError,
            match="IDM-W510: EDGE_TARGET_TYPE_MISMATCH",
        ):
            repo.add_valuation_dependencies(
                valuation.id,
                (evidence_id,),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM domain_nodes WHERE id = %s",
                    (evidence_id,),
                )


def test_missing_valuation_source_fails_closed(repo):
    valuation = _valuation()
    estimate = _estimate()
    repo.add_estimate(estimate)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W507: EDGE_SOURCE_NOT_FOUND",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id,),
        )


def test_empty_valuation_dependency_set_is_rejected(repo):
    valuation = _valuation()
    repo.add_valuation(valuation)

    with pytest.raises(
        ValueError,
        match="dependency_ids must be a non-empty tuple",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (),
        )


def test_duplicate_valuation_dependencies_are_rejected(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    with pytest.raises(
        ValueError,
        match="dependency_ids must not contain duplicates",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id, estimate.id),
        )


def test_noncanonical_valuation_source_id_is_rejected(repo):
    estimate = _estimate()
    repo.add_estimate(estimate)

    with pytest.raises(
        ValueError,
        match="valuation_id must be a canonical Valuation ID",
    ):
        repo.add_valuation_dependencies(
            "valuation:not-canonical",
            (estimate.id,),
        )


def _valuation_dependency_rows(repo, valuation_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT target_id, target_type
            FROM domain_edges
            WHERE source_id = %s
              AND source_type = 'Valuation'
              AND edge_type = 'DEPENDS_ON'
            ORDER BY target_id
            """,
            (valuation_id,),
        ).fetchall()


def _second_estimate() -> Estimate:
    payload = {
        "subject_id": "company:nvidia",
        "metric_name": "financial.revenue",
        "period_end": datetime(2028, 1, 31, tzinfo=UTC),
        "value": Decimal("300000000000"),
        "unit": "currency",
        "scenario": "BASE",
        "model_version": "1",
        "as_of": datetime(2026, 9, 26, tzinfo=UTC),
        "currency": "USD",
    }
    return Estimate(
        id=canonical_id("estimate", payload),
        **payload,
    )


def test_existing_partial_valuation_dependency_set_is_not_repaired(repo):
    valuation = _valuation()
    first = _estimate()
    second = _second_estimate()

    repo.add_valuation(valuation)
    repo.add_estimate(first)
    repo.add_estimate(second)

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
                    'Valuation',
                    'DEPENDS_ON',
                    %s,
                    'Estimate',
                    CURRENT_TIMESTAMP
                )
                """,
                (valuation.id, first.id),
            )

    before = _valuation_dependency_rows(repo, valuation.id)

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W535: VALUATION_DEPENDENCY_SET_MISMATCH",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (first.id, second.id),
        )

    after = _valuation_dependency_rows(repo, valuation.id)

    assert before == [(first.id, "Estimate")]
    assert after == before


def test_missing_dependency_fails_without_partial_valuation_edge_set(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    missing = canonical_id(
        "estimate",
        {
            "subject_id": "company:nvidia",
            "metric_name": "financial.revenue",
            "period_end": datetime(2029, 1, 31, tzinfo=UTC),
            "value": Decimal("400000000000"),
            "unit": "currency",
            "scenario": "BASE",
            "model_version": "1",
            "as_of": datetime(2026, 9, 26, tzinfo=UTC),
            "currency": "USD",
        },
    )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W508: EDGE_TARGET_NOT_FOUND",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id, missing),
        )

    assert _valuation_dependency_rows(repo, valuation.id) == []


def test_corrupted_valuation_domain_node_blocks_dependency_write(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s::jsonb,
                    payload_hash = %s
                WHERE id = %s
                """,
                (
                    '{"corrupted":true}',
                    "0" * 64,
                    valuation.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id,),
        )

    assert _valuation_dependency_rows(repo, valuation.id) == []


def test_corrupted_valuation_projection_blocks_dependency_write(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE valuation_facts
                SET scenario = 'BULL'
                WHERE node_id = %s
                """,
                (valuation.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W533: VALUATION_PROJECTION_MISMATCH",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id,),
        )

    assert _valuation_dependency_rows(repo, valuation.id) == []


def test_missing_valuation_projection_blocks_dependency_write(repo):
    valuation, estimate = _persist_valuation_and_estimate(repo)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM valuation_facts
                WHERE node_id = %s
                """,
                (valuation.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W532: VALUATION_PROJECTION_WRITE_LOST",
    ):
        repo.add_valuation_dependencies(
            valuation.id,
            (estimate.id,),
        )

    assert _valuation_dependency_rows(repo, valuation.id) == []


def test_database_rejects_invalid_valuation_dependency_target_type(repo):
    valuation = _valuation()
    repo.add_valuation(valuation)

    evidence_id = "evidence:invalid-valuation-dependency-db-test"

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
                VALUES (
                    %s,
                    'Evidence',
                    '{}'::jsonb,
                    repeat('0', 64)
                )
                """,
                (evidence_id,),
            )

    try:
        with pytest.raises(Exception) as exc_info:
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
                            'Valuation',
                            'DEPENDS_ON',
                            %s,
                            'Evidence',
                            CURRENT_TIMESTAMP
                        )
                        """,
                        (valuation.id, evidence_id),
                    )

        exc = exc_info.value
        assert getattr(exc, "sqlstate", None) == "23514"
        assert (
            getattr(
                getattr(exc, "diag", None),
                "constraint_name",
                None,
            )
            == "domain_edges_source_relation_target_type"
        )

        assert _valuation_dependency_rows(repo, valuation.id) == []

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    "DELETE FROM domain_nodes WHERE id = %s",
                    (evidence_id,),
                )


def test_valuation_dependency_aggregate_rolls_back_on_second_insert_failure(
    repo,
):
    valuation = _valuation()
    first = _estimate()
    second = _second_estimate()

    repo.add_valuation(valuation)
    repo.add_estimate(first)
    repo.add_estimate(second)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                CREATE OR REPLACE FUNCTION
                    reject_second_valuation_dependency_for_atomicity_test()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.target_id = TG_ARGV[0] THEN
                        RAISE EXCEPTION
                            'forced second valuation dependency failure';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )

            from psycopg import sql

            trigger_sql = sql.SQL(
                """
                CREATE TRIGGER
                    reject_second_valuation_dependency_for_atomicity_test
                BEFORE INSERT ON domain_edges
                FOR EACH ROW
                WHEN (
                    NEW.source_type = 'Valuation'
                    AND NEW.edge_type = 'DEPENDS_ON'
                )
                EXECUTE FUNCTION
                    reject_second_valuation_dependency_for_atomicity_test(
                        {}
                    )
                """
            ).format(sql.Literal(second.id))

            con.execute(trigger_sql)

    try:
        with pytest.raises(
            Exception,
            match="forced second valuation dependency failure",
        ):
            repo.add_valuation_dependencies(
                valuation.id,
                (first.id, second.id),
            )

        # Both edge inserts are inside the same repository transaction.
        # Failure on the second insert must roll back the first.
        assert _valuation_dependency_rows(repo, valuation.id) == []

    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DROP TRIGGER IF EXISTS
                        reject_second_valuation_dependency_for_atomicity_test
                    ON domain_edges
                    """
                )
                con.execute(
                    """
                    DROP FUNCTION IF EXISTS
                        reject_second_valuation_dependency_for_atomicity_test()
                    """
                )



def test_valuation_dependency_on_catalyst_impact_is_persisted(repo):
    valuation = _valuation()
    impact = _catalyst_impact()

    repo.add_valuation(valuation)
    repo.add_catalyst_impact(impact)

    repo.add_valuation_dependencies(
        valuation.id,
        (impact.id,),
    )

    assert _valuation_dependency_rows(
        repo,
        valuation.id,
    ) == [
        (impact.id, "CatalystImpact"),
    ]


def test_mixed_valuation_dependency_set_accepts_catalyst_impact(repo):
    valuation = _valuation()
    estimate = _estimate()
    impact = _catalyst_impact()

    repo.add_valuation(valuation)
    repo.add_estimate(estimate)
    repo.add_catalyst_impact(impact)

    repo.add_valuation_dependencies(
        valuation.id,
        (estimate.id, impact.id),
    )

    assert set(
        _valuation_dependency_rows(repo, valuation.id)
    ) == {
        (estimate.id, "Estimate"),
        (impact.id, "CatalystImpact"),
    }

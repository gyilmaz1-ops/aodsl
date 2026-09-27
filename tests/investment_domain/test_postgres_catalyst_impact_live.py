from __future__ import annotations

import os
from datetime import datetime, timezone

import psycopg
import pytest

from investment_domain.postgres_migrations import (
    CATALYST_IMPACT_FACTS,
    MIGRATION_HISTORY_TABLE,
    PostgreSQLMigrationManager,
)


DSN = os.getenv("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN not configured",
)

UTC = timezone.utc


def connect():
    return psycopg.connect(DSN)


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    PostgreSQLMigrationManager(DSN).migrate()


@pytest.fixture(autouse=True)
def clean_v17_rows():
    with connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM catalyst_impact_facts
                WHERE node_id LIKE 'v17:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v17:%'
                """
            )

    yield

    with connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM catalyst_impact_facts
                WHERE node_id LIKE 'v17:%'
                """
            )
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE id LIKE 'v17:%'
                """
            )


def _insert_node(
    con,
    node_id: str,
    node_type: str = "CatalystImpact",
) -> None:
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
            %s,
            '{}'::jsonb,
            repeat('0', 64)
        )
        """,
        (node_id, node_type),
    )


def _insert_impact(
    con,
    *,
    node_id: str,
    node_type: str = "CatalystImpact",
    catalyst_id: str = "catalyst:test",
    target_id: str = "claim:test",
    direction: str = "POSITIVE",
    magnitude: str = "HIGH",
    probability=0.75,
    confidence=0.80,
    horizon: str = "NEAR_TERM",
) -> None:
    con.execute(
        """
        INSERT INTO catalyst_impact_facts (
            node_id,
            node_type,
            catalyst_id,
            target_id,
            direction,
            magnitude,
            probability,
            confidence,
            horizon,
            rationale,
            as_of,
            created_by
        )
        VALUES (
            %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s
        )
        """,
        (
            node_id,
            node_type,
            catalyst_id,
            target_id,
            direction,
            magnitude,
            probability,
            confidence,
            horizon,
            "V17 live PostgreSQL contract test",
            datetime(2026, 9, 27, 9, 0, tzinfo=UTC),
            "test-agent",
        ),
    )


def _assert_check_violation(
    *,
    constraint_name: str,
    **impact_overrides,
) -> None:
    node_id = impact_overrides.pop(
        "node_id",
        f"v17:check:{constraint_name}",
    )

    with connect() as con:
        with con.transaction():
            _insert_node(con, node_id)

    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        with connect() as con:
            with con.transaction():
                _insert_impact(
                    con,
                    node_id=node_id,
                    **impact_overrides,
                )

    assert exc_info.value.sqlstate == "23514"
    assert exc_info.value.diag.constraint_name == constraint_name


def test_v17_migration_history_is_persisted():
    with connect() as con:
        row = con.execute(
            f"""
            SELECT version, name, checksum
            FROM {MIGRATION_HISTORY_TABLE}
            WHERE version = 17
            """
        ).fetchone()

    assert row == (
        17,
        CATALYST_IMPACT_FACTS.name,
        CATALYST_IMPACT_FACTS.checksum,
    )


def test_database_accepts_valid_catalyst_impact():
    node_id = "v17:impact:valid"

    with connect() as con:
        with con.transaction():
            _insert_node(con, node_id)
            _insert_impact(con, node_id=node_id)

    with connect() as con:
        row = con.execute(
            """
            SELECT
                node_type,
                catalyst_id,
                target_id,
                direction,
                magnitude,
                probability,
                confidence,
                horizon
            FROM catalyst_impact_facts
            WHERE node_id = %s
            """,
            (node_id,),
        ).fetchone()

    assert row is not None
    assert row[0] == "CatalystImpact"
    assert row[1] == "catalyst:test"
    assert row[2] == "claim:test"
    assert row[3] == "POSITIVE"
    assert row[4] == "HIGH"
    assert float(row[5]) == 0.75
    assert float(row[6]) == 0.80
    assert row[7] == "NEAR_TERM"


def test_database_rejects_missing_domain_node():
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info:
        with connect() as con:
            with con.transaction():
                _insert_impact(
                    con,
                    node_id="v17:impact:missing-parent",
                )

    assert exc_info.value.sqlstate == "23503"
    assert (
        exc_info.value.diag.constraint_name
        == "catalyst_impact_domain_node_fk"
    )


def test_database_rejects_wrong_fact_node_type():
    node_id = "v17:impact:wrong-fact-type"

    with connect() as con:
        with con.transaction():
            _insert_node(con, node_id)

    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        with connect() as con:
            with con.transaction():
                _insert_impact(
                    con,
                    node_id=node_id,
                    node_type="Catalyst",
                )

    assert exc_info.value.sqlstate == "23514"
    assert (
        exc_info.value.diag.constraint_name
        == "catalyst_impact_node_type"
    )


@pytest.mark.parametrize(
    ("direction", "constraint_name"),
    [
        ("UP", "catalyst_impact_direction"),
        ("positive", "catalyst_impact_direction"),
    ],
)
def test_database_rejects_invalid_direction(
    direction,
    constraint_name,
):
    _assert_check_violation(
        constraint_name=constraint_name,
        node_id=f"v17:direction:{direction}",
        direction=direction,
    )


def test_database_rejects_invalid_magnitude():
    _assert_check_violation(
        constraint_name="catalyst_impact_magnitude",
        magnitude="EXTREME",
    )


def test_database_rejects_invalid_horizon():
    _assert_check_violation(
        constraint_name="catalyst_impact_horizon",
        horizon="IMMEDIATE",
    )


@pytest.mark.parametrize(
    "probability",
    [-0.01, 1.01],
)
def test_database_rejects_probability_outside_unit_interval(
    probability,
):
    _assert_check_violation(
        constraint_name="catalyst_impact_probability_range",
        node_id=f"v17:probability:{probability}",
        probability=probability,
    )


@pytest.mark.parametrize(
    "confidence",
    [-0.01, 1.01],
)
def test_database_rejects_confidence_outside_unit_interval(
    confidence,
):
    _assert_check_violation(
        constraint_name="catalyst_impact_confidence_range",
        node_id=f"v17:confidence:{confidence}",
        confidence=confidence,
    )


def test_database_accepts_probability_and_confidence_boundaries():
    cases = [
        ("v17:boundary:zero", 0, 0),
        ("v17:boundary:one", 1, 1),
    ]

    with connect() as con:
        with con.transaction():
            for node_id, probability, confidence in cases:
                _insert_node(con, node_id)
                _insert_impact(
                    con,
                    node_id=node_id,
                    probability=probability,
                    confidence=confidence,
                )

    with connect() as con:
        rows = con.execute(
            """
            SELECT
                node_id,
                probability,
                confidence
            FROM catalyst_impact_facts
            WHERE node_id LIKE 'v17:boundary:%'
            ORDER BY node_id
            """
        ).fetchall()

    assert len(rows) == 2


def test_database_restricts_deleting_parent_domain_node():
    node_id = "v17:impact:delete-restrict"

    with connect() as con:
        with con.transaction():
            _insert_node(con, node_id)
            _insert_impact(con, node_id=node_id)

    with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info:
        with connect() as con:
            with con.transaction():
                con.execute(
                    """
                    DELETE FROM domain_nodes
                    WHERE id = %s
                    """,
                    (node_id,),
                )

    assert exc_info.value.sqlstate == "23503"
    assert (
        exc_info.value.diag.constraint_name
        == "catalyst_impact_domain_node_fk"
    )


def test_database_allows_multiple_assessments_for_same_catalyst_target():
    first_id = "v17:impact:assessment:1"
    second_id = "v17:impact:assessment:2"

    with connect() as con:
        with con.transaction():
            _insert_node(con, first_id)
            _insert_node(con, second_id)

            _insert_impact(
                con,
                node_id=first_id,
                catalyst_id="catalyst:shared",
                target_id="forecast:shared",
                probability=0.60,
                confidence=0.70,
            )

            _insert_impact(
                con,
                node_id=second_id,
                catalyst_id="catalyst:shared",
                target_id="forecast:shared",
                probability=0.85,
                confidence=0.90,
            )

    with connect() as con:
        count = con.execute(
            """
            SELECT count(*)
            FROM catalyst_impact_facts
            WHERE catalyst_id = 'catalyst:shared'
              AND target_id = 'forecast:shared'
            """
        ).fetchone()[0]

    assert count == 2

from __future__ import annotations

import os
from dataclasses import replace
from decimal import Decimal

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
        "subject_id": "company:acme",
        "formula": "ADD(REF(0),REF(1))",
        "input_ids": (M1, M2),
        "value": Decimal("125.500"),
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
def clean_calculation_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM calculation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Calculation",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM calculation_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Calculation",),
            )


def fetch_projection(repo, calculation_id):
    with repo.connect() as con:
        return con.execute(
            """
            SELECT
                node_type,
                subject_id,
                formula,
                input_ids,
                value,
                unit,
                currency,
                model_version
            FROM calculation_facts
            WHERE node_id = %s
            """,
            (calculation_id,),
        ).fetchone()


# CP001 / CP002 / CP003
def test_repository_exposes_add_calculation_and_validates_before_write(repo):
    calc = calculation()

    assert callable(repo.add_calculation)

    repo.add_calculation(calc)

    bad_values = {
        "subject_id": calc.subject_id,
        "formula": "add(REF(0),REF(1))",
        "input_ids": calc.input_ids,
        "model_version": calc.model_version,
    }
    bad = replace(
        calc,
        id=_calculation_id(bad_values),
        formula=bad_values["formula"],
    )

    with pytest.raises(ValueError):
        repo.add_calculation(bad)


# CP004..CP014
def test_add_calculation_persists_complete_projection(repo):
    calc = calculation()

    repo.add_calculation(calc)

    row = fetch_projection(repo, calc.id)

    assert row is not None
    assert row[0] == "Calculation"
    assert row[1] == calc.subject_id
    assert row[2] == calc.formula
    assert tuple(row[3]) == calc.input_ids
    assert row[4] == calc.value
    assert isinstance(row[4], Decimal)
    assert not isinstance(row[4], float)
    assert row[5] == calc.unit
    assert row[6] == calc.currency
    assert row[7] == calc.model_version

    with repo.connect() as con:
        node = con.execute(
            """
            SELECT node_type
            FROM domain_nodes
            WHERE id = %s
            """,
            (calc.id,),
        ).fetchone()

    assert node == ("Calculation",)


# CP010 / CP011 / CP012 / CP020
def test_input_ids_preserve_semantic_order(repo):
    calc = calculation(
        formula="SUB(REF(0),REF(1))",
        input_ids=(M2, M1),
        value=Decimal("10"),
    )

    repo.add_calculation(calc)

    row = fetch_projection(repo, calc.id)

    assert tuple(row[3]) == (M2, M1)


# CP014
def test_nullable_currency_round_trip(repo):
    calc = calculation(
        formula="DIV(REF(0),REF(1))",
        value=Decimal("2"),
        unit="ratio",
        currency=None,
    )

    repo.add_calculation(calc)

    row = fetch_projection(repo, calc.id)
    assert row[6] is None


# CP015 / CP016
def test_identical_calculation_write_is_idempotent(repo):
    calc = calculation()

    repo.add_calculation(calc)
    repo.add_calculation(calc)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (calc.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM calculation_facts WHERE node_id = %s",
            (calc.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert projection_count == 1


# CP017
def test_same_id_with_corrupted_domain_node_fails_closed(repo):
    calc = calculation()
    repo.add_calculation(calc)

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
                    calc.id,
                ),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W501: IDENTITY_CONTENT_COLLISION",
    ):
        repo.add_calculation(calc)


# CP019 / CP020
def test_existing_calculation_projection_mismatch_fails_closed(repo):
    calc = calculation()
    repo.add_calculation(calc)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE calculation_facts
                SET input_ids = %s
                WHERE node_id = %s
                """,
                ([M2, M1], calc.id),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W522: CALCULATION_PROJECTION_MISMATCH",
    ):
        repo.add_calculation(calc)


# CP018
def test_missing_projection_on_replay_fails_closed(repo):
    calc = calculation()
    repo.add_calculation(calc)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM calculation_facts WHERE node_id = %s",
                (calc.id,),
            )

    with pytest.raises(
        RepositoryWriteError,
        match="IDM-W521: CALCULATION_PROJECTION_WRITE_LOST",
    ):
        repo.add_calculation(calc)


# CP002 / CP003 / CP021 / CP022
def test_invalid_calculation_is_rejected_before_persistence(repo):
    good = calculation()

    bad_values = {
        "subject_id": good.subject_id,
        "formula": "ADD(REF(0),REF(1))",
        "input_ids": (M1, M1),
        "model_version": good.model_version,
    }

    bad = Calculation(
        id=_calculation_id(bad_values),
        subject_id=good.subject_id,
        formula=bad_values["formula"],
        input_ids=bad_values["input_ids"],
        value=good.value,
        unit=good.unit,
        currency=good.currency,
        model_version=good.model_version,
    )

    with pytest.raises(ValueError):
        repo.add_calculation(bad)

    with repo.connect() as con:
        node_count = con.execute(
            "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
            (bad.id,),
        ).fetchone()[0]

        projection_count = con.execute(
            "SELECT COUNT(*) FROM calculation_facts WHERE node_id = %s",
            (bad.id,),
        ).fetchone()[0]

    assert node_count == 0
    assert projection_count == 0


# CP005 / CP021 / CP022
def test_projection_failure_rolls_back_domain_node_insert(repo):
    calc = calculation()

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE calculation_facts
                ADD CONSTRAINT calculation_test_forced_failure
                CHECK (model_version <> 'cel-v1')
                """
            )

    try:
        with pytest.raises(Exception):
            repo.add_calculation(calc)

        with repo.connect() as con:
            node_count = con.execute(
                "SELECT COUNT(*) FROM domain_nodes WHERE id = %s",
                (calc.id,),
            ).fetchone()[0]

            projection_count = con.execute(
                """
                SELECT COUNT(*)
                FROM calculation_facts
                WHERE node_id = %s
                """,
                (calc.id,),
            ).fetchone()[0]

        assert node_count == 0
        assert projection_count == 0
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    ALTER TABLE calculation_facts
                    DROP CONSTRAINT IF EXISTS
                        calculation_test_forced_failure
                    """
                )

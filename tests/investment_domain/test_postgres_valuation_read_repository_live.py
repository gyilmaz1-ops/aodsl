from datetime import datetime, timezone
from decimal import Decimal

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Valuation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


def utc(year, month, day, hour):
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


def valuation(
    seed,
    *,
    security_id="security:test:test",
    method="DCF",
    scenario="BASE",
    as_of=None,
):
    values = {
        "security_id": security_id,
        "method": method,
        "value": Decimal("250.12500"),
        "currency": "USD",
        "as_of": as_of or utc(2026, 9, 30, 12),
        "model_version": f"valuation-model-{seed}",
        "scenario": scenario,
    }
    return Valuation(
        id=canonical_id("valuation", values),
        **values,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM valuation_facts")
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Valuation'
                """
            )

    return repository


def test_valuation_at_round_trips_visible_node(repo):
    node = valuation("visible", as_of=utc(2026, 9, 30, 11))
    repo.add_valuation(node)

    assert repo.valuation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_valuation_at_hides_future_node(repo):
    node = valuation("future", as_of=utc(2026, 9, 30, 13))
    repo.add_valuation(node)

    assert repo.valuation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_valuation_at_is_visible_at_exact_as_of(repo):
    node = valuation("boundary", as_of=utc(2026, 9, 30, 12))
    repo.add_valuation(node)

    assert repo.valuation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_valuation_at_returns_none_when_anchor_is_missing(repo):
    node = valuation("missing")

    assert repo.valuation_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_valuation_at_rejects_noncanonical_id_before_database(repo):
    with pytest.raises(ValueError):
        repo.valuation_at(
            "valuation:not-a-canonical-content-id",
            utc(2026, 9, 30, 12),
        )


def test_valuation_at_rejects_naive_cutoff_before_database(repo):
    node = valuation("naive-cutoff")

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.valuation_at(
            node.id,
            datetime(2026, 9, 30, 12),
        )


def test_latest_valuation_at_returns_none_when_stream_has_no_node(repo):
    assert repo.latest_valuation_at(
        "security:test:missing",
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_valuation_at_returns_latest_visible_node(repo):
    security_id = "security:test:latest"

    older = valuation(
        "older",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 10),
    )
    newer = valuation(
        "newer",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_valuation(older)
    repo.add_valuation(newer)

    assert repo.latest_valuation_at(
        security_id,
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) == newer


def test_latest_valuation_at_ignores_future_node_for_selection(repo):
    security_id = "security:test:future-selection"

    visible = valuation(
        "visible",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = valuation(
        "future",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_valuation(visible)
    repo.add_valuation(future)

    assert repo.latest_valuation_at(
        security_id,
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) == visible


def test_latest_valuation_at_exact_cutoff_is_visible(repo):
    node = valuation(
        "exact-cutoff",
        security_id="security:test:exact",
        as_of=utc(2026, 9, 30, 12),
    )
    repo.add_valuation(node)

    assert repo.latest_valuation_at(
        node.security_id,
        node.method,
        node.scenario,
        utc(2026, 9, 30, 12),
    ) == node


def test_latest_valuation_at_rejects_naive_cutoff(repo):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.latest_valuation_at(
            "security:test:test",
            "DCF",
            "BASE",
            datetime(2026, 9, 30, 12),
        )


def test_latest_valuation_at_rejects_empty_security_id(repo):
    with pytest.raises(ValueError, match="security_id must not be empty"):
        repo.latest_valuation_at(
            "",
            "DCF",
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_latest_valuation_at_rejects_empty_method(repo):
    with pytest.raises(ValueError, match="method must not be empty"):
        repo.latest_valuation_at(
            "security:test:test",
            "",
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_latest_valuation_at_rejects_empty_scenario(repo):
    with pytest.raises(ValueError, match="scenario must not be empty"):
        repo.latest_valuation_at(
            "security:test:test",
            "DCF",
            "",
            utc(2026, 9, 30, 12),
        )


def test_latest_valuation_at_accepts_canonical_business_reference(repo):
    assert repo.latest_valuation_at(
        "security:test:business-reference",
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_valuation_at_separates_method_streams(repo):
    security_id = "security:test:method"

    dcf = valuation(
        "dcf",
        security_id=security_id,
        method="DCF",
        as_of=utc(2026, 9, 30, 10),
    )
    pe = valuation(
        "pe",
        security_id=security_id,
        method="PE",
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_valuation(dcf)
    repo.add_valuation(pe)

    assert repo.latest_valuation_at(
        security_id,
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) == dcf


def test_latest_valuation_at_separates_scenario_streams(repo):
    security_id = "security:test:scenario"

    base = valuation(
        "base",
        security_id=security_id,
        scenario="BASE",
        as_of=utc(2026, 9, 30, 10),
    )
    bull = valuation(
        "bull",
        security_id=security_id,
        scenario="BULL",
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_valuation(base)
    repo.add_valuation(bull)

    assert repo.latest_valuation_at(
        security_id,
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) == base


def test_valuation_at_fails_closed_when_projection_is_missing(repo):
    node = valuation("missing-projection")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM valuation_facts WHERE node_id = %s",
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R571: VALUATION_PROJECTION_NOT_FOUND",
    ):
        repo.valuation_at(node.id, utc(2026, 9, 30, 12))


def test_valuation_at_fails_closed_on_projection_tampering(repo):
    node = valuation("projection-tamper")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE valuation_facts
                SET method = %s
                WHERE node_id = %s
                """,
                ("INVALID", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R569: INVALID_STORED_VALUATION",
    ):
        repo.valuation_at(node.id, utc(2026, 9, 30, 12))


def test_valuation_at_fails_closed_on_canonical_payload_tampering(repo):
    node = valuation("canonical-payload-tamper")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{method}',
                        to_jsonb(%s::text)
                    )
                WHERE id = %s
                """,
                ("PE", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R570: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.valuation_at(node.id, utc(2026, 9, 30, 12))


def test_valuation_at_fails_closed_on_payload_hash_tampering(repo):
    node = valuation("hash-tamper")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R570: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.valuation_at(node.id, utc(2026, 9, 30, 12))


def test_valuation_at_fails_closed_on_future_anchor_integrity_failure(repo):
    node = valuation(
        "future-anchor-integrity",
        as_of=utc(2026, 9, 30, 13),
    )
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R570: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.valuation_at(node.id, utc(2026, 9, 30, 12))


def test_latest_valuation_at_fails_closed_on_latest_as_of_tie(repo):
    security_id = "security:test:tie"

    first = valuation(
        "tie-first",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    second = valuation(
        "tie-second",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_valuation(first)
    repo.add_valuation(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R572: VALUATION_PIT_AMBIGUITY",
    ):
        repo.latest_valuation_at(
            security_id,
            "DCF",
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_latest_valuation_at_does_not_look_ahead_into_future_integrity(repo):
    security_id = "security:test:no-look-ahead"

    visible = valuation(
        "no-look-ahead-visible",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = valuation(
        "no-look-ahead-future",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_valuation(visible)
    repo.add_valuation(future)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, future.id),
            )

    assert repo.latest_valuation_at(
        security_id,
        "DCF",
        "BASE",
        utc(2026, 9, 30, 12),
    ) == visible

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R570: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.valuation_at(
            future.id,
            utc(2026, 9, 30, 12),
        )


def test_latest_valuation_at_fails_closed_on_selected_anchor_integrity(repo):
    security_id = "security:test:selected-integrity"

    selected = valuation(
        "selected-integrity",
        security_id=security_id,
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_valuation(selected)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET payload_hash = %s
                WHERE id = %s
                """,
                ("0" * 64, selected.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R570: VALUATION_INTEGRITY_FAILURE",
    ):
        repo.latest_valuation_at(
            security_id,
            "DCF",
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_valuation_at_fails_closed_on_wrong_anchor_type(repo):
    node = valuation("wrong-anchor-type")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE valuation_facts
                DROP CONSTRAINT valuation_domain_node_fk
                """
            )
            con.execute(
                """
                UPDATE domain_nodes
                SET node_type = 'Claim'
                WHERE id = %s
                """,
                (node.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R568: VALUATION_TYPE_MISMATCH",
        ):
            repo.valuation_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Valuation'
                    WHERE id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE valuation_facts
                    ADD CONSTRAINT valuation_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )


def test_valuation_at_fails_closed_on_wrong_projection_type(repo):
    node = valuation("wrong-projection-type")
    repo.add_valuation(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE valuation_facts
                DROP CONSTRAINT valuation_node_type
                """
            )
            con.execute(
                """
                ALTER TABLE valuation_facts
                DROP CONSTRAINT valuation_domain_node_fk
                """
            )
            con.execute(
                """
                UPDATE valuation_facts
                SET node_type = 'Claim'
                WHERE node_id = %s
                """,
                (node.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R568: VALUATION_TYPE_MISMATCH",
        ):
            repo.valuation_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE valuation_facts
                    SET node_type = 'Valuation'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE valuation_facts
                    ADD CONSTRAINT valuation_node_type
                    CHECK (node_type = 'Valuation')
                    """
                )
                con.execute(
                    """
                    ALTER TABLE valuation_facts
                    ADD CONSTRAINT valuation_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )

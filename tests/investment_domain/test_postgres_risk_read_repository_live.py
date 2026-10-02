from datetime import datetime, timezone

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Risk
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


def utc(year, month, day, hour):
    return datetime(
        year,
        month,
        day,
        hour,
        0,
        tzinfo=timezone.utc,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM risk_facts")
            con.execute(
                """                 DELETE FROM domain_nodes
                WHERE node_type = 'Risk'
                """
            )

    return repository


def risk(
    seed,
    *,
    subject_id="security:TEST",
    as_of=None,
):
    kwargs = {
        "subject_id": subject_id,
        "description": f"Risk assessment {seed}",
        "as_of": as_of or utc(2026, 9, 30, 12),
    }
    return Risk(
        id=canonical_id("risk", kwargs),
        **kwargs,
    )


def test_risk_at_round_trips_visible_node(repo):
    node = risk("visible", as_of=utc(2026, 9, 30, 11))
    repo.add_risk(node)

    assert repo.risk_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_risk_at_hides_future_assessment(repo):
    node = risk("future", as_of=utc(2026, 9, 30, 13))
    repo.add_risk(node)

    assert repo.risk_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_risk_at_is_visible_at_exact_as_of(repo):
    node = risk("boundary", as_of=utc(2026, 9, 30, 12))
    repo.add_risk(node)

    assert repo.risk_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_risk_at_returns_none_when_anchor_is_missing(repo):
    missing = canonical_id(
        "risk",
        {
            "subject_id": "security:MISSING",
            "description": "Missing risk",
            "as_of": utc(2026, 9, 30, 11),
        },
    )

    assert repo.risk_at(
        missing,
        utc(2026, 9, 30, 12),
    ) is None


def test_risk_at_rejects_noncanonical_id_before_database(repo):
    with pytest.raises(ValueError):
        repo.risk_at(
            "risk:not-a-canonical-content-id",
            utc(2026, 9, 30, 12),
        )


def test_risk_at_rejects_naive_cutoff_before_database(repo):
    node = risk("naive-cutoff")

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.risk_at(
            node.id,
            datetime(2026, 9, 30, 12),
        )


def test_latest_risk_at_returns_none_when_subject_has_no_assessment(repo):
    assert repo.latest_risk_at(
        "security:MISSING",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_risk_at_returns_latest_visible_assessment(repo):
    subject_id = "security:LATEST"

    older = risk(
        "latest-older",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 10),
    )
    newer = risk(
        "latest-newer",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_risk(older)
    repo.add_risk(newer)

    assert repo.latest_risk_at(
        subject_id,
        utc(2026, 9, 30, 12),
    ) == newer


def test_latest_risk_at_ignores_future_assessment_for_selection(repo):
    subject_id = "security:FUTURE-SELECTION"

    visible = risk(
        "future-selection-visible",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = risk(
        "future-selection-future",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_risk(visible)
    repo.add_risk(future)

    assert repo.latest_risk_at(
        subject_id,
        utc(2026, 9, 30, 12),
    ) == visible


def test_latest_risk_at_exact_cutoff_is_visible(repo):
    node = risk(
        "latest-boundary",
        subject_id="security:BOUNDARY",
        as_of=utc(2026, 9, 30, 12),
    )
    repo.add_risk(node)

    assert repo.latest_risk_at(
        node.subject_id,
        utc(2026, 9, 30, 12),
    ) == node


def test_latest_risk_at_rejects_naive_cutoff(repo):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.latest_risk_at(
            "security:TEST",
            datetime(2026, 9, 30, 12),
        )


def test_latest_risk_at_rejects_empty_subject_id(repo):
    with pytest.raises(
        ValueError,
        match="subject_id must not be empty",
    ):
        repo.latest_risk_at(
            "",
            utc(2026, 9, 30, 12),
        )


def test_latest_risk_at_accepts_noncanonical_business_reference_id(repo):
    node = risk(
        "business-reference",
        subject_id="security:business-key",
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_risk(node)

    assert repo.latest_risk_at(
        "security:business-key",
        utc(2026, 9, 30, 12),
    ) == node


def test_risk_at_fails_closed_when_projection_is_missing(repo):
    node = risk("missing-projection")
    repo.add_risk(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                DELETE FROM risk_facts
                WHERE node_id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R561: RISK_PROJECTION_NOT_FOUND",
    ):
        repo.risk_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_risk_at_fails_closed_on_projection_tampering(repo):
    node = risk("projection-tamper")
    repo.add_risk(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE risk_facts
                SET description = %s
                WHERE node_id = %s
                """,
                ("", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R559: INVALID_STORED_RISK",
    ):
        repo.risk_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_risk_at_fails_closed_on_canonical_payload_tampering(repo):
    node = risk("canonical-payload-tamper")
    repo.add_risk(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{description}',
                        to_jsonb(%s::text)
                    )
                WHERE id = %s
                """,
                ("Tampered canonical risk", node.id),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R560: RISK_INTEGRITY_FAILURE",
    ):
        repo.risk_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_risk_at_fails_closed_on_payload_hash_tampering(repo):
    node = risk("hash-tamper")
    repo.add_risk(node)

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
        match="IDM-R560: RISK_INTEGRITY_FAILURE",
    ):
        repo.risk_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_risk_at_fails_closed_on_future_anchor_integrity_failure(repo):
    node = risk(
        "future-anchor-integrity",
        as_of=utc(2026, 9, 30, 13),
    )
    repo.add_risk(node)

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
        match="IDM-R560: RISK_INTEGRITY_FAILURE",
    ):
        repo.risk_at(
            node.id,
            utc(2026, 9, 30, 12),
        )


def test_latest_risk_at_fails_closed_on_latest_as_of_tie(repo):
    subject_id = "security:TIE"

    first = risk(
        "tie-first",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    second = risk(
        "tie-second",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_risk(first)
    repo.add_risk(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R562: RISK_PIT_AMBIGUITY",
    ):
        repo.latest_risk_at(
            subject_id,
            utc(2026, 9, 30, 12),
        )


def test_latest_risk_at_does_not_look_ahead_into_future_integrity(repo):
    subject_id = "security:NO-LOOK-AHEAD"

    visible = risk(
        "no-look-ahead-visible",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = risk(
        "no-look-ahead-future",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_risk(visible)
    repo.add_risk(future)

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

    assert repo.latest_risk_at(
        subject_id,
        utc(2026, 9, 30, 12),
    ) == visible

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R560: RISK_INTEGRITY_FAILURE",
    ):
        repo.risk_at(
            future.id,
            utc(2026, 9, 30, 12),
        )


def test_latest_risk_at_fails_closed_on_selected_anchor_integrity(repo):
    subject_id = "security:SELECTED-INTEGRITY"

    selected = risk(
        "selected-integrity",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_risk(selected)

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
        match="IDM-R560: RISK_INTEGRITY_FAILURE",
    ):
        repo.latest_risk_at(
            subject_id,
            utc(2026, 9, 30, 12),
        )


def test_risk_at_fails_closed_on_wrong_anchor_type(repo):
    node = risk("wrong-anchor-type")
    repo.add_risk(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE risk_facts
                DROP CONSTRAINT risk_domain_node_fk
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
            match="IDM-R558: RISK_TYPE_MISMATCH",
        ):
            repo.risk_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Risk'
                    WHERE id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE risk_facts
                    ADD CONSTRAINT risk_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )


def test_risk_at_fails_closed_on_wrong_projection_type(repo):
    node = risk("wrong-projection-type")
    repo.add_risk(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                ALTER TABLE risk_facts
                DROP CONSTRAINT risk_node_type
                """
            )
            con.execute(
                """
                ALTER TABLE risk_facts
                DROP CONSTRAINT risk_domain_node_fk
                """
            )
            con.execute(
                """
                UPDATE risk_facts
                SET node_type = 'Claim'
                WHERE node_id = %s
                """,
                (node.id,),
            )

    try:
        with pytest.raises(
            RepositoryReadError,
            match="IDM-R558: RISK_TYPE_MISMATCH",
        ):
            repo.risk_at(
                node.id,
                utc(2026, 9, 30, 12),
            )
    finally:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE risk_facts
                    SET node_type = 'Risk'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )
                con.execute(
                    """
                    ALTER TABLE risk_facts
                    ADD CONSTRAINT risk_node_type
                    CHECK (node_type = 'Risk')
                    """
                )
                con.execute(
                    """
                    ALTER TABLE risk_facts
                    ADD CONSTRAINT risk_domain_node_fk
                    FOREIGN KEY (node_id, node_type)
                    REFERENCES domain_nodes(id, node_type)
                    ON DELETE RESTRICT
                    """
                )

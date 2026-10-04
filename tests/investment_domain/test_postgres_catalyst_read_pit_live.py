from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from investment_domain import canonical_id
from investment_domain.nodes import Catalyst
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = os.environ.get("IDM_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_TEST_POSTGRES_DSN is not configured",
)


def utc(year, month, day, hour=0):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def catalyst(**overrides):
    values = {
        "subject_id": "security:nasdaq:nvda",
        "description": "Next-generation GPU platform launch",
        "as_of": utc(2026, 9, 26, 8),
        "expected_at": utc(2027, 3, 1, 8),
    }
    values.update(overrides)

    supplied_id = values.pop("id", None)

    values["id"] = supplied_id or canonical_id(
        "catalyst",
        {
            "subject_id": values["subject_id"],
            "description": values["description"],
            "as_of": values["as_of"],
            "expected_at": values["expected_at"],
        },
    )

    return Catalyst(**values)


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    return PostgreSQLEvidenceRepository(DSN)


@pytest.fixture(autouse=True)
def clean_catalyst_read_state(repo):
    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM catalyst_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Catalyst",),
            )

    yield

    with repo.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM catalyst_impact_facts")
            con.execute("DELETE FROM catalyst_facts")
            con.execute(
                "DELETE FROM domain_nodes WHERE node_type = %s",
                ("Catalyst",),
            )


def test_catalyst_at_returns_visible_exact_id(repo):
    item = catalyst()
    repo.add_catalyst(item)

    result = repo.catalyst_at(
        item.id,
        utc(2026, 9, 27, 8),
    )

    assert result == item


def test_catalyst_at_hides_valid_future_item(repo):
    item = catalyst()
    repo.add_catalyst(item)

    result = repo.catalyst_at(
        item.id,
        utc(2026, 9, 25, 8),
    )

    assert result is None


def test_catalyst_at_is_visible_at_exact_cutoff(repo):
    item = catalyst()
    repo.add_catalyst(item)

    result = repo.catalyst_at(
        item.id,
        item.as_of,
    )

    assert result == item


def test_catalyst_at_returns_none_when_anchor_missing(repo):
    missing_id = canonical_id(
        "catalyst",
        {
            "subject_id": "security:nasdaq:missing",
            "description": "Missing catalyst",
            "as_of": utc(2026, 9, 26, 8),
            "expected_at": None,
        },
    )

    assert repo.catalyst_at(
        missing_id,
        utc(2026, 9, 27, 8),
    ) is None


def test_catalyst_at_rejects_noncanonical_id_before_db(repo):
    with pytest.raises(
        ValueError,
        match="canonical Catalyst ID",
    ):
        repo.catalyst_at(
            "not-a-canonical-catalyst-id",
            utc(2026, 9, 27, 8),
        )


def test_catalyst_at_rejects_naive_cutoff_before_db(repo):
    item = catalyst()

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.catalyst_at(
            item.id,
            datetime(2026, 9, 27, 8),
        )


def test_catalyst_at_fails_when_projection_missing(repo):
    item = catalyst()
    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM catalyst_facts WHERE node_id = %s",
                (item.id,),
            )

    with pytest.raises(
        Exception,
        match="IDM-R581: CATALYST_PROJECTION_NOT_FOUND",
    ):
        repo.catalyst_at(
            item.id,
            utc(2026, 9, 27, 8),
        )


def test_catalyst_at_fails_for_invalid_stored_catalyst(repo):
    item = catalyst()
    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE catalyst_facts
                SET description = ''
                WHERE node_id = %s
                """,
                (item.id,),
            )

    with pytest.raises(
        Exception,
        match="IDM-R579: INVALID_STORED_CATALYST",
    ):
        repo.catalyst_at(
            item.id,
            utc(2026, 9, 27, 8),
        )


def test_catalyst_at_fails_on_canonical_integrity_tamper(repo):
    item = catalyst()
    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s
                WHERE id = %s
                """,
                ("{}", item.id),
            )

    with pytest.raises(
        Exception,
        match="IDM-R580: CATALYST_INTEGRITY_FAILURE",
    ):
        repo.catalyst_at(
            item.id,
            utc(2026, 9, 27, 8),
        )


def test_catalyst_at_checks_integrity_before_future_visibility(repo):
    item = catalyst(
        as_of=utc(2026, 10, 10, 8),
        expected_at=utc(2027, 3, 1, 8),
    )
    repo.add_catalyst(item)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload = %s
                WHERE id = %s
                """,
                ("{}", item.id),
            )

    with pytest.raises(
        Exception,
        match="IDM-R580: CATALYST_INTEGRITY_FAILURE",
    ):
        repo.catalyst_at(
            item.id,
            utc(2026, 10, 1, 8),
        )


def test_latest_catalyst_at_returns_none_when_stream_missing(repo):
    assert (
        repo.latest_catalyst_at(
            "security:missing",
            utc(2026, 9, 27, 8),
        )
        is None
    )


def test_latest_catalyst_at_returns_latest_visible_for_subject(repo):
    subject_id = "security:latest-catalyst"

    older = catalyst(
        subject_id=subject_id,
        description="Older catalyst",
        as_of=utc(2026, 9, 20, 8),
        expected_at=utc(2026, 12, 1, 8),
    )
    newer = catalyst(
        subject_id=subject_id,
        description="Newer catalyst",
        as_of=utc(2026, 9, 25, 8),
        expected_at=utc(2027, 1, 1, 8),
    )

    repo.add_catalyst(older)
    repo.add_catalyst(newer)

    actual = repo.latest_catalyst_at(
        subject_id,
        utc(2026, 9, 27, 8),
    )

    assert actual == newer


def test_latest_catalyst_at_ignores_future_rows(repo):
    subject_id = "security:future-catalyst"

    visible = catalyst(
        subject_id=subject_id,
        description="Visible catalyst",
        as_of=utc(2026, 9, 20, 8),
    )
    future = catalyst(
        subject_id=subject_id,
        description="Future catalyst",
        as_of=utc(2026, 10, 10, 8),
    )

    repo.add_catalyst(visible)
    repo.add_catalyst(future)

    actual = repo.latest_catalyst_at(
        subject_id,
        utc(2026, 10, 1, 8),
    )

    assert actual == visible


def test_latest_catalyst_at_includes_exact_cutoff(repo):
    subject_id = "security:exact-cutoff-catalyst"

    item = catalyst(
        subject_id=subject_id,
        description="Exact cutoff catalyst",
        as_of=utc(2026, 10, 1, 8),
    )
    repo.add_catalyst(item)

    actual = repo.latest_catalyst_at(
        subject_id,
        utc(2026, 10, 1, 8),
    )

    assert actual == item


def test_latest_catalyst_at_rejects_empty_subject_before_db(repo):
    with pytest.raises(ValueError):
        repo.latest_catalyst_at(
            "",
            utc(2026, 10, 1, 8),
        )


def test_latest_catalyst_at_rejects_naive_cutoff_before_db(repo):
    with pytest.raises(ValueError):
        repo.latest_catalyst_at(
            "security:naive-cutoff-catalyst",
            datetime(2026, 10, 1, 8),
        )


def test_latest_catalyst_at_accepts_noncanonical_business_subject(repo):
    subject_id = "business-subject-reference"

    item = catalyst(
        subject_id=subject_id,
        description="Business subject catalyst",
        as_of=utc(2026, 9, 20, 8),
    )
    repo.add_catalyst(item)

    actual = repo.latest_catalyst_at(
        subject_id,
        utc(2026, 9, 27, 8),
    )

    assert actual == item


def test_latest_catalyst_at_description_is_supersedable(repo):
    subject_id = "security:catalyst-description-supersession"

    older = catalyst(
        subject_id=subject_id,
        description="Old description",
        as_of=utc(2026, 9, 20, 8),
    )
    newer = catalyst(
        subject_id=subject_id,
        description="New description",
        as_of=utc(2026, 9, 21, 8),
    )

    repo.add_catalyst(older)
    repo.add_catalyst(newer)

    assert repo.latest_catalyst_at(
        subject_id,
        utc(2026, 9, 22, 8),
    ) == newer


def test_latest_catalyst_at_expected_at_is_supersedable(repo):
    subject_id = "security:catalyst-expected-at-supersession"

    older = catalyst(
        subject_id=subject_id,
        description="Catalyst",
        as_of=utc(2026, 9, 20, 8),
        expected_at=utc(2026, 11, 1, 8),
    )
    newer = catalyst(
        subject_id=subject_id,
        description="Catalyst",
        as_of=utc(2026, 9, 21, 8),
        expected_at=utc(2026, 12, 1, 8),
    )

    repo.add_catalyst(older)
    repo.add_catalyst(newer)

    assert repo.latest_catalyst_at(
        subject_id,
        utc(2026, 9, 22, 8),
    ) == newer


def test_latest_catalyst_at_fails_on_same_time_ambiguity(repo):
    subject_id = "security:catalyst-pit-ambiguity"
    as_of = utc(2026, 9, 20, 8)

    first = catalyst(
        subject_id=subject_id,
        description="First catalyst",
        as_of=as_of,
    )
    second = catalyst(
        subject_id=subject_id,
        description="Second catalyst",
        as_of=as_of,
    )

    repo.add_catalyst(first)
    repo.add_catalyst(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R582: CATALYST_PIT_AMBIGUITY",
    ):
        repo.latest_catalyst_at(
            subject_id,
            utc(2026, 9, 21, 8),
        )


def test_latest_catalyst_at_ignores_corrupted_future_row(repo):
    subject_id = "security:catalyst-future-corruption"

    visible = catalyst(
        subject_id=subject_id,
        description="Visible catalyst",
        as_of=utc(2026, 9, 20, 8),
    )
    future = catalyst(
        subject_id=subject_id,
        description="Future catalyst",
        as_of=utc(2026, 10, 20, 8),
    )

    repo.add_catalyst(visible)
    repo.add_catalyst(future)

    with repo.connect() as con:
        con.execute(
            """
            UPDATE domain_nodes
            SET canonical_payload = %s
            WHERE id = %s
            """,
            ("{}", future.id),
        )
        con.commit()

    assert repo.latest_catalyst_at(
        subject_id,
        utc(2026, 9, 25, 8),
    ) == visible


def test_latest_catalyst_at_fails_on_selected_visible_corruption(repo):
    subject_id = "security:catalyst-visible-corruption"

    selected = catalyst(
        subject_id=subject_id,
        description="Selected catalyst",
        as_of=utc(2026, 9, 20, 8),
    )

    repo.add_catalyst(selected)

    with repo.connect() as con:
        con.execute(
            """
            UPDATE domain_nodes
            SET canonical_payload = %s
            WHERE id = %s
            """,
            ("{}", selected.id),
        )
        con.commit()

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R580: CATALYST_INTEGRITY_FAILURE",
    ):
        repo.latest_catalyst_at(
            subject_id,
            utc(2026, 9, 25, 8),
        )

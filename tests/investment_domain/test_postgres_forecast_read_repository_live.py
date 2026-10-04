from datetime import datetime, timezone

import pytest

from investment_domain.identity import canonical_id
from investment_domain.nodes import Forecast
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.postgres_repository import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


DSN = "postgresql://aodsl:aodsl_cert@127.0.0.1:55433/aodsl_cert"


def utc(year, month, day, hour):
    return datetime(year, month, day, hour, 0, tzinfo=timezone.utc)


def forecast(
    seed,
    *,
    subject_id="security:T  T",
    scenario="BASE",
    as_of=None,
):
    values = {
        "subject_id": subject_id,
        "scenario": scenario,
        "as_of": as_of or utc(2026, 9, 30, 12),
        "model_version": f"forecast-model-{seed}",
    }
    return Forecast(
        id=canonical_id("forecast", values),
        **values,
    )


@pytest.fixture
def repo():
    PostgreSQLMigrationManager(DSN).migrate()
    repository = PostgreSQLEvidenceRepository(DSN)

    with repository.connect() as con:
        with con.transaction():
            con.execute("DELETE FROM domain_edges")
            con.execute("DELETE FROM forecast_facts")
            con.execute(
                """
                DELETE FROM domain_nodes
                WHERE node_type = 'Forecast'
                """
            )

    return repository


def test_forecast_at_round_trips_visible_node(repo):
    node = forecast(
        "visible",
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_forecast(node)

    assert repo.forecast_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_forecast_at_hides_future_node(repo):
    node = forecast(
        "future",
        as_of=utc(2026, 9, 30, 13),
    )
    repo.add_forecast(node)

    assert repo.forecast_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_forecast_at_is_visible_at_exact_as_of(repo):
    node = forecast(
        "boundary",
        as_of=utc(2026, 9, 30, 12),
    )
    repo.add_forecast(node)

    assert repo.forecast_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) == node


def test_forecast_at_returns_none_when_anchor_is_missing(repo):
    node = forecast("missing")

    assert repo.forecast_at(
        node.id,
        utc(2026, 9, 30, 12),
    ) is None


def test_forecast_at_rejects_noncanonical_id_before_database(repo):
    with pytest.raises(ValueError):
        repo.forecast_at(
            "forecast:not-a-canonical-content-id",
            utc(2026, 9, 30, 12),
        )


def test_forecast_at_rejects_naive_cutoff_before_database(repo):
    node = forecast("naive-cutoff")

    with pytest.raises(ValueError, match="timezone-aware"):
        repo.forecast_at(
            node.id,
            datetime(2026, 9, 30, 12),
        )


def test_latest_forecast_at_returns_none_when_stream_has_no_node(repo):
    assert repo.latest_forecast_at(
        "security:MISSING",
        "BASE",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_forecast_at_returns_latest_visible_node(repo):
    subject_id = "security:LATEST"

    older = forecast(
        "older",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 10),
    )
    newer = forecast(
        "newer",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_forecast(older)
    repo.add_forecast(newer)

    assert repo.latest_forecast_at(
        subject_id,
        "BASE",
        utc(2026, 9, 30, 12),
    ) == newer


def test_latest_forecast_at_ignores_future_node_for_selection(repo):
    subject_id = "security:FUTURE-SELECTION"

    visible = forecast(
        "visible-selection",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = forecast(
        "future-selection",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_forecast(visible)
    repo.add_forecast(future)

    assert repo.latest_forecast_at(
        subject_id,
        "BASE",
        utc(2026, 9, 30, 12),
    ) == visible


def test_latest_forecast_at_exact_cutoff_is_visible(repo):
    node = forecast(
        "latest-boundary",
        subject_id="security:EXACT",
        as_of=utc(2026, 9, 30, 12),
    )
    repo.add_forecast(node)

    assert repo.latest_forecast_at(
        node.subject_id,
        node.scenario,
        utc(2026, 9, 30, 12),
    ) == node


def test_latest_forecast_at_rejects_naive_cutoff(repo):
    with pytest.raises(ValueError, match="timezone-aware"):
        repo.latest_forecast_at(
            "security:TEST",
            "BASE",
            datetime(2026, 9, 30, 12),
        )


def test_latest_forecast_at_rejects_empty_subject_id(repo):
    with pytest.raises(ValueError, match="subject_id"):
        repo.latest_forecast_at(
            "",
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_latest_forecast_at_rejects_empty_scenario(repo):
    with pytest.raises(ValueError, match="scenario"):
        repo.latest_forecast_at(
            "security:TEST",
            "",
            utc(2026, 9, 30, 12),
        )


def test_latest_forecast_at_accepts_noncanonical_business_reference(repo):
    assert repo.latest_forecast_at(
        "security:business-reference",
        "BASE",
        utc(2026, 9, 30, 12),
    ) is None


def test_latest_forecast_at_separates_scenario_streams(repo):
    subject_id = "security:SCENARIO-STREAM"

    base = forecast(
        "base-stream",
        subject_id=subject_id,
        scenario="BASE",
        as_of=utc(2026, 9, 30, 10),
    )
    bull = forecast(
        "bull-stream",
        subject_id=subject_id,
        scenario="BULL",
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_forecast(base)
    repo.add_forecast(bull)

    assert repo.latest_forecast_at(
        subject_id,
        "BASE",
        utc(2026, 9, 30, 12),
    ) == base

    assert repo.latest_forecast_at(
        subject_id,
        "BULL",
        utc(2026, 9, 30, 12),
    ) == bull


def test_latest_forecast_at_supersedes_model_version(repo):
    subject_id = "security:MODEL-VERSION"

    older = forecast(
        "model-v1",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 10),
    )
    newer = forecast(
        "model-v2",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )

    repo.add_forecast(older)
    repo.add_forecast(newer)

    assert older.model_version != newer.model_version

    assert repo.latest_forecast_at(
        subject_id,
        "BASE",
        utc(2026, 9, 30, 12),
    ) == newer


def test_forecast_at_fails_closed_when_projection_is_missing(repo):
    node = forecast("missing-projection")
    repo.add_forecast(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                "DELETE FROM forecast_facts WHERE node_id = %s",
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R576: FORECAST_PROJECTION_NOT_FOUND",
    ):
        repo.forecast_at(
            node.id,
            utc(2026, 9, 30, 13),         )


def test_forecast_at_fails_closed_on_invalid_stored_projection(repo):
    node = forecast("invalid-projection")
    repo.add_forecast(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE forecast_facts
                SET scenario = 'INVALID'
                WHERE node_id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R574: INVALID_STORED_FORECAST",
    ):
        repo.forecast_at(
            node.id,
            utc(2026, 9, 30, 13),
        )


def test_forecast_at_fails_closed_on_canonical_payload_tamper(repo):
    node = forecast("payload-tamper")
    repo.add_forecast(node)

    with repo.connect() as con:
        with con.transaction():
            con.execute(
                """
                UPDATE domain_nodes
                SET canonical_payload =
                    jsonb_set(
                        canonical_payload,
                        '{model_version}',
                        '"tampered-model"'::jsonb
                    )
                WHERE id = %s
                """,
                (node.id,),
            )

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R575: FORECAST_INTEGRITY_FAILURE",
    ):
        repo.forecast_at(
            node.id,
            utc(2026, 9, 30, 13),
        )


def test_future_exact_forecast_checks_integrity_before_cutoff(repo):
    node = forecast(
        "future-corrupted",
        as_of=utc(2026, 9, 30, 13),
    )
    repo.add_forecast(node)

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
        match="IDM-R575: FORECAST_INTEGRITY_FAILURE",
    ):
        repo.forecast_at(
            node.id,
            utc(2026, 9, 30, 12),
        )



def test_database_rejects_wrong_forecast_anchor_type(repo):
    node = forecast("wrong-anchor-type")
    repo.add_forecast(node)

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE domain_nodes
                    SET node_type = 'Claim'
                    WHERE id = %s
                    """,
                    (node.id,),
                )

    assert exc_info.value.__class__.__name__ == "ForeignKeyViolation"


def test_database_rejects_wrong_forecast_projection_type(repo):
    node = forecast("wrong-projection-type")
    repo.add_forecast(node)

    with pytest.raises(Exception) as exc_info:
        with repo.connect() as con:
            with con.transaction():
                con.execute(
                    """
                    UPDATE forecast_facts
                    SET node_type = 'Claim'
                    WHERE node_id = %s
                    """,
                    (node.id,),
                )

    assert exc_info.value.__class__.__name__ == "CheckViolation"


def test_latest_forecast_at_fails_closed_on_same_time_ambiguity(repo):
    subject_id = "security:AMBIGUOUS"
    as_of = utc(2026, 9, 30, 11)

    first = forecast(
        "ambiguity-v1",
        subject_id=subject_id,
        as_of=as_of,
    )
    second = forecast(
        "ambiguity-v2",
        subject_id=subject_id,
        as_of=as_of,
    )

    assert first.id != second.id
    assert first.model_version != second.model_version

    repo.add_forecast(first)
    repo.add_forecast(second)

    with pytest.raises(
        RepositoryReadError,
        match="IDM-R577: FORECAST_PIT_AMBIGUITY",
    ):
        repo.latest_forecast_at(
            subject_id,
            "BASE",
            utc(2026, 9, 30, 12),
        )


def test_latest_forecast_at_does_not_integrity_traverse_future_row(repo):
    subject_id = "security:NO-LOOKAHEAD"

    visible = forecast(
        "visible-good",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    future = forecast(
        "future-corrupted-latest",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 13),
    )

    repo.add_forecast(visible)
    repo.add_forecast(future)

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

    assert repo.latest_forecast_at(
        subject_id,
        "BASE",
        utc(2026, 9, 30, 12),
    ) == visible


def test_latest_forecast_at_fails_closed_on_selected_visible_corruption(repo):
    subject_id = "security:VISIBLE-CORRUPTED"

    node = forecast(
        "visible-corrupted",
        subject_id=subject_id,
        as_of=utc(2026, 9, 30, 11),
    )
    repo.add_forecast(node)

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
        match="IDM-R575: FORECAST_INTEGRITY_FAILURE",
    ):
        repo.latest_forecast_at(
            subject_id,
            "BASE",
            utc(2026, 9, 30, 12),
        )

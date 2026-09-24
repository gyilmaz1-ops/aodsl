import pytest

from investment_domain import (
    PostgreSQLEvidenceRepository,
    RepositoryReadError,
)


def test_repository_read_error_is_runtime_error():
    assert issubclass(RepositoryReadError, RuntimeError)


def test_naive_cutoff_rejected_before_database(monkeypatch):
    from datetime import datetime

    repo = PostgreSQLEvidenceRepository(
        "postgresql://unused"
    )

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.evidence_for_claim_at(
            "claim:anything",
            datetime(2026, 9, 1, 12),
        )


def test_non_claim_identifier_rejected_before_database(
    monkeypatch,
):
    from datetime import datetime, timezone

    repo = PostgreSQLEvidenceRepository(
        "postgresql://unused"
    )

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="claim_id must reference Claim",
    ):
        repo.evidence_for_claim_at(
            "evidence:not-a-claim",
            datetime(
                2026, 9, 1, 12,
                tzinfo=timezone.utc,
            ),
        )


def test_metric_naive_cutoff_rejected_before_database(monkeypatch):
    from datetime import datetime

    repo = PostgreSQLEvidenceRepository(
        "postgresql://unused"
    )

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="research_cutoff must be timezone-aware",
    ):
        repo.evidence_for_metric_at(
            "metric:anything",
            datetime(2026, 9, 1, 12),
        )


def test_non_metric_identifier_rejected_before_database(
    monkeypatch,
):
    from datetime import datetime, timezone

    repo = PostgreSQLEvidenceRepository(
        "postgresql://unused"
    )

    def forbidden_connect():
        raise AssertionError("database must not be touched")

    monkeypatch.setattr(repo, "connect", forbidden_connect)

    with pytest.raises(
        ValueError,
        match="metric_id must reference Metric",
    ):
        repo.evidence_for_metric_at(
            "evidence:not-a-metric",
            datetime(
                2026, 9, 1, 12,
                tzinfo=timezone.utc,
            ),
        )


def test_repository_protocol_exposes_metric_pit_read():
    from investment_domain.repository import EvidenceRepository

    assert hasattr(
        EvidenceRepository,
        "evidence_for_metric_at",
    )

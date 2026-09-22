from investment_domain import (
    EvidenceRepository,
    PostgreSQLEvidenceRepository,
    RepositoryWriteError,
)


def test_postgres_repository_is_runtime_visible():
    assert PostgreSQLEvidenceRepository.__name__ == (
        "PostgreSQLEvidenceRepository"
    )
    assert issubclass(RepositoryWriteError, RuntimeError)


def test_postgres_repository_exposes_repository_contract():
    expected = {
        "add_claim",
        "add_evidence",
        "add_claim_evidence_link",
        "evidence_for_claim_at",
    }

    assert expected <= set(PostgreSQLEvidenceRepository.__dict__)


def test_postgres_repository_protocol_is_structural():
    # Protocol intentionally remains persistence-independent.
    assert EvidenceRepository.__name__ == "EvidenceRepository"


def test_postgres_repository_rejects_empty_dsn():
    for value in ("", "   "):
        try:
            PostgreSQLEvidenceRepository(value)
        except ValueError as exc:
            assert "dsn must not be empty" in str(exc)
        else:
            raise AssertionError("empty DSN must fail closed")

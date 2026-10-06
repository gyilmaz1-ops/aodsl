from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from investment_domain import PostgreSQLEvidenceRepository
from investment_domain.ingestion import ExternalObservation
from investment_domain.postgres_migrations import PostgreSQLMigrationManager
from investment_domain.sec_edgar import SecFilingRequest
from investment_domain.sec_edgar_discovery import SecFilingSelector
from investment_domain.sec_edgar_ingestion import SecEdgarIngestionService


UTC = timezone.utc
DSN = os.environ.get("IDM_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="IDM_POSTGRES_DSN is not configured",
)


def connect():
    import psycopg

    return psycopg.connect(DSN)


@pytest.fixture(autouse=True)
def clean_database():
    PostgreSQLMigrationManager(DSN).migrate()

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM evidence_facts")
        con.execute(
            "DELETE FROM domain_nodes WHERE node_type = 'Evidence'"
        )
        con.commit()

    yield

    with connect() as con:
        con.execute("DELETE FROM domain_edges")
        con.execute("DELETE FROM evidence_facts")
        con.execute(
            "DELETE FROM domain_nodes WHERE node_type = 'Evidence'"
        )
        con.commit()


class Resolver:
    def __init__(self, request):
        self.request = request

    def resolve(self, selector):
        return self.request


class Provider:
    def __init__(self, observation):
        self.observation = observation

    def fetch_filing(self, request):
        return (self.observation,)


def test_sec_filing_round_trip_and_replay_are_idempotent():
    request = SecFilingRequest(
        accession_number="0000320193-26-000001",
        primary_document="aapl-20260926.htm",
        cik="0000320193",
        form="10-Q",
        filing_date="2026-09-26",
        effective_at=datetime(2026, 9, 26, tzinfo=UTC),
        acceptance_datetime=datetime(
            2026, 9, 26, 20, 15, tzinfo=UTC
        ),
    )

    observation = ExternalObservation(
        source_id="sec-edgar:10-q:0000320193-26-000001",
        source_version="sec-edgar-v1",
        raw_content="<html>deterministic SEC filing</html>",
        effective_at=request.effective_at,
        observed_at=request.acceptance_datetime,
        published_at=request.acceptance_datetime,
        ingested_at=datetime(2026, 9, 26, 20, 16, tzinfo=UTC),
        source_uri=(
            "https:" + "//www.sec.gov/Archives/edgar/data/"
            "320193/000032019326000001/aapl-20260926.htm"
        ),
    )

    repository = PostgreSQLEvidenceRepository(DSN)
    service = SecEdgarIngestionService(
        resolver=Resolver(request),
        provider=Provider(observation),
        repository=repository,
    )

    selector = SecFilingSelector(
        cik="0000320193",
        form="10-Q",
        effective_at=datetime(2026, 9, 26, tzinfo=UTC),
    )

    first = service.execute(selector)

    assert repository.evidence_at(
        first.id,
        datetime(2026, 9, 26, 20, 16, tzinfo=UTC),
    ) == first

    second = service.execute(selector)

    assert second == first
    assert second.id == first.id

    assert repository.evidence_at(
        second.id,
        datetime(2026, 9, 26, 20, 16, tzinfo=UTC),
    ) == first

    with connect() as con:
        node_count = con.execute(
            """
            SELECT COUNT(*)
            FROM domain_nodes
            WHERE id = %s
            """,
            (first.id,),
        ).fetchone()[0]

        fact_count = con.execute(
            """
            SELECT COUNT(*)
            FROM evidence_facts
            WHERE node_id = %s
            """,
            (first.id,),
        ).fetchone()[0]

    assert node_count == 1
    assert fact_count == 1

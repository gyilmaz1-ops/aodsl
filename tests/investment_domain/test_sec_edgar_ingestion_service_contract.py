from datetime import datetime, timezone

import pytest

from investment_domain.ingestion import ExternalObservation
from investment_domain.sec_edgar import SecFilingRequest
from investment_domain.sec_edgar_discovery import SecFilingSelector
from investment_domain.sec_edgar_ingestion import SecEdgarIngestionService


UTC = timezone.utc
EFFECTIVE_AT = datetime(2025, 12, 31, tzinfo=UTC)
ACCEPTED_AT = datetime(2026, 2, 25, 21, 4, 5, tzinfo=UTC)
INGESTED_AT = datetime(2026, 2, 26, 8, tzinfo=UTC)


def selector():
    return SecFilingSelector(
        cik="320193",
        form="10-K",
        effective_at=EFFECTIVE_AT,
    )


def request():
    return SecFilingRequest(
        accession_number="0000320193-26-000012",
        primary_document="aapl-20260225.htm",
        cik="0000320193",
        form="10-K",
        filing_date=datetime(2026, 2, 25, tzinfo=UTC),
        effective_at=EFFECTIVE_AT,
        acceptance_datetime=ACCEPTED_AT,
    )


def observation():
    return ExternalObservation(
        source_id="sec-edgar:10-k:0000320193-26-000012",
        source_version="sec-edgar-v1",
        raw_content="<html>filing</html>",
        effective_at=EFFECTIVE_AT,
        observed_at=ACCEPTED_AT,
        published_at=ACCEPTED_AT,
        ingested_at=INGESTED_AT,
        source_uri="https://example.test/filing",
    )


class Resolver:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def resolve(self, value):
        self.calls.append(value)
        if self.error is not None:
            raise self.error
        return self.result


class Provider:
    def __init__(self, result=(), error=None):
        self.result = result
        self.error = error
        self.calls = []

    def fetch_filing(self, value):
        self.calls.append(value)
        if self.error is not None:
            raise self.error
        return self.result


class Repository:
    def __init__(self):
        self.items = []

    def add_evidence(self, evidence):
        self.items.append(evidence)


def service(resolver, provider, repository):
    return SecEdgarIngestionService(
        resolver=resolver,
        provider=provider,
        repository=repository,
    )


def test_execute_resolves_fetches_normalizes_and_persists_one_filing():
    resolved = request()
    obs = observation()
    resolver = Resolver(result=resolved)
    provider = Provider(result=(obs,))
    repository = Repository()

    evidence = service(
        resolver,
        provider,
        repository,
    ).execute(selector())

    assert resolver.calls == [selector()]
    assert provider.calls == [resolved]
    assert repository.items == [evidence]
    assert evidence.source_id == obs.source_id
    assert evidence.source_version == obs.source_version


def test_resolver_failure_does_not_fetch_or_persist():
    resolver = Resolver(error=LookupError("missing filing"))
    provider = Provider()
    repository = Repository()

    with pytest.raises(LookupError, match="missing filing"):
        service(
            resolver,
            provider,
            repository,
        ).execute(selector())

    assert provider.calls == []
    assert repository.items == []


def test_provider_failure_does_not_persist():
    resolver = Resolver(result=request())
    provider = Provider(error=RuntimeError("fetch failed"))
    repository = Repository()

    with pytest.raises(RuntimeError, match="fetch failed"):
        service(
            resolver,
            provider,
            repository,
        ).execute(selector())

    assert repository.items == []


@pytest.mark.parametrize("observations", [(), (observation(), observation())])
def test_execute_requires_exactly_one_observation(observations):
    resolver = Resolver(result=request())
    provider = Provider(result=observations)
    repository = Repository()

    with pytest.raises(
        ValueError,
        match="SEC filing fetch must return exactly one observation",
    ):
        service(
            resolver,
            provider,
            repository,
        ).execute(selector())

    assert repository.items == []

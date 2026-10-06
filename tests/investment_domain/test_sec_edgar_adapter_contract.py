from __future__ import annotations

from datetime import datetime, timezone

import pytest

from investment_domain.ingestion import normalize_external_observation
from investment_domain.validation import DomainValidationError
from investment_domain.sec_edgar import (
    HttpResponse,
    SecEdgarProvider,
    SecFilingRequest,
)


UTC = timezone.utc

FILING_DATE = datetime(2026, 2, 25, 0, 0, tzinfo=UTC)
EFFECTIVE_AT = datetime(2025, 12, 31, 0, 0, tzinfo=UTC)
ACCEPTED_AT = datetime(2026, 2, 25, 21, 4, 5, tzinfo=UTC)
INGESTED_AT = datetime(2026, 2, 26, 8, 0, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(
        self,
        response: HttpResponse,
    ) -> None:
        self.response = response
        self.calls = []

    def get(self, url, *, headers):
        self.calls.append((url, dict(headers)))
        return self.response


def filing_request(**changes):
    values = {
        "accession_number": "0000320193-26-000012",
        "primary_document": "aapl-20260225.htm",
        "cik": "0000320193",
        "form": "10-K",
        "filing_date": FILING_DATE,
        "effective_at": EFFECTIVE_AT,
        "acceptance_datetime": ACCEPTED_AT,
    }
    values.update(changes)
    return SecFilingRequest(**values)


def provider(
    *,
    body=b"<html>filing</html>",
    status_code=200,
):
    transport = RecordingTransport(
        HttpResponse(
            status_code=status_code,
            body=body,
            headers={"Content-Type": "text/html"},
        )
    )
    adapter = SecEdgarProvider(
        transport,
        user_agent="AODSL research@example.test",
        clock=lambda: INGESTED_AT,
    )
    return adapter, transport


def test_fetch_filing_uses_canonical_sec_archive_url():
    adapter, transport = provider()

    observations = adapter.fetch_filing(filing_request())

    assert len(observations) == 1
    assert transport.calls == [
        (
            "https://www.sec.gov/Archives/edgar/data/"
            "320193/000032019326000012/aapl-20260225.htm",
            {
                "User-Agent": "AODSL research@example.test",
                "Accept-Encoding": "gzip, deflate",
            },
        )
    ]


def test_fetch_filing_maps_sec_provenance_and_timestamps():
    adapter, _ = provider()

    observation = adapter.fetch_filing(filing_request())[0]

    assert observation.source_id == (
        "sec-edgar:10-k:0000320193-26-000012"
    )
    assert observation.source_version == "sec-edgar-v1"
    assert observation.raw_content == "<html>filing</html>"
    assert observation.effective_at == EFFECTIVE_AT
    assert observation.effective_at != FILING_DATE
    assert observation.observed_at == ACCEPTED_AT
    assert observation.published_at == ACCEPTED_AT
    assert observation.ingested_at == INGESTED_AT
    assert observation.source_uri == (
        "https://www.sec.gov/Archives/edgar/data/"
        "320193/000032019326000012/aapl-20260225.htm"
    )


def test_same_filing_is_deterministic_through_tj3_normalization():
    adapter_a, _ = provider()
    adapter_b, _ = provider()

    evidence_a = normalize_external_observation(
        adapter_a.fetch_filing(filing_request())[0]
    )
    evidence_b = normalize_external_observation(
        adapter_b.fetch_filing(filing_request())[0]
    )

    assert evidence_a == evidence_b
    assert evidence_a.id.startswith("evidence:")
    assert len(evidence_a.id) == len("evidence:") + 64


def test_transport_failure_fails_closed():
    adapter, _ = provider(status_code=503)

    with pytest.raises(
        RuntimeError,
        match="SEC EDGAR request failed with HTTP 503",
    ):
        adapter.fetch_filing(filing_request())


def test_non_utf8_payload_fails_closed():
    adapter, _ = provider(body=b"\xff")

    with pytest.raises(
        ValueError,
        match="SEC EDGAR response body must be UTF-8",
    ):
        adapter.fetch_filing(filing_request())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("cik", "ABC", "cik must contain only digits"),
        (
            "accession_number",
            "bad-accession",
            "accession_number must use SEC accession format",
        ),
        (
            "primary_document",
            "../secret.htm",
            "primary_document must be a single path segment",
        ),
    ],
)
def test_invalid_request_identity_fails_before_transport(
    field,
    value,
    message,
):
    adapter, transport = provider()

    with pytest.raises(ValueError, match=message):
        adapter.fetch_filing(
            filing_request(**{field: value})
        )

    assert transport.calls == []


def test_empty_user_agent_fails_closed():
    transport = RecordingTransport(
        HttpResponse(
            status_code=200,
            body=b"x",
            headers={},
        )
    )

    with pytest.raises(
        ValueError,
        match="user_agent must not be empty",
    ):
        SecEdgarProvider(
            transport,
            user_agent=" ",
            clock=lambda: INGESTED_AT,
        )


def test_naive_adapter_clock_is_rejected_by_existing_domain_contract():
    naive = datetime(2026, 2, 26, 8, 0, 0)

    transport = RecordingTransport(
        HttpResponse(
            status_code=200,
            body=b"<html>filing</html>",
            headers={},
        )
    )

    adapter = SecEdgarProvider(
        transport,
        user_agent="AODSL research@example.test",
        clock=lambda: naive,
    )

    observation = adapter.fetch_filing(filing_request())

    with pytest.raises(
        DomainValidationError,
        match=r"IDM-C004: ingested_at must be timezone-aware",
    ):
        normalize_external_observation(observation[0])


def test_empty_form_fails_before_transport():
    adapter, transport = provider()

    with pytest.raises(
        ValueError,
        match="form must not be empty",
    ):
        adapter.fetch_filing(
            filing_request(form=" ")
        )

    assert transport.calls == []


def test_form_is_canonicalized_for_source_identity():
    adapter, _ = provider()

    observation = adapter.fetch_filing(
        filing_request(form=" 10-K ")
    )[0]

    assert observation.source_id == (
        "sec-edgar:10-k:0000320193-26-000012"
    )

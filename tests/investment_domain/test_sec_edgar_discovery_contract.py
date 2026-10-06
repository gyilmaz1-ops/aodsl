from __future__ import annotations

from datetime import datetime, timezone

import pytest

from investment_domain.sec_edgar import HttpResponse
from investment_domain.sec_edgar_discovery import (
    SecEdgarFilingResolver,
    SecFilingSelector,
)


UTC = timezone.utc
EFFECTIVE_AT = datetime(2025, 12, 31, 0, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls = []

    def get(self, url, *, headers):
        self.calls.append((url, dict(headers)))
        return self.response


def resolver(*, body=b"{}", status_code=200):
    transport = RecordingTransport(
        HttpResponse(
            status_code=status_code,
            body=body,
            headers={"Content-Type": "application/json"},
        )
    )
    instance = SecEdgarFilingResolver(
        transport,
        user_agent="AODSL research@example.test",
    )
    return instance, transport


def selector(**changes):
    values = {
        "cik": "320193",
        "form": "10-K",
        "effective_at": EFFECTIVE_AT,
    }
    values.update(changes)
    return SecFilingSelector(**values)


def test_cik_is_canonicalized_to_ten_digits():
    instance, _ = resolver()

    assert instance._cik(" 320193 ") == "0000320193"


def test_form_is_canonicalized_for_metadata_matching():
    instance, _ = resolver()

    assert instance._form(" 10-k ") == "10-K"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("", "cik must contain only digits"),
        ("ABC", "cik must contain only digits"),
        ("12345678901", "cik must contain at most 10 digits"),
    ],
)
def test_invalid_cik_fails_closed(value, message):
    instance, _ = resolver()

    with pytest.raises(ValueError, match=message):
        instance._cik(value)


def test_empty_form_fails_closed():
    instance, _ = resolver()

    with pytest.raises(ValueError, match="form must not be empty"):
        instance._form(" ")


def test_non_utf8_submissions_payload_fails_closed():
    instance, _ = resolver()

    with pytest.raises(
        ValueError,
        match="SEC submissions response body must be UTF-8",
    ):
        instance._parse_json(b"\xff")


def test_empty_submissions_payload_fails_closed():
    instance, _ = resolver()

    with pytest.raises(
        ValueError,
        match="SEC submissions response body must not be empty",
    ):
        instance._parse_json(b"")


def test_malformed_submissions_json_fails_closed():
    instance, _ = resolver()

    with pytest.raises(
        ValueError,
        match="SEC submissions response body must be valid JSON",
    ):
        instance._parse_json(b"{bad json")


def test_non_object_submissions_json_fails_closed():
    instance, _ = resolver()

    with pytest.raises(
        ValueError,
        match="SEC submissions response must be a JSON object",
    ):
        instance._parse_json(b"[]")


def test_empty_user_agent_fails_closed():
    transport = RecordingTransport(
        HttpResponse(
            status_code=200,
            body=b"{}",
            headers={},
        )
    )

    with pytest.raises(
        ValueError,
        match="user_agent must not be empty",
    ):
        SecEdgarFilingResolver(
            transport,
            user_agent=" ",
        )


def submissions_payload(
    *,
    accession_numbers=None,
    filing_dates=None,
    acceptance_datetimes=None,
    forms=None,
    primary_documents=None,
):
    import json

    recent = {
        "accessionNumber": accession_numbers
        or [
            "0000320193-26-000010",
            "0000320193-26-000012",
            "0000320193-25-000009",
        ],
        "filingDate": filing_dates
        or [
            "2026-02-20",
            "2026-02-25",
            "2025-11-01",
        ],
        "acceptanceDateTime": acceptance_datetimes
        or [
            "2026-02-20T20:00:00.000Z",
            "2026-02-25T21:04:05.000Z",
            "2025-11-01T18:00:00.000Z",
        ],
        "form": forms
        or [
            "10-K",
            "10-K",
            "8-K",
        ],
        "primaryDocument": primary_documents
        or [
            "aapl-old.htm",
            "aapl-20260225.htm",
            "aapl-8k.htm",
        ],
    }

    return json.dumps(
        {"filings": {"recent": recent}}
    ).encode("utf-8")


def test_resolve_fetches_canonical_submissions_url_and_headers():
    instance, transport = resolver(
        body=submissions_payload()
    )

    instance.resolve(selector())

    assert transport.calls == [
        (
            "https://data.sec.gov/submissions/CIK0000320193.json",
            {
                "User-Agent": "AODSL research@example.test",
                "Accept-Encoding": "gzip, deflate",
            },
        )
    ]


def test_resolve_selects_latest_matching_form_deterministically():
    instance, _ = resolver(
        body=submissions_payload()
    )

    request = instance.resolve(
        selector(form=" 10-k ")
    )

    assert request.accession_number == "0000320193-26-000012"
    assert request.primary_document == "aapl-20260225.htm"
    assert request.cik == "0000320193"
    assert request.form == "10-K"
    assert request.filing_date == datetime(
        2026,
        2,
        25,
        tzinfo=UTC,
    )
    assert request.acceptance_datetime == datetime(
        2026,
        2,
        25,
        21,
        4,
        5,
        tzinfo=UTC,
    )


def test_resolve_preserves_explicit_economic_effective_at():
    instance, _ = resolver(
        body=submissions_payload()
    )

    request = instance.resolve(selector())

    assert request.effective_at == EFFECTIVE_AT
    assert request.effective_at != request.filing_date


def test_metadata_order_does_not_control_latest_selection():
    instance, _ = resolver(
        body=submissions_payload(
            accession_numbers=[
                "0000320193-26-000012",
                "0000320193-26-000010",
            ],
            filing_dates=[
                "2026-02-25",
                "2026-02-20",
            ],
            acceptance_datetimes=[
                "2026-02-25T21:04:05.000Z",
                "2026-02-20T20:00:00.000Z",
            ],
            forms=[
                "10-K",
                "10-K",
            ],
            primary_documents=[
                "latest.htm",
                "older.htm",
            ],
        )
    )

    request = instance.resolve(selector())

    assert request.accession_number == "0000320193-26-000012"
    assert request.primary_document == "latest.htm"


def test_no_matching_form_fails_closed():
    instance, _ = resolver(
        body=submissions_payload(
            forms=["8-K", "8-K", "8-K"],
        )
    )

    with pytest.raises(
        LookupError,
        match="no SEC filing found for form 10-K",
    ):
        instance.resolve(selector())


def test_http_failure_fails_closed():
    instance, _ = resolver(
        body=submissions_payload(),
        status_code=503,
    )

    with pytest.raises(
        RuntimeError,
        match="SEC submissions request failed with HTTP 503",
    ):
        instance.resolve(selector())


def test_missing_filings_object_fails_closed():
    instance, _ = resolver(body=b"{}")

    with pytest.raises(
        ValueError,
        match="SEC submissions filings must be a JSON object",
    ):
        instance.resolve(selector())


def test_missing_recent_object_fails_closed():
    instance, _ = resolver(
        body=b'{"filings": {}}'
    )

    with pytest.raises(
        ValueError,
        match="filings.recent must be a JSON object",
    ):
        instance.resolve(selector())


def test_parallel_array_length_mismatch_fails_closed():
    instance, _ = resolver(
        body=submissions_payload(
            primary_documents=["only-one.htm"],
        )
    )

    with pytest.raises(
        ValueError,
        match="recent filing arrays must have equal length",
    ):
        instance.resolve(selector())


def test_invalid_filing_date_fails_closed():
    instance, _ = resolver(
        body=submissions_payload(
            filing_dates=[
                "bad-date",
                "2026-02-25",
                "2025-11-01",
            ],
        )
    )

    with pytest.raises(
        ValueError,
        match="filingDate must use YYYY-MM-DD",
    ):
        instance.resolve(selector())


def test_invalid_acceptance_datetime_fails_closed():
    instance, _ = resolver(
        body=submissions_payload(
            acceptance_datetimes=[
                "bad-time",
                "2026-02-25T21:04:05.000Z",
                "2025-11-01T18:00:00.000Z",
            ],
        )
    )

    with pytest.raises(
        ValueError,
        match="acceptanceDateTime must be UTC ISO-8601",
    ):
        instance.resolve(selector())


def test_distinct_filings_with_same_latest_acceptance_time_fail_closed():
    instance, _ = resolver(
        body=submissions_payload(
            accession_numbers=[
                "0000320193-26-000012",
                "0000320193-26-000013",
            ],
            filing_dates=[
                "2026-02-25",
                "2026-02-25",
            ],
            acceptance_datetimes=[
                "2026-02-25T21:04:05.000Z",
                "2026-02-25T21:04:05.000Z",
            ],
            forms=[
                "10-K",
                "10-K",
            ],
            primary_documents=[
                "first.htm",
                "second.htm",
            ],
        )
    )

    with pytest.raises(
        ValueError,
        match=(
            "ambiguous SEC filings share latest "
            "acceptanceDateTime"
        ),
    ):
        instance.resolve(selector())


def test_exact_duplicate_latest_filing_rows_resolve_to_one_identity():
    instance, _ = resolver(
        body=submissions_payload(
            accession_numbers=[
                "0000320193-26-000012",
                "0000320193-26-000012",
            ],
            filing_dates=[
                "2026-02-25",
                "2026-02-25",
            ],
            acceptance_datetimes=[
                "2026-02-25T21:04:05.000Z",
                "2026-02-25T21:04:05.000Z",
            ],
            forms=[
                "10-K",
                "10-K",
            ],
            primary_documents=[
                "same.htm",
                "same.htm",
            ],
        )
    )

    request = instance.resolve(selector())

    assert request.accession_number == "0000320193-26-000012"
    assert request.primary_document == "same.htm"
    assert request.filing_date == datetime(
        2026,
        2,
        25,
        tzinfo=UTC,
    )
    assert request.acceptance_datetime == datetime(
        2026,
        2,
        25,
        21,
        4,
        5,
        tzinfo=UTC,
    )
    assert request.effective_at == EFFECTIVE_AT


def test_older_same_timestamp_ambiguity_does_not_block_unique_latest_filing():
    instance, _ = resolver(
        body=submissions_payload(
            accession_numbers=[
                "0000320193-26-000020",
                "0000320193-26-000010",
                "0000320193-26-000011",
            ],
            filing_dates=[
                "2026-03-01",
                "2026-02-20",
                "2026-02-20",
            ],
            acceptance_datetimes=[
                "2026-03-01T20:00:00.000Z",
                "2026-02-20T20:00:00.000Z",
                "2026-02-20T20:00:00.000Z",
            ],
            forms=[
                "10-K",
                "10-K",
                "10-K",
            ],
            primary_documents=[
                "latest.htm",
                "older-a.htm",
                "older-b.htm",
            ],
        )
    )

    request = instance.resolve(selector())

    assert request.accession_number == "0000320193-26-000020"
    assert request.primary_document == "latest.htm"

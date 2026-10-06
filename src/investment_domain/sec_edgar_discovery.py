from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Mapping

from .sec_edgar import HttpTransport, SecFilingRequest


@dataclass(frozen=True)
class SecFilingSelector:
    cik: str
    form: str
    effective_at: datetime


class SecEdgarFilingResolver:
    """
    Resolve one filing from SEC submissions metadata into SecFilingRequest.

    This boundary performs deterministic metadata resolution only.

    It does not:
    - fetch the filing document,
    - infer economic effective_at,
    - retry requests,
    - persist results,
    - use wall-clock time.
    """

    SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        user_agent: str,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must not be empty")

        self._transport = transport
        self._user_agent = user_agent

    @staticmethod
    def _cik(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("cik must be str")

        normalized = value.strip()

        if not normalized or not normalized.isdigit():
            raise ValueError("cik must contain only digits")

        if len(normalized) > 10:
            raise ValueError("cik must contain at most 10 digits")

        return normalized.zfill(10)

    @staticmethod
    def _form(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("form must be str")

        normalized = value.strip().upper()

        if not normalized:
            raise ValueError("form must not be empty")

        return normalized

    @staticmethod
    def _parse_json(body: bytes) -> Mapping[str, object]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "SEC submissions response body must be UTF-8"
            ) from exc

        if not text:
            raise ValueError(
                "SEC submissions response body must not be empty"
            )

        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "SEC submissions response body must be valid JSON"
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError(
                "SEC submissions response must be a JSON object"
            )

        return payload

    @staticmethod
    def _recent_columns(
        payload: Mapping[str, object],
    ) -> tuple[
        list[object],
        list[object],
        list[object],
        list[object],
        list[object],
    ]:
        filings = payload.get("filings")

        if not isinstance(filings, dict):
            raise ValueError(
                "SEC submissions filings must be a JSON object"
            )

        recent = filings.get("recent")

        if not isinstance(recent, dict):
            raise ValueError(
                "SEC submissions filings.recent must be a JSON object"
            )

        names = (
            "accessionNumber",
            "filingDate",
            "acceptanceDateTime",
            "form",
            "primaryDocument",
        )

        columns = []

        for name in names:
            column = recent.get(name)

            if not isinstance(column, list):
                raise ValueError(
                    f"SEC submissions filings.recent.{name} "
                    "must be an array"
                )

            columns.append(column)

        lengths = {len(column) for column in columns}

        if len(lengths) != 1:
            raise ValueError(
                "SEC submissions recent filing arrays "
                "must have equal length"
            )

        return tuple(columns)

    @staticmethod
    def _required_string(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"SEC submissions {field} must be a non-empty string"
            )

        return value.strip()

    @staticmethod
    def _filing_date(value: str) -> datetime:
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(
                "SEC submissions filingDate must use YYYY-MM-DD"
            ) from exc

        return parsed.replace(tzinfo=timezone.utc)

    @staticmethod
    def _acceptance_datetime(value: str) -> datetime:
        try:
            parsed = datetime.strptime(
                value,
                "%Y-%m-%dT%H:%M:%S.%fZ",
            )
        except ValueError:
            try:
                parsed = datetime.strptime(
                    value,
                    "%Y-%m-%dT%H:%M:%SZ",
                )
            except ValueError as exc:
                raise ValueError(
                    "SEC submissions acceptanceDateTime "
                    "must be UTC ISO-8601"
                ) from exc

        return parsed.replace(tzinfo=timezone.utc)

    def resolve(
        self,
        selector: SecFilingSelector,
    ) -> SecFilingRequest:
        cik = self._cik(selector.cik)
        requested_form = self._form(selector.form)

        url = f"{self.SUBMISSIONS_BASE_URL}/CIK{cik}.json"

        response = self._transport.get(
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                "SEC submissions request failed with HTTP "
                f"{response.status_code}"
            )

        payload = self._parse_json(response.body)

        (
            accession_numbers,
            filing_dates,
            acceptance_datetimes,
            forms,
            primary_documents,
        ) = self._recent_columns(payload)

        candidates = []

        for index in range(len(forms)):
            form = self._required_string(
                forms[index],
                "form",
            )

            if self._form(form) != requested_form:
                continue

            accession_number = self._required_string(
                accession_numbers[index],
                "accessionNumber",
            )
            primary_document = self._required_string(
                primary_documents[index],
                "primaryDocument",
            )
            filing_date = self._filing_date(
                self._required_string(
                    filing_dates[index],
                    "filingDate",
                )
            )
            acceptance_datetime = self._acceptance_datetime(
                self._required_string(
                    acceptance_datetimes[index],
                    "acceptanceDateTime",
                )
            )

            candidates.append(
                (
                    acceptance_datetime,
                    accession_number,
                    primary_document,
                    filing_date,
                )
            )

        if not candidates:
            raise LookupError(
                f"no SEC filing found for form {requested_form}"
            )

        latest_acceptance_datetime = max(
            candidate[0]
            for candidate in candidates
        )

        latest_candidates = [
            candidate
            for candidate in candidates
            if candidate[0] == latest_acceptance_datetime
        ]

        latest_identities = {
            (
                candidate[1],
                candidate[2],
                candidate[3],
            )
            for candidate in latest_candidates
        }

        if len(latest_identities) != 1:
            raise ValueError(
                "ambiguous SEC filings share latest "
                "acceptanceDateTime"
            )

        (
            accession_number,
            primary_document,
            filing_date,
        ) = next(iter(latest_identities))

        acceptance_datetime = latest_acceptance_datetime

        return SecFilingRequest(
            accession_number=accession_number,
            primary_document=primary_document,
            cik=cik,
            form=requested_form,
            filing_date=filing_date,
            effective_at=selector.effective_at,
            acceptance_datetime=acceptance_datetime,
        )

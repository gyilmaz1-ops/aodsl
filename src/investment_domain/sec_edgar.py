from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol

from .ingestion import ExternalObservation


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HttpTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> HttpResponse:
        ...


@dataclass(frozen=True)
class SecFilingRequest:
    accession_number: str
    primary_document: str
    cik: str
    form: str
    filing_date: datetime
    effective_at: datetime
    acceptance_datetime: datetime
    source_version: str = "sec-edgar-v1"


class SecEdgarProvider:
    """
    Provider adapter for one already-resolved SEC EDGAR filing document.

    Discovery, retry/backoff, persistence, and orchestration are deliberately
    outside this adapter contract.
    """

    BASE_URL = "https://www.sec.gov/Archives/edgar/data"

    def __init__(
        self,
        transport: HttpTransport,
        *,
        user_agent: str,
        clock,
    ) -> None:
        if not isinstance(user_agent, str) or not user_agent.strip():
            raise ValueError("user_agent must not be empty")

        if not callable(clock):
            raise TypeError("clock must be callable")

        self._transport = transport
        self._user_agent = user_agent
        self._clock = clock

    @staticmethod
    def _digits(value: str, name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{name} must be str")

        normalized = value.strip()

        if not normalized or not normalized.isdigit():
            raise ValueError(f"{name} must contain only digits")

        return normalized

    @staticmethod
    def _form(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("form must be str")

        normalized = value.strip().lower()

        if not normalized:
            raise ValueError("form must not be empty")

        return normalized

    @staticmethod
    def _accession_path(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("accession_number must be str")

        normalized = value.strip()
        compact = normalized.replace("-", "")

        if (
            len(normalized) != 20
            or len(compact) != 18
            or normalized[10] != "-"
            or normalized[13] != "-"
            or not compact.isdigit()
        ):
            raise ValueError(
                "accession_number must use SEC accession format "
                "##########-##-######"
            )

        return compact

    @staticmethod
    def _primary_document(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("primary_document must be str")

        normalized = value.strip()

        if (
            not normalized
            or normalized in {".", ".."}
            or "/" in normalized
            or "\\" in normalized
        ):
            raise ValueError("primary_document must be a single path segment")

        return normalized

    def fetch_filing(
        self,
        request: SecFilingRequest,
    ) -> tuple[ExternalObservation, ...]:
        cik = self._digits(request.cik, "cik")
        form = self._form(request.form)
        accession_path = self._accession_path(request.accession_number)
        primary_document = self._primary_document(request.primary_document)

        url = (
            f"{self.BASE_URL}/"
            f"{int(cik)}/"
            f"{accession_path}/"
            f"{primary_document}"
        )

        response = self._transport.get(
            url,
            headers={
                "User-Agent": self._user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
        )

        if response.status_code != 200:
            raise RuntimeError(
                f"SEC EDGAR request failed with HTTP "
                f"{response.status_code}"
            )

        try:
            raw_content = response.body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "SEC EDGAR response body must be UTF-8"
            ) from exc

        if not raw_content:
            raise ValueError("SEC EDGAR response body must not be empty")

        ingested_at = self._clock()

        observation = ExternalObservation(
            source_id=(
                f"sec-edgar:{form}:"
                f"{request.accession_number.strip()}"
            ),
            source_version=request.source_version,
            raw_content=raw_content,
            effective_at=request.effective_at,
            observed_at=request.acceptance_datetime,
            published_at=request.acceptance_datetime,
            ingested_at=ingested_at,
            source_uri=url,
        )

        return (observation,)

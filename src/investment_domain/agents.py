from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentContractValidationError(ValueError):
    """Raised when a specialist-agent boundary contract is invalid."""


class AgentCapability(Enum):
    BEAR_ANALYSIS = "BEAR_ANALYSIS"
    CALCULATION = "CALCULATION"
    FUNDAMENTAL_ANALYSIS = "FUNDAMENTAL_ANALYSIS"
    INDUSTRY_ANALYSIS = "INDUSTRY_ANALYSIS"
    NEWS_RESEARCH = "NEWS_RESEARCH"
    VALUATION_ANALYSIS = "VALUATION_ANALYSIS"
    VERIFICATION = "VERIFICATION"


SPECIALIST_AGENT_CAPABILITIES = tuple(
    sorted(AgentCapability, key=lambda capability: capability.name)
)


def _require_non_empty_string(
    value: Any,
    *,
    field_name: str,
) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AgentContractValidationError(
            f"{field_name} must be a non-empty string"
        )


def _require_capability(value: Any) -> None:
    if not isinstance(value, AgentCapability):
        raise AgentContractValidationError(
            "capability must be an AgentCapability"
        )


def _require_object_ids(
    value: Any,
    *,
    field_name: str,
) -> None:
    if not isinstance(value, tuple) or not value:
        raise AgentContractValidationError(
            f"{field_name} must be a non-empty tuple"
        )

    for object_id in value:
        _require_non_empty_string(
            object_id,
            field_name=f"{field_name} item",
        )

    if len(set(value)) != len(value):
        raise AgentContractValidationError(
            f"{field_name} must not contain duplicates"
        )


@dataclass(frozen=True)
class AgentRequest:
    request_id: str
    capability: AgentCapability
    subject_id: str
    as_of: str
    input_object_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_agent_request(self)


@dataclass(frozen=True)
class AgentResult:
    result_id: str
    request_id: str
    capability: AgentCapability
    output_object_ids: tuple[str, ...]
    provenance_object_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        validate_agent_result(self)


def validate_agent_request(
    request: AgentRequest,
) -> AgentRequest:
    if not isinstance(request, AgentRequest):
        raise AgentContractValidationError(
            "request must be an AgentRequest"
        )

    _require_non_empty_string(
        request.request_id,
        field_name="request_id",
    )
    _require_capability(request.capability)
    _require_non_empty_string(
        request.subject_id,
        field_name="subject_id",
    )
    _require_non_empty_string(
        request.as_of,
        field_name="as_of",
    )
    _require_object_ids(
        request.input_object_ids,
        field_name="input_object_ids",
    )

    return request


def validate_agent_result(
    result: AgentResult,
    *,
    request: AgentRequest | None = None,
) -> AgentResult:
    if not isinstance(result, AgentResult):
        raise AgentContractValidationError(
            "result must be an AgentResult"
        )

    _require_non_empty_string(
        result.result_id,
        field_name="result_id",
    )
    _require_non_empty_string(
        result.request_id,
        field_name="request_id",
    )
    _require_capability(result.capability)
    _require_object_ids(
        result.output_object_ids,
        field_name="output_object_ids",
    )
    _require_object_ids(
        result.provenance_object_ids,
        field_name="provenance_object_ids",
    )

    if request is not None:
        validate_agent_request(request)

        if result.request_id != request.request_id:
            raise AgentContractValidationError(
                "result request_id does not match request"
            )

        if result.capability is not request.capability:
            raise AgentContractValidationError(
                "result capability does not match request"
            )

    return result

from __future__ import annotations

import hashlib
import json
from typing import Final

from investment_domain.agents import (
    AgentCapability,
    AgentRequest,
    AgentResult,
    AgentContractValidationError,
    validate_agent_request,
    validate_agent_result,
)


class AgentOrchestrationValidationError(ValueError):
    """Raised when specialist-agent orchestration fails closed."""


RUNTIME_CAPABILITY_TO_AGENT_CAPABILITY: Final[
    dict[str, AgentCapability]
] = {
    "RESEARCH_SOURCE":
        AgentCapability.NEWS_RESEARCH,
    "CALCULATE_FINANCIAL_METRIC":
        AgentCapability.CALCULATION,
    "VERIFY_CLAIM":
        AgentCapability.VERIFICATION,
    "ADVERSARIAL_ANALYSIS":
        AgentCapability.BEAR_ANALYSIS,
}


def _require_non_empty_string(
    name: str,
    value: object,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AgentOrchestrationValidationError(
            f"{name} must be a non-empty string"
        )

    return value


def _map_runtime_capability(
    runtime_capability: object,
) -> AgentCapability:
    capability = _require_non_empty_string(
        "runtime_capability",
        runtime_capability,
    )

    try:
        return RUNTIME_CAPABILITY_TO_AGENT_CAPABILITY[
            capability
        ]
    except KeyError as exc:
        raise AgentOrchestrationValidationError(
            "unsupported runtime capability: "
            + capability
        ) from exc


def _deterministic_request_id(
    *,
    event_id: str,
    plan_hash: str,
    runtime_capability: str,
    subject_id: str,
    as_of: str,
    input_object_ids: tuple[str, ...],
) -> str:
    payload = {
        "as_of": as_of,
        "event_id": event_id,
        "input_object_ids": list(input_object_ids),
        "plan_hash": plan_hash,
        "runtime_capability": runtime_capability,
        "subject_id": subject_id,
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    digest = hashlib.sha256(canonical).hexdigest()

    return "agent-request:" + digest


def build_agent_request(
    *,
    event_id: str,
    plan_hash: str,
    runtime_capability: str,
    subject_id: str,
    as_of: str,
    input_object_ids: tuple[str, ...],
) -> AgentRequest:
    event_id = _require_non_empty_string(
        "event_id",
        event_id,
    )
    plan_hash = _require_non_empty_string(
        "plan_hash",
        plan_hash,
    )
    runtime_capability = _require_non_empty_string(
        "runtime_capability",
        runtime_capability,
    )
    subject_id = _require_non_empty_string(
        "subject_id",
        subject_id,
    )
    as_of = _require_non_empty_string(
        "as_of",
        as_of,
    )

    capability = _map_runtime_capability(
        runtime_capability
    )

    request_id = _deterministic_request_id(
        event_id=event_id,
        plan_hash=plan_hash,
        runtime_capability=runtime_capability,
        subject_id=subject_id,
        as_of=as_of,
        input_object_ids=input_object_ids,
    )

    try:
        request = AgentRequest(
            request_id=request_id,
            capability=capability,
            subject_id=subject_id,
            as_of=as_of,
            input_object_ids=input_object_ids,
        )

        return validate_agent_request(request)

    except AgentContractValidationError as exc:
        raise AgentOrchestrationValidationError(
            "invalid specialist-agent request"
        ) from exc


def validate_orchestrated_result(
    request: AgentRequest,
    result: AgentResult,
) -> AgentResult:
    try:
        validate_agent_request(request)

        validated = validate_agent_result(
            result,
            request=request,
        )

    except AgentContractValidationError as exc:
        raise AgentOrchestrationValidationError(
            "invalid specialist-agent result"
        ) from exc

    return validated

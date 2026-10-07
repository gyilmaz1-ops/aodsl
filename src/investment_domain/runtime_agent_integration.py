from __future__ import annotations

from typing import Any, Mapping

from aodsl.runtime import AODSLError, CapabilityRegistry
from investment_domain.agent_orchestration import (
    AgentOrchestrationValidationError,
    build_agent_request,
)
from investment_domain.agents import AgentRequest


class RuntimeAgentIntegrationError(ValueError):
    """Raised when runtime-to-specialist-agent integration fails closed."""


def _require_non_empty_string(
    dispatch: Mapping[str, Any],
    field_name: str,
) -> str:
    value = dispatch.get(field_name)

    if not isinstance(value, str) or not value.strip():
        raise RuntimeAgentIntegrationError(
            f"{field_name} must be a non-empty string"
        )

    return value


def _require_input_object_ids(
    dispatch: Mapping[str, Any],
) -> tuple[str, ...]:
    value = dispatch.get("input_object_ids")

    if not isinstance(value, tuple) or not value:
        raise RuntimeAgentIntegrationError(
            "input_object_ids must be a non-empty tuple"
        )

    return value


def build_agent_request_from_dispatch(
    dispatch: Mapping[str, Any],
    *,
    registry: CapabilityRegistry | None = None,
) -> AgentRequest:
    if not isinstance(dispatch, Mapping):
        raise RuntimeAgentIntegrationError(
            "dispatch must be a mapping"
        )

    event_id = _require_non_empty_string(
        dispatch,
        "event_id",
    )
    plan_hash = _require_non_empty_string(
        dispatch,
        "plan_hash",
    )
    capability = _require_non_empty_string(
        dispatch,
        "capability",
    )
    target = _require_non_empty_string(
        dispatch,
        "target",
    )
    as_of = _require_non_empty_string(
        dispatch,
        "as_of",
    )
    input_object_ids = _require_input_object_ids(
        dispatch
    )

    provider = _require_non_empty_string(
        dispatch,
        "provider",
    )

    authoritative_registry = (
        registry
        if registry is not None
        else CapabilityRegistry()
    )

    try:
        authoritative_provider = authoritative_registry.resolve(
            capability
        )
    except AODSLError as exc:
        raise RuntimeAgentIntegrationError(
            "runtime capability has no provider"
        ) from exc

    if provider != authoritative_provider:
        raise RuntimeAgentIntegrationError(
            "dispatch provider does not match runtime capability provider"
        )

    try:
        return build_agent_request(
            event_id=event_id,
            plan_hash=plan_hash,
            runtime_capability=capability,
            subject_id=target,
            as_of=as_of,
            input_object_ids=input_object_ids,
        )
    except AgentOrchestrationValidationError as exc:
        raise RuntimeAgentIntegrationError(
            "invalid runtime specialist-agent dispatch"
        ) from exc

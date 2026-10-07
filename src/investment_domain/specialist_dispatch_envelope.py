from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from investment_domain.runtime_agent_integration import (
    RuntimeAgentIntegrationError,
    build_agent_request_from_dispatch,
)


class SpecialistDispatchEnvelopeError(ValueError):
    """Invalid or unsupported specialist dispatch envelope."""


SCHEMA_VERSION = "specialist-dispatch-v1"

REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "plan_hash",
    "capability",
    "provider",
    "target",
    "as_of",
    "input_object_ids",
)


def validate_specialist_dispatch_envelope(
    dispatch: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(dispatch, Mapping):
        raise SpecialistDispatchEnvelopeError(
            "dispatch envelope must be a mapping"
        )

    missing = [
        field
        for field in REQUIRED_FIELDS
        if field not in dispatch
    ]

    if missing:
        raise SpecialistDispatchEnvelopeError(
            "missing required dispatch fields: "
            + ", ".join(missing)
        )

    unexpected = set(dispatch) - set(REQUIRED_FIELDS)
    if unexpected:
        raise SpecialistDispatchEnvelopeError(
            "unexpected dispatch fields: "
            + ", ".join(sorted(unexpected))
        )

    if dispatch["schema_version"] != SCHEMA_VERSION:
        raise SpecialistDispatchEnvelopeError(
            "unsupported specialist dispatch schema version"
        )

    as_of = dispatch["as_of"]
    if not isinstance(as_of, str):
        raise SpecialistDispatchEnvelopeError(
            "as_of must be an ISO calendar date"
        )

    try:
        parsed_date = date.fromisoformat(as_of)
    except ValueError as exc:
        raise SpecialistDispatchEnvelopeError(
            "as_of must be an ISO calendar date"
        ) from exc

    if parsed_date.isoformat() != as_of:
        raise SpecialistDispatchEnvelopeError(
            "as_of must use canonical YYYY-MM-DD format"
        )

    try:
        build_agent_request_from_dispatch(dispatch)
    except RuntimeAgentIntegrationError as exc:
        raise SpecialistDispatchEnvelopeError(
            "invalid specialist dispatch envelope"
        ) from exc

    return dict(dispatch)

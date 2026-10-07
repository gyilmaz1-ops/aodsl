from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from investment_domain.agents import AgentRequest
from investment_domain.runtime_agent_integration import (
    RuntimeAgentIntegrationError,
    build_agent_request_from_dispatch,
)
from investment_domain.specialist_dispatch_envelope import (
    SpecialistDispatchEnvelopeError,
    validate_specialist_dispatch_envelope,
)


class SpecialistOutboxBindingError(ValueError):
    """Durable specialist dispatch binding failed."""


IDENTITY_FIELDS = (
    "event_id",
    "plan_hash",
    "capability",
    "provider",
    "target",
)

LEASE_IDENTITY_FIELDS = (
    "event_id",
    "capability",
    "provider",
    "target",
)


def bind_specialist_outbox_dispatch(
    durable_record: Mapping[str, Any],
    lease: Any,
    envelope: Mapping[str, Any],
) -> AgentRequest:
    if not isinstance(durable_record, Mapping):
        raise SpecialistOutboxBindingError(
            "durable record must be a mapping"
        )

    if not isinstance(envelope, Mapping):
        raise SpecialistOutboxBindingError(
            "envelope must be a mapping"
        )

    try:
        durable_id = durable_record["outbox_id"]
        lease_id = lease.outbox_id

        if (
            type(durable_id) is not int
            or type(lease_id) is not int
            or durable_id <= 0
            or durable_id != lease_id
        ):
            raise SpecialistOutboxBindingError(
                "outbox identity mismatch"
            )

        payload_json = durable_record["payload_json"]

        if not isinstance(payload_json, str):
            raise SpecialistOutboxBindingError(
                "durable payload must be JSON text"
            )

        payload = json.loads(payload_json)

        if not isinstance(payload, dict):
            raise SpecialistOutboxBindingError(
                "durable payload must be a JSON object"
            )

        if not isinstance(payload.get("input_object_ids"), list):
            raise SpecialistOutboxBindingError(
                "durable input_object_ids must be a JSON array"
            )

        payload["input_object_ids"] = tuple(
            payload["input_object_ids"]
        )

        validated = validate_specialist_dispatch_envelope(
            payload
        )

        external_payload = dict(envelope)
        external_ids = external_payload.get("input_object_ids")

        if isinstance(external_ids, list):
            external_payload["input_object_ids"] = tuple(
                external_ids
            )

        external = validate_specialist_dispatch_envelope(
            external_payload
        )

        if validated != external:
            raise SpecialistOutboxBindingError(
                "external envelope differs from durable payload"
            )

        lease_payload = getattr(lease, "payload", None)

        if not isinstance(lease_payload, Mapping):
            raise SpecialistOutboxBindingError(
                "lease payload must be a mapping"
            )

        lease_envelope = dict(lease_payload)
        lease_ids = lease_envelope.get("input_object_ids")

        if isinstance(lease_ids, list):
            lease_envelope["input_object_ids"] = tuple(
                lease_ids
            )

        validated_lease = validate_specialist_dispatch_envelope(
            lease_envelope
        )

        if validated_lease != validated:
            raise SpecialistOutboxBindingError(
                "lease payload differs from durable payload"
            )

        for field in IDENTITY_FIELDS:
            durable_value = durable_record[field]

            if (
                not isinstance(durable_value, str)
                or not durable_value.strip()
                or durable_value != validated[field]
            ):
                raise SpecialistOutboxBindingError(
                    f"durable {field} mismatch"
                )

        for field in LEASE_IDENTITY_FIELDS:
            if getattr(lease, field) != validated[field]:
                raise SpecialistOutboxBindingError(
                    f"lease {field} mismatch"
                )

        return build_agent_request_from_dispatch(
            validated
        )

    except SpecialistOutboxBindingError:
        raise

    except (
        KeyError,
        AttributeError,
        TypeError,
        ValueError,
        RuntimeAgentIntegrationError,
        SpecialistDispatchEnvelopeError,
    ) as exc:
        raise SpecialistOutboxBindingError(
            "invalid durable specialist dispatch binding"
        ) from exc

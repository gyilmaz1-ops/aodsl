import json
from types import SimpleNamespace

import pytest

from investment_domain.specialist_outbox_binding import (
    SpecialistOutboxBindingError,
    bind_specialist_outbox_dispatch,
)


def valid_envelope():
    return {
        "schema_version": "specialist-dispatch-v1",
        "event_id": "EVENT-001",
        "plan_hash": "PLAN-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-001",),
    }


def durable_record():
    return {
        "outbox_id": 17,
        "event_id": "EVENT-001",
        "plan_hash": "PLAN-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "payload_json": json.dumps(
            valid_envelope(), sort_keys=True
        ),
    }


def valid_lease():
    return SimpleNamespace(
        outbox_id=17,
        event_id="EVENT-001",
        capability="VERIFY_CLAIM",
        provider="verification_agent_v1",
        target="CLAIM-001",
        payload=json.loads(
            json.dumps(valid_envelope())
        ),
    )


def test_valid_binding():
    result = bind_specialist_outbox_dispatch(
        durable_record(),
        valid_lease(),
        valid_envelope(),
    )
    assert result.request_id.startswith("agent-request:")


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "plan_hash",
        "capability",
        "provider",
        "target",
    ],
)
def test_durable_identity_mismatch_fails_closed(field):
    record = durable_record()
    record[field] = "TAMPERED"

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            record,
            valid_lease(),
            valid_envelope(),
        )


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "capability",
        "provider",
        "target",
    ],
)
def test_lease_identity_mismatch_fails_closed(field):
    lease = valid_lease()
    setattr(lease, field, "TAMPERED")

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable_record(),
            lease,
            valid_envelope(),
        )


def test_outbox_id_mismatch_fails_closed():
    lease = valid_lease()
    lease.outbox_id = 999

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable_record(),
            lease,
            valid_envelope(),
        )


def test_missing_durable_plan_hash_fails_closed():
    record = durable_record()
    del record["plan_hash"]

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            record,
            valid_lease(),
            valid_envelope(),
        )


def test_invalid_envelope_fails_closed():
    envelope = valid_envelope()
    envelope["as_of"] = "invalid-date"

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable_record(),
            valid_lease(),
            envelope,
        )


def test_external_envelope_mismatch_fails_closed():
    envelope = valid_envelope()
    envelope["input_object_ids"] = ("OTHER-EVIDENCE",)

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable_record(),
            valid_lease(),
            envelope,
        )


def test_invalid_durable_payload_fails_closed():
    record = durable_record()
    record["payload_json"] = "{}"

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            record,
            valid_lease(),
            valid_envelope(),
        )

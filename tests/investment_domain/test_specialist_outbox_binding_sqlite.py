import json

import pytest

from aodsl.outbox import MultiWorkerOutboxStore
from investment_domain.specialist_outbox_binding import (
    SpecialistOutboxBindingError,
    bind_specialist_outbox_dispatch,
)


def envelope():
    return {
        "schema_version": "specialist-dispatch-v1",
        "event_id": "EVENT-SQLITE-001",
        "plan_hash": "PLAN-SQLITE-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-001",),
    }


def create_store(tmp_path, payload=None):
    store = MultiWorkerOutboxStore(
        str(tmp_path / "specialist-outbox.db")
    )
    dispatch = envelope()

    from aodsl.multiworker import _event

    store.append_event(_event(dispatch["event_id"]))
    event_lease = store.claim_next_event(
        "SPECIALIST-EVENT-WORKER", 30
    )
    assert event_lease is not None
    assert event_lease.event.event_id == dispatch["event_id"]
    store.commit_event(event_lease)

    outbox_id = store.enqueue_outbox(
        event_id=dispatch["event_id"],
        plan_hash=dispatch["plan_hash"],
        capability=dispatch["capability"],
        provider=dispatch["provider"],
        target=dispatch["target"],
        reason="SPECIALIST_DISPATCH",
        payload=dispatch if payload is None else payload,
        idempotency_key="SPECIALIST-SQLITE-001",
    )

    lease = store.claim_next_outbox("SPECIALIST-WORKER", 30)
    assert lease is not None
    assert lease.outbox_id == outbox_id

    return store, lease, dispatch


def test_real_sqlite_binding(tmp_path):
    store, lease, dispatch = create_store(tmp_path)
    row = dict(store.outbox_row(lease.outbox_id))

    request = bind_specialist_outbox_dispatch(
        row, lease, dispatch
    )

    assert request.request_id.startswith("agent-request:")
    assert request.subject_id == dispatch["target"]


def test_real_sqlite_payload_mismatch_fails_closed(tmp_path):
    altered = envelope()
    altered["input_object_ids"] = ("OTHER-EVIDENCE",)

    store, lease, dispatch = create_store(
        tmp_path, payload=altered
    )

    row = dict(store.outbox_row(lease.outbox_id))

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            row, lease, dispatch
        )

    assert store.outbox_row(lease.outbox_id)["status"] == "SENDING"


def test_real_sqlite_durable_identity_mismatch_fails_closed(
    tmp_path,
):
    store, lease, dispatch = create_store(tmp_path)
    row = dict(store.outbox_row(lease.outbox_id))
    row["plan_hash"] = "TAMPERED"

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            row, lease, dispatch
        )


def test_real_sqlite_binding_deterministic(tmp_path):
    store, lease, dispatch = create_store(tmp_path)
    row = dict(store.outbox_row(lease.outbox_id))

    first = bind_specialist_outbox_dispatch(
        row, lease, dispatch
    )
    second = bind_specialist_outbox_dispatch(
        row, lease, dispatch
    )

    assert first.request_id == second.request_id


def test_tampered_lease_payload_fails_closed(tmp_path):
    from dataclasses import replace

    store, lease, _ = create_store(tmp_path)

    durable = dict(store.outbox_row(lease.outbox_id))

    altered_payload = dict(lease.payload)
    altered_payload["input_object_ids"] = ["UNTRUSTED-OBJECT"]

    tampered_lease = replace(
        lease,
        payload=altered_payload,
    )

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable,
            tampered_lease,
            altered_payload,
        )


def test_tampered_lease_rejected_with_original_envelope(tmp_path):
    from dataclasses import replace

    store, lease, original_envelope = create_store(tmp_path)
    durable = dict(store.outbox_row(lease.outbox_id))

    altered_payload = dict(lease.payload)
    altered_payload["input_object_ids"] = ["UNTRUSTED-OBJECT"]

    tampered_lease = replace(
        lease,
        payload=altered_payload,
    )

    with pytest.raises(SpecialistOutboxBindingError):
        bind_specialist_outbox_dispatch(
            durable,
            tampered_lease,
            original_envelope,
        )


def test_expired_specialist_lease_cannot_ack(tmp_path):
    from aodsl.multiworker import ManualClock
    from aodsl.runtime import AODSLError

    clock = ManualClock()
    store = MultiWorkerOutboxStore(
        str(tmp_path / "specialist-fencing.db"),
        clock,
    )
    dispatch = envelope()

    from aodsl.multiworker import _event

    store.append_event(_event(dispatch["event_id"]))
    event_lease = store.claim_next_event("EVENT-WORKER", 30)
    assert event_lease is not None
    store.commit_event(event_lease)

    outbox_id = store.enqueue_outbox(
        event_id=dispatch["event_id"],
        plan_hash=dispatch["plan_hash"],
        capability=dispatch["capability"],
        provider=dispatch["provider"],
        target=dispatch["target"],
        reason="SPECIALIST_DISPATCH",
        payload=dispatch,
        idempotency_key="SPECIALIST-FENCING-001",
        max_attempts=3,
    )

    first = store.claim_next_outbox(
        "WORKER-OLD", lease_seconds=5
    )
    assert first is not None

    clock.advance(6)

    second = store.claim_next_outbox(
        "WORKER-NEW", lease_seconds=5
    )
    assert second is not None
    assert second.outbox_id == outbox_id
    assert second.lease_version == first.lease_version + 1

    with pytest.raises(AODSLError) as ack_error:
        store.ack_dispatched(first)

    assert ack_error.value.code == "AODSL-R411"

    with pytest.raises(AODSLError) as fail_error:
        store.fail_dispatch(first, error="STALE_WORKER")

    assert fail_error.value.code == "AODSL-R411"

    current = store.outbox_row(outbox_id)
    assert current["status"] == "SENDING"
    assert current["lease_owner"] == "WORKER-NEW"
    assert current["lease_version"] == second.lease_version

    store.ack_dispatched(second)

    assert store.outbox_row(outbox_id)["status"] == "DISPATCHED"

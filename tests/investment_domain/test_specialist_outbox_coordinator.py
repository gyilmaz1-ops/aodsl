import pytest

from aodsl.multiworker import _event
from aodsl.outbox import MultiWorkerOutboxStore
from investment_domain.agents import AgentResult
from investment_domain.specialist_outbox_coordinator import (
    SpecialistOutboxCoordinatorError,
    dispatch_specialist_outbox_once,
)


def envelope():
    return {
        "schema_version": "specialist-dispatch-v1",
        "event_id": "EVENT-COORD-001",
        "plan_hash": "PLAN-COORD-001",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-001",),
    }


def seeded_store(tmp_path):
    store = MultiWorkerOutboxStore(
        str(tmp_path / "coordinator.db")
    )
    dispatch = envelope()

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
        idempotency_key="SPECIALIST-COORD-001",
        max_attempts=2,
    )

    return store, outbox_id


def valid_result(request):
    return AgentResult(
        result_id="RESULT-001",
        request_id=request.request_id,
        capability=request.capability,
        output_object_ids=("OUTPUT-001",),
        provenance_object_ids=("PROVENANCE-001",),
    )


def test_valid_result_acknowledged(tmp_path):
    store, outbox_id = seeded_store(tmp_path)

    result = dispatch_specialist_outbox_once(
        store, "WORKER-1", valid_result
    )

    assert result is not None
    assert store.outbox_row(outbox_id)["status"] == "DISPATCHED"


def test_invalid_result_never_acknowledged(tmp_path):
    store, outbox_id = seeded_store(tmp_path)

    def invalid_result(request):
        return AgentResult(
            result_id="RESULT-BAD",
            request_id="WRONG-REQUEST",
            capability=request.capability,
            output_object_ids=("OUTPUT-001",),
            provenance_object_ids=("PROVENANCE-001",),
        )

    with pytest.raises(SpecialistOutboxCoordinatorError):
        dispatch_specialist_outbox_once(
            store, "WORKER-1", invalid_result
        )

    assert store.outbox_row(outbox_id)["status"] != "DISPATCHED"


def test_provider_exception_never_acknowledged(tmp_path):
    store, outbox_id = seeded_store(tmp_path)

    def failing_provider(request):
        raise RuntimeError("SIMULATED_PROVIDER_FAILURE")

    with pytest.raises(SpecialistOutboxCoordinatorError):
        dispatch_specialist_outbox_once(
            store, "WORKER-1", failing_provider
        )

    assert store.outbox_row(outbox_id)["status"] != "DISPATCHED"


def test_no_pending_work_returns_none(tmp_path):
    store, _ = seeded_store(tmp_path)

    dispatch_specialist_outbox_once(
        store, "WORKER-1", valid_result
    )

    assert dispatch_specialist_outbox_once(
        store, "WORKER-2", valid_result
    ) is None


def test_invalid_result_reaches_dead_letter(tmp_path):
    store, outbox_id = seeded_store(tmp_path)

    def invalid_result(request):
        return AgentResult(
            result_id="RESULT-BAD",
            request_id="WRONG-REQUEST",
            capability=request.capability,
            output_object_ids=("OUTPUT-001",),
            provenance_object_ids=("PROVENANCE-001",),
        )

    for worker in ("WORKER-1", "WORKER-2"):
        with pytest.raises(SpecialistOutboxCoordinatorError):
            dispatch_specialist_outbox_once(
                store, worker, invalid_result
            )

    assert store.outbox_row(outbox_id)["status"] == "DEAD_LETTER"


def test_stale_worker_cannot_ack_after_ownership_transfer(tmp_path):
    from aodsl.multiworker import ManualClock
    from aodsl.runtime import AODSLError

    clock = ManualClock()
    controlled_store = MultiWorkerOutboxStore(
        str(tmp_path / "coordinator-fencing.db"),
        clock,
    )

    dispatch = envelope()

    controlled_store.append_event(
        _event(dispatch["event_id"])
    )

    event_lease = controlled_store.claim_next_event(
        "EVENT-WORKER", 30
    )
    assert event_lease is not None
    controlled_store.commit_event(event_lease)

    outbox_id = controlled_store.enqueue_outbox(
        event_id=dispatch["event_id"],
        plan_hash=dispatch["plan_hash"],
        capability=dispatch["capability"],
        provider=dispatch["provider"],
        target=dispatch["target"],
        reason="SPECIALIST_DISPATCH",
        payload=dispatch,
        idempotency_key="SPECIALIST-FENCING-C2",
        max_attempts=3,
    )

    def execute_after_lease_transfer(request):
        # WORKER-OLD holds a 5-second lease.
        clock.advance(6)

        new_lease = controlled_store.claim_next_outbox(
            "WORKER-NEW",
            lease_seconds=30,
        )

        assert new_lease is not None
        assert new_lease.outbox_id == outbox_id
        assert new_lease.lease_owner == "WORKER-NEW"

        return valid_result(request)

    with pytest.raises(AODSLError) as error:
        dispatch_specialist_outbox_once(
            controlled_store,
            "WORKER-OLD",
            execute_after_lease_transfer,
            lease_seconds=5,
        )

    assert error.value.code == "AODSL-R411"

    row = controlled_store.outbox_row(outbox_id)
    assert row["status"] == "SENDING"
    assert row["lease_owner"] == "WORKER-NEW"

import pytest

from aodsl.multiworker import _event
from aodsl.outbox import MultiWorkerOutboxStore
from investment_domain.agents import AgentCapability, AgentResult


def seeded_store(tmp_path, clock=None):
    store = MultiWorkerOutboxStore(
        str(tmp_path / "cp88ic.db"),
        clock=clock,
    )

    store.append_event(_event("CP88IC-EVENT"))

    event_lease = store.claim_next_event(
        "EVENT-WORKER", 30
    )
    assert event_lease is not None
    store.commit_event(event_lease)

    oid = store.enqueue_outbox(
        event_id="CP88IC-EVENT",
        plan_hash="CP88IC-PLAN",
        capability="VERIFY_CLAIM",
        provider="verification_agent_v1",
        target="CLAIM-001",
        reason="SPECIALIST_DISPATCH",
        payload={
            "schema_version": "specialist-dispatch-v1",
            "event_id": "CP88IC-EVENT",
            "plan_hash": "CP88IC-PLAN",
            "capability": "VERIFY_CLAIM",
            "provider": "verification_agent_v1",
            "target": "CLAIM-001",
            "as_of": "2026-10-08",
            "input_object_ids": ("EVIDENCE-001",),
        },
        idempotency_key="CP88IC-KEY",
    )

    lease = store.claim_next_outbox("WORKER-1", 30)
    assert lease is not None

    return store, oid, lease


def result():
    from investment_domain.runtime_agent_integration import (
        build_agent_request_from_dispatch,
    )

    request = build_agent_request_from_dispatch({
        "schema_version": "specialist-dispatch-v1",
        "event_id": "CP88IC-EVENT",
        "plan_hash": "CP88IC-PLAN",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-001",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-001",),
    })

    return AgentResult(
        result_id="CP88IC-RESULT",
        request_id=request.request_id,
        capability=request.capability,
        output_object_ids=("OUTPUT-001",),
        provenance_object_ids=("PROVENANCE-001",),
    )


def test_atomic_result_persistence_and_ack(tmp_path):
    store, oid, lease = seeded_store(tmp_path)

    store.persist_specialist_result_and_ack(
        lease,
        result(),
    )

    assert store.outbox_row(oid)["status"] == "DISPATCHED"

    with store.connect() as con:
        row = con.execute(
            """
            SELECT *
            FROM specialist_agent_results
            WHERE outbox_id = ?
            """,
            (oid,),
        ).fetchone()

    assert row is not None
    assert row["result_id"] == "CP88IC-RESULT"


def test_stale_lease_cannot_persist_result(tmp_path):
    from aodsl.multiworker import ManualClock
    from aodsl.runtime import AODSLError

    clock = ManualClock()

    store = MultiWorkerOutboxStore(
        str(tmp_path / "stale.db"),
        clock,
    )

    store.append_event(_event("CP88IC-STALE"))
    event_lease = store.claim_next_event("EVENT", 30)
    assert event_lease is not None
    store.commit_event(event_lease)

    oid = store.enqueue_outbox(
        "CP88IC-STALE",
        "PLAN",
        "VERIFY_CLAIM",
        "verification_agent_v1",
        "CLAIM-001",
        "SPECIALIST_DISPATCH",
        {},
        "CP88IC-STALE-KEY",
    )

    old = store.claim_next_outbox("OLD", 5)
    assert old is not None

    clock.advance(6)

    new = store.claim_next_outbox("NEW", 30)
    assert new is not None

    with pytest.raises(AODSLError) as exc:
        store.persist_specialist_result_and_ack(
            old,
            result(),
        )

    assert exc.value.code == "AODSL-R411"

    assert store.outbox_row(oid)["status"] == "SENDING"

    with store.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM specialist_agent_results
            WHERE outbox_id = ?
            """,
            (oid,),
        ).fetchone()[0]

    assert count == 0


def test_duplicate_result_identity_rolls_back(tmp_path):
    import sqlite3
    from investment_domain.runtime_agent_integration import (
        build_agent_request_from_dispatch,
    )

    store, oid, lease = seeded_store(tmp_path)
    store.persist_specialist_result_and_ack(lease, result())

    store.append_event(_event("CP88IC-SECOND"))
    event_lease = store.claim_next_event("EVENT-2", 30)
    assert event_lease is not None
    store.commit_event(event_lease)

    envelope = {
        "schema_version": "specialist-dispatch-v1",
        "event_id": "CP88IC-SECOND",
        "plan_hash": "PLAN-2",
        "capability": "VERIFY_CLAIM",
        "provider": "verification_agent_v1",
        "target": "CLAIM-002",
        "as_of": "2026-10-08",
        "input_object_ids": ("EVIDENCE-002",),
    }

    second_oid = store.enqueue_outbox(
        "CP88IC-SECOND",
        "PLAN-2",
        "VERIFY_CLAIM",
        "verification_agent_v1",
        "CLAIM-002",
        "SPECIALIST_DISPATCH",
        envelope,
        "CP88IC-SECOND-KEY",
    )

    second_lease = store.claim_next_outbox("WORKER-2", 30)
    assert second_lease is not None
    assert second_lease.outbox_id == second_oid

    second_request = build_agent_request_from_dispatch(envelope)

    duplicate = AgentResult(
        result_id="CP88IC-RESULT",
        request_id=second_request.request_id,
        capability=second_request.capability,
        output_object_ids=("OUTPUT-002",),
        provenance_object_ids=("PROVENANCE-002",),
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.persist_specialist_result_and_ack(
            second_lease, duplicate
        )

    assert store.outbox_row(second_oid)["status"] == "SENDING"

    with store.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM specialist_agent_results"
        ).fetchone()[0]

    assert count == 1


def test_result_request_mismatch_fails_closed(tmp_path):
    store, oid, lease = seeded_store(tmp_path)

    wrong = AgentResult(
        result_id="CP88IC-WRONG",
        request_id="UNRELATED-REQUEST",
        capability=AgentCapability.VERIFICATION,
        output_object_ids=("OUTPUT-001",),
        provenance_object_ids=("PROVENANCE-001",),
    )

    # The persistence boundary must reject a result
    # that does not belong to the durable dispatch.
    with pytest.raises(Exception):
        store.persist_specialist_result_and_ack(
            lease, wrong
        )

    assert store.outbox_row(oid)["status"] != "DISPATCHED"


def test_atomic_rollback_on_ack_failure(tmp_path):
    from unittest.mock import patch

    store, oid, lease = seeded_store(tmp_path)

    original_connect = store.tx

    # Simulate an SQL failure during the outbox UPDATE.
    from contextlib import contextmanager

    @contextmanager
    def failing_tx():
        with original_connect() as con:
            class ConnectionProxy:
                def execute(self, sql, params=()):
                    if "UPDATE outbox" in sql:
                        raise RuntimeError("INJECTED_ACK_FAILURE")
                    return con.execute(sql, params)

            yield ConnectionProxy()

    with patch.object(store, "tx", failing_tx):
        with pytest.raises(RuntimeError, match="INJECTED_ACK_FAILURE"):
            store.persist_specialist_result_and_ack(
                lease, result()
            )

    assert store.outbox_row(oid)["status"] == "SENDING"

    with store.connect() as con:
        count = con.execute(
            """
            SELECT COUNT(*)
            FROM specialist_agent_results
            WHERE outbox_id=?
            """,
            (oid,),
        ).fetchone()[0]

    assert count == 0


def test_persistence_failure_preserves_retryable_lease(tmp_path):
    from unittest.mock import patch
    import sqlite3

    store, oid, lease = seeded_store(tmp_path)

    original_tx = store.tx

    from contextlib import contextmanager

    @contextmanager
    def failing_tx():
        with original_tx() as con:
            class ConnectionProxy:
                def execute(self, sql, parameters=()):
                    if "UPDATE outbox" in sql:
                        raise sqlite3.OperationalError(
                            "INJECTED_PERSISTENCE_FAILURE"
                        )
                    return con.execute(sql, parameters)

            yield ConnectionProxy()

    with patch.object(store, "tx", failing_tx):
        with pytest.raises(
            sqlite3.OperationalError,
            match="INJECTED_PERSISTENCE_FAILURE",
        ):
            store.persist_specialist_result_and_ack(
                lease, result()
            )

    assert store.outbox_row(oid)["status"] == "SENDING"

    with store.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM specialist_agent_results"
        ).fetchone()[0]

    assert count == 0


def test_failed_persistence_can_be_reclaimed_after_expiry(tmp_path):
    from unittest.mock import patch
    import sqlite3
    from contextlib import contextmanager

    from aodsl.multiworker import ManualClock

    clock = ManualClock()
    store, oid, lease = seeded_store(tmp_path, clock=clock)

    assert store.clock is clock
    assert lease.lease_expires_at == clock() + 30

    original_tx = store.tx

    @contextmanager
    def failing_tx():
        with original_tx() as con:
            class ConnectionProxy:
                def execute(self, sql, parameters=()):
                    if "UPDATE outbox" in sql:
                        raise sqlite3.OperationalError(
                            "INJECTED_PERSISTENCE_FAILURE"
                        )
                    return con.execute(sql, parameters)

            yield ConnectionProxy()

    with patch.object(store, "tx", failing_tx):
        with pytest.raises(sqlite3.OperationalError):
            store.persist_specialist_result_and_ack(
                lease, result()
            )

    # Expire the original lease deterministically.
    clock.advance(31)

    reclaimed = store.claim_next_outbox(
        "RECOVERY-WORKER",
        lease_seconds=30,
    )

    assert reclaimed is not None
    assert reclaimed.outbox_id == oid
    assert reclaimed.lease_version > lease.lease_version

    store.persist_specialist_result_and_ack(
        reclaimed, result()
    )

    assert store.outbox_row(oid)["status"] == "DISPATCHED"

    with store.connect() as con:
        count = con.execute(
            "SELECT COUNT(*) FROM specialist_agent_results"
        ).fetchone()[0]

    assert count == 1

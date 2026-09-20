"""
AODSL v0.8.0 — End-to-End Multi-Worker Crash Consistency

INV-034 Atomic Event-to-Outbox Handoff
INV-035 Crash-Recoverable Dispatch Continuity
INV-036 End-to-End Stable Logical Effect
INV-037 Cross-Worker Stale Finalizer Rejection
"""
import json, tempfile
from pathlib import Path

import aodsl.runtime as a
import aodsl.multiworker as mw
import aodsl.outbox as ob


class EndToEndStore(ob.MultiWorkerOutboxStore):
    def commit_dispatch_plan(self, lease, plan_hash, capability, provider, target,
                             reason, payload, idempotency_key, outbox_max_attempts=5,
                             failpoint=None):
        """Atomically finalize event and create durable outbox row."""
        now=self.clock()
        with self.tx() as con:
            self._assert_fence(con,lease)
            if failpoint=="BEFORE_OUTBOX":
                raise RuntimeError("CHAOS_BEFORE_OUTBOX")
            con.execute("""
                INSERT OR IGNORE INTO outbox(
                  idempotency_key,event_id,plan_hash,capability,provider,target,reason,
                  payload_json,status,attempt_count,created_at,max_attempts,next_attempt_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,'PENDING',0,?,?,?,?)
            """,(idempotency_key,lease.event.event_id,plan_hash,capability,provider,
                 target,reason,json.dumps(payload,sort_keys=True),now,
                 outbox_max_attempts,now,now))
            if failpoint=="AFTER_OUTBOX":
                raise RuntimeError("CHAOS_AFTER_OUTBOX")
            con.execute("""
                UPDATE event_log SET status='COMMITTED',plan_hash=?,
                  lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE event_id=?
            """,(plan_hash,now,lease.event.event_id))
            if failpoint=="AFTER_EVENT_COMMIT":
                raise RuntimeError("CHAOS_AFTER_EVENT_COMMIT")

    def counts(self,event_id):
        with self.connect() as con:
            e=con.execute("SELECT status,attempt_count,lease_version FROM event_log WHERE event_id=?",
                          (event_id,)).fetchone()
            o=con.execute("SELECT COUNT(*) n FROM outbox WHERE event_id=?",(event_id,)).fetchone()["n"]
            d=con.execute("SELECT COUNT(*) n FROM outbox WHERE event_id=? AND status='DISPATCHED'",
                          (event_id,)).fetchone()["n"]
            return dict(event_status=e["status"],event_attempts=e["attempt_count"],
                        event_lease_version=e["lease_version"],outbox_count=o,
                        dispatched_count=d)


class Receiver(ob.IdempotentReceiver):
    pass


def _append(store,eid,max_attempts=5):
    store.append_event(mw._event(eid),max_attempts=max_attempts)


def _commit_action(store,lease,key,failpoint=None):
    store.commit_dispatch_plan(
        lease=lease, plan_hash="PLAN-"+lease.event.event_id,
        capability="VERIFY_CLAIM", provider="Verifier", target="C1",
        reason="E2E_TEST", payload={"event":lease.event.event_id},
        idempotency_key=key, outbox_max_attempts=5, failpoint=failpoint)


def inv034_037_contract_tests():
    clock=mw.ManualClock()
    with tempfile.TemporaryDirectory() as td:
        store=EndToEndStore(str(Path(td)/"e2e.db"),clock)

        # INV-034: crashes anywhere inside event->outbox transaction roll back both.
        for n,fp in enumerate(("BEFORE_OUTBOX","AFTER_OUTBOX","AFTER_EVENT_COMMIT"),1):
            eid=f"ATOMIC-{n}"; _append(store,eid)
            lease=store.claim_next_event("EW1",5)
            try:
                _commit_action(store,lease,"KEY-"+eid,fp); assert False
            except RuntimeError:
                pass
            c=store.counts(eid)
            assert c["event_status"]=="PROCESSING" and c["outbox_count"]==0
            clock.advance(6)
            recovered=store.claim_next_event("EW2",5)
            assert recovered and recovered.lease_version==lease.lease_version+1
            _commit_action(store,recovered,"KEY-"+eid)
            c=store.counts(eid)
            assert c["event_status"]=="COMMITTED" and c["outbox_count"]==1
            # Drain prior scenario outbox so the next claim targets the E2E case.
            pending=store.claim_next_outbox("DRAIN",5)
            assert pending and pending.event_id==eid
            store.ack_dispatched(pending)

        # INV-035/036/037: full crash-after-send path.
        eid="E2E-CRASH"; _append(store,eid)
        event_lease=store.claim_next_event("EVENT-W1",5)
        _commit_action(store,event_lease,"STABLE-E2E-KEY")
        c=store.counts(eid)
        assert c["event_status"]=="COMMITTED" and c["outbox_count"]==1

        receiver=Receiver()
        send1=store.claim_next_outbox("DISPATCH-W1",5)
        assert send1 and send1.event_id==eid
        receiver.send(send1)  # provider effect happened
        # crash: no ack_dispatched()
        clock.advance(6)
        send2=store.claim_next_outbox("DISPATCH-W2",5)
        assert send2 and send2.lease_version==send1.lease_version+1
        assert send2.idempotency_key==send1.idempotency_key=="STABLE-E2E-KEY"
        receiver.send(send2)
        assert receiver.logical_effects==1

        # stale dispatcher cannot finalize after lease ownership changed.
        try:
            store.ack_dispatched(send1); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-R411"
        store.ack_dispatched(send2)
        c=store.counts(eid)
        assert c["event_status"]=="COMMITTED"
        assert c["outbox_count"]==1 and c["dispatched_count"]==1
        assert receiver.logical_effects==1

        # Event itself cannot be claimed again after durable COMMITTED.
        clock.advance(100)
        nxt=store.claim_next_event("EVENT-W3",5)
        assert nxt is None

        # Duplicate enqueue with the same durable logical identity stays one row.
        same=store.enqueue_outbox(eid,"PLAN-"+eid,"VERIFY_CLAIM","Verifier","C1",
                                  "E2E_TEST",{"event":eid},"STABLE-E2E-KEY",5)
        c=store.counts(eid)
        assert c["outbox_count"]==1

    print("AODSL v0.8.0 INV-034..INV-037: END-TO-END CRASH CONSISTENCY TESTS PASSED")
    print("Covered: atomic event->outbox handoff | transaction rollback failpoints | lease recovery")
    print("         crash-after-send redelivery | stable idempotency | one logical effect | stale fencing")


if __name__=="__main__":
    inv034_037_contract_tests()

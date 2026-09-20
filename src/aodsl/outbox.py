"""
AODSL v0.7.0 — Multi-Worker Outbox Lease & Fencing

INV-030 Exclusive Outbox Lease
INV-031 Outbox Lease Recovery
INV-032 Fenced Outbox Finalization
INV-033 Bounded Outbox Retry

Network delivery remains at-least-once. Durable idempotency_key is preserved;
receivers must honor it for effectively-once logical task creation.
"""
from dataclasses import dataclass
from typing import Optional
import json, time, tempfile, threading
from pathlib import Path

import aodsl.runtime as a
import aodsl.multiworker as mw


@dataclass(frozen=True)
class OutboxLease:
    outbox_id:int
    idempotency_key:str
    event_id:str
    capability:str
    provider:str
    target:Optional[str]
    reason:Optional[str]
    payload:dict
    lease_owner:str
    lease_version:int
    lease_expires_at:float
    attempt_count:int


class MultiWorkerOutboxStore(mw.MultiWorkerSQLiteExecutionStore):
    def __init__(self,db_path,clock=None):
        super().__init__(db_path,clock)
        self._migrate_outbox()

    def _migrate_outbox(self):
        cols={
            "lease_owner":"TEXT",
            "lease_expires_at":"REAL",
            "lease_version":"INTEGER NOT NULL DEFAULT 0",
            "max_attempts":"INTEGER NOT NULL DEFAULT 5",
            "next_attempt_at":"REAL",
            "dead_letter_reason":"TEXT",
            "updated_at":"REAL",
        }
        with self.connect() as con:
            existing={r["name"] for r in con.execute("PRAGMA table_info(outbox)").fetchall()}
            for name,ddl in cols.items():
                if name not in existing:
                    con.execute(f"ALTER TABLE outbox ADD COLUMN {name} {ddl}")

    def enqueue_outbox(self,event_id,plan_hash,capability,provider,target,reason,payload,
                       idempotency_key,max_attempts=5):
        if max_attempts < 1:
            raise a.AODSLError("AODSL-R400","max_attempts must be >= 1")
        now=self.clock()
        with self.connect() as con:
            cur=con.execute("""
                INSERT OR IGNORE INTO outbox(
                    idempotency_key,event_id,plan_hash,capability,provider,target,reason,
                    payload_json,status,attempt_count,created_at,max_attempts,next_attempt_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?, 'PENDING',0,?,?,?,?)
            """,(idempotency_key,event_id,plan_hash,capability,provider,target,reason,
                 json.dumps(payload,sort_keys=True),now,max_attempts,now,now))
            if cur.rowcount==0:
                row=con.execute("SELECT outbox_id FROM outbox WHERE idempotency_key=?",
                                (idempotency_key,)).fetchone()
                return int(row["outbox_id"])
            return int(cur.lastrowid)

    def claim_next_outbox(self,worker_id,lease_seconds=30.0)->Optional[OutboxLease]:
        if not worker_id: raise a.AODSLError("AODSL-R400","worker_id required")
        if lease_seconds<=0: raise a.AODSLError("AODSL-R400","lease_seconds must be > 0")
        now=self.clock()
        with self.tx() as con:
            con.execute("""
                UPDATE outbox
                SET status='DEAD_LETTER',dead_letter_reason='MAX_ATTEMPTS_EXCEEDED',
                    lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE status IN ('PENDING','FAILED','SENDING')
                  AND attempt_count>=max_attempts
                  AND (status!='SENDING' OR lease_expires_at IS NULL OR lease_expires_at<=?)
            """,(now,now))
            row=con.execute("""
                SELECT * FROM outbox
                WHERE attempt_count<max_attempts AND (
                  (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at,0)<=?)
                  OR (status='SENDING' AND lease_expires_at<=?)
                )
                ORDER BY created_at,outbox_id LIMIT 1
            """,(now,now)).fetchone()
            if not row:return None
            ver=int(row["lease_version"] or 0)+1; exp=now+lease_seconds
            cur=con.execute("""
                UPDATE outbox SET status='SENDING',lease_owner=?,lease_expires_at=?,
                    lease_version=?,attempt_count=attempt_count+1,updated_at=?
                WHERE outbox_id=? AND lease_version=? AND (
                  (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at,0)<=?)
                  OR (status='SENDING' AND lease_expires_at<=?)
                )
            """,(worker_id,exp,ver,now,row["outbox_id"],int(row["lease_version"] or 0),now,now))
            if cur.rowcount!=1:return None
            return OutboxLease(
                int(row["outbox_id"]),row["idempotency_key"],row["event_id"],
                row["capability"],row["provider"],row["target"],row["reason"],
                json.loads(row["payload_json"]),worker_id,ver,exp,int(row["attempt_count"])+1)

    def _assert_outbox_fence(self,con,lease):
        row=con.execute("""SELECT status,lease_owner,lease_version,lease_expires_at
                           FROM outbox WHERE outbox_id=?""",(lease.outbox_id,)).fetchone()
        now=self.clock()
        if (not row or row["status"]!="SENDING" or row["lease_owner"]!=lease.lease_owner
            or int(row["lease_version"])!=lease.lease_version
            or row["lease_expires_at"] is None or float(row["lease_expires_at"])<=now):
            raise a.AODSLError("AODSL-R411","STALE_OR_EXPIRED_OUTBOX_LEASE")

    def ack_dispatched(self,lease):
        now=self.clock()
        with self.tx() as con:
            self._assert_outbox_fence(con,lease)
            con.execute("""UPDATE outbox SET status='DISPATCHED',dispatched_at=?,
                           lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                           WHERE outbox_id=?""",(now,now,lease.outbox_id))

    def fail_dispatch(self,lease,error="PROVIDER_FAILURE",backoff_seconds=0.0):
        if backoff_seconds<0: raise a.AODSLError("AODSL-R400","backoff_seconds must be >= 0")
        now=self.clock()
        with self.tx() as con:
            self._assert_outbox_fence(con,lease)
            row=con.execute("SELECT attempt_count,max_attempts FROM outbox WHERE outbox_id=?",
                            (lease.outbox_id,)).fetchone()
            exhausted=int(row["attempt_count"])>=int(row["max_attempts"])
            status="DEAD_LETTER" if exhausted else "FAILED"
            reason="MAX_ATTEMPTS_EXCEEDED" if exhausted else None
            con.execute("""UPDATE outbox SET status=?,last_error=?,dead_letter_reason=?,
                           next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                           WHERE outbox_id=?""",
                        (status,error,reason,now+backoff_seconds,now,lease.outbox_id))

    def heartbeat_outbox(self,lease,lease_seconds=30.0):
        if lease_seconds<=0: raise a.AODSLError("AODSL-R400","lease_seconds must be > 0")
        now=self.clock()
        with self.tx() as con:
            self._assert_outbox_fence(con,lease)
            exp=now+lease_seconds
            con.execute("UPDATE outbox SET lease_expires_at=?,updated_at=? WHERE outbox_id=?",
                        (exp,now,lease.outbox_id))
        return OutboxLease(lease.outbox_id,lease.idempotency_key,lease.event_id,
            lease.capability,lease.provider,lease.target,lease.reason,lease.payload,
            lease.lease_owner,lease.lease_version,exp,lease.attempt_count)

    def outbox_row(self,oid):
        with self.connect() as con:
            return con.execute("SELECT * FROM outbox WHERE outbox_id=?",(oid,)).fetchone()


class IdempotentReceiver:
    def __init__(self): self.keys=set(); self.logical_effects=0
    def send(self,lease):
        if lease.idempotency_key not in self.keys:
            self.keys.add(lease.idempotency_key); self.logical_effects+=1


def _seed_event(store,eid):
    store.append_event(mw._event(eid))
    l=store.claim_next_event("seed",10)
    store.commit_event(l)


def inv030_033_contract_tests():
    clock=mw.ManualClock()
    with tempfile.TemporaryDirectory() as td:
        store=MultiWorkerOutboxStore(str(Path(td)/"outbox.db"),clock)
        _seed_event(store,"E1")
        oid=store.enqueue_outbox("E1","P1","VERIFY_CLAIM","Verifier","C1","TEST",
                                 {"x":1},"KEY-1",3)

        # INV-030: concurrent dispatchers cannot hold the same live lease.
        barrier=threading.Barrier(2); got=[]; lock=threading.Lock()
        def race(w):
            barrier.wait(); x=store.claim_next_outbox(w,10)
            with lock:got.append(x)
        ts=[threading.Thread(target=race,args=(w,)) for w in ("D1","D2")]
        [t.start() for t in ts]; [t.join() for t in ts]
        winners=[x for x in got if x]
        assert len(winners)==1
        first=winners[0]
        assert store.claim_next_outbox("D3",10) is None

        # INV-031/032: crash after external send -> reclaim; old owner fenced.
        receiver=IdempotentReceiver()
        receiver.send(first)  # external effect happened, ack did not.
        clock.advance(11)
        second=store.claim_next_outbox("D2",10)
        assert second and second.lease_version==first.lease_version+1
        assert second.idempotency_key==first.idempotency_key=="KEY-1"
        receiver.send(second) # physical redelivery, one logical effect.
        assert receiver.logical_effects==1
        try:
            store.ack_dispatched(first); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-R411"
        store.ack_dispatched(second)
        assert store.outbox_row(oid)["status"]=="DISPATCHED"

        # Heartbeat retains generation and prevents steal.
        _seed_event(store,"E2")
        oid2=store.enqueue_outbox("E2","P2","VERIFY_CLAIM","Verifier","C2","TEST",{},"KEY-2",3)
        l=store.claim_next_outbox("D1",5); clock.advance(3)
        l2=store.heartbeat_outbox(l,5); assert l2.lease_version==l.lease_version
        clock.advance(3); assert store.claim_next_outbox("D2",5) is None
        store.ack_dispatched(l2)

        # INV-033: bounded provider retry/backoff -> DEAD_LETTER.
        _seed_event(store,"E3")
        oid3=store.enqueue_outbox("E3","P3","VERIFY_CLAIM","Verifier","C3","TEST",{},"KEY-3",2)
        x1=store.claim_next_outbox("D1",5)
        store.fail_dispatch(x1,"TIMEOUT",10)
        assert store.claim_next_outbox("D2",5) is None
        clock.advance(10)
        x2=store.claim_next_outbox("D2",5)
        store.fail_dispatch(x2,"TIMEOUT",20)
        r=store.outbox_row(oid3)
        assert r["status"]=="DEAD_LETTER" and r["dead_letter_reason"]=="MAX_ATTEMPTS_EXCEEDED"
        clock.advance(100); assert store.claim_next_outbox("D3",5) is None

        # Durable idempotency key remains UNIQUE.
        same=store.enqueue_outbox("E3","P3","VERIFY_CLAIM","Verifier","C3","TEST",{},"KEY-3",2)
        assert same==oid3

    print("AODSL v0.7.0 INV-030..INV-033: MULTI-WORKER OUTBOX TESTS PASSED")
    print("Covered: exclusive dispatch lease | expiry/reclaim | heartbeat | fencing")
    print("         crash-after-send redelivery | stable idempotency | bounded provider retry | dead letter")


if __name__=="__main__":
    inv030_033_contract_tests()

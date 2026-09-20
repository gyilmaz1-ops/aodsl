"""
AODSL v0.6.0 — Multi-Worker Lease & Fencing Runtime

INV-026 Exclusive Leased Processing
INV-027 Lease Recovery
INV-028 Fenced Finalization
INV-029 Bounded Event Retry

This module hardens durable event claiming for multiple workers. Runtime
scheduling uses an injected clock; rule decision semantics remain deterministic.
"""
from dataclasses import dataclass
from typing import Optional
import json, sqlite3, time, tempfile, threading
from pathlib import Path

import aodsl.runtime as a


@dataclass(frozen=True)
class EventLease:
    event: a.GraphEvent
    lease_owner: str
    lease_version: int
    lease_expires_at: float
    attempt_count: int


class ManualClock:
    def __init__(self, now=1000.0): self.now=float(now)
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class MultiWorkerSQLiteExecutionStore(a.SQLiteExecutionStore):
    def __init__(self, db_path, clock=None):
        self.clock = clock or time.time
        super().__init__(db_path)
        self._migrate()

    def _migrate(self):
        cols = {
            "lease_owner": "TEXT",
            "lease_expires_at": "REAL",
            "lease_version": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "next_attempt_at": "REAL",
            "dead_letter_reason": "TEXT",
        }
        with self.connect() as con:
            existing={r["name"] for r in con.execute("PRAGMA table_info(event_log)").fetchall()}
            for name, ddl in cols.items():
                if name not in existing:
                    con.execute(f"ALTER TABLE event_log ADD COLUMN {name} {ddl}")

    def append_event(self, event:a.GraphEvent, max_attempts=3):
        if max_attempts < 1:
            raise a.AODSLError("AODSL-R400","max_attempts must be >= 1")
        now=self.clock()
        with self.connect() as con:
            con.execute("""
                INSERT OR IGNORE INTO event_log(
                  event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                  status,attempt_count,max_attempts,next_attempt_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,'PENDING',0,?,?,?,?)
            """,(event.event_id,event.event_type,event.entity_type,event.entity_id,
                 json.dumps(event.payload,sort_keys=True),event.caused_by,
                 max_attempts,now,now,now))

    def claim_next_event(self, worker_id:str, lease_seconds=30.0)->Optional[EventLease]:
        if not worker_id:
            raise a.AODSLError("AODSL-R400","worker_id required")
        if lease_seconds <= 0:
            raise a.AODSLError("AODSL-R400","lease_seconds must be > 0")
        now=self.clock()
        with self.tx() as con:
            # Exhausted eligible work becomes durable DEAD_LETTER before selection.
            con.execute("""
                UPDATE event_log
                SET status='DEAD_LETTER', dead_letter_reason='MAX_ATTEMPTS_EXCEEDED',
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE status IN ('PENDING','FAILED','PROCESSING')
                  AND attempt_count >= max_attempts
                  AND (status!='PROCESSING' OR lease_expires_at IS NULL OR lease_expires_at<=?)
            """,(now,now))
            row=con.execute("""
                SELECT * FROM event_log
                WHERE attempt_count < max_attempts
                  AND (
                    (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at,0)<=?)
                    OR
                    (status='PROCESSING' AND lease_expires_at<=?)
                  )
                ORDER BY created_at,event_id
                LIMIT 1
            """,(now,now)).fetchone()
            if not row: return None
            new_version=int(row["lease_version"] or 0)+1
            expires=now+lease_seconds
            updated=con.execute("""
                UPDATE event_log
                SET status='PROCESSING', lease_owner=?, lease_expires_at=?,
                    lease_version=?, attempt_count=attempt_count+1, updated_at=?
                WHERE event_id=? AND lease_version=?
                  AND (
                    (status IN ('PENDING','FAILED') AND COALESCE(next_attempt_at,0)<=?)
                    OR
                    (status='PROCESSING' AND lease_expires_at<=?)
                  )
            """,(worker_id,expires,new_version,now,row["event_id"],
                 int(row["lease_version"] or 0),now,now))
            if updated.rowcount != 1: return None
            event=a.GraphEvent(row["event_id"],row["event_type"],row["entity_type"],
                               row["entity_id"],json.loads(row["payload_json"]),row["caused_by"])
            return EventLease(event,worker_id,new_version,expires,int(row["attempt_count"])+1)

    def _assert_fence(self, con, lease:EventLease):
        row=con.execute("""
            SELECT status,lease_owner,lease_version,lease_expires_at
            FROM event_log WHERE event_id=?
        """,(lease.event.event_id,)).fetchone()
        now=self.clock()
        if (not row or row["status"]!="PROCESSING"
            or row["lease_owner"]!=lease.lease_owner
            or int(row["lease_version"])!=lease.lease_version
            or row["lease_expires_at"] is None
            or float(row["lease_expires_at"])<=now):
            raise a.AODSLError("AODSL-R410","STALE_OR_EXPIRED_LEASE")

    def commit_event(self, lease:EventLease):
        now=self.clock()
        with self.tx() as con:
            self._assert_fence(con,lease)
            con.execute("""
                UPDATE event_log SET status='COMMITTED',lease_owner=NULL,
                    lease_expires_at=NULL,updated_at=? WHERE event_id=?
            """,(now,lease.event.event_id))

    def fail_event(self, lease:EventLease, error_code="WORKER_FAILURE",
                   backoff_seconds=0.0):
        if backoff_seconds < 0:
            raise a.AODSLError("AODSL-R400","backoff_seconds must be >= 0")
        now=self.clock()
        with self.tx() as con:
            self._assert_fence(con,lease)
            row=con.execute("SELECT attempt_count,max_attempts FROM event_log WHERE event_id=?",
                            (lease.event.event_id,)).fetchone()
            exhausted=int(row["attempt_count"])>=int(row["max_attempts"])
            status="DEAD_LETTER" if exhausted else "FAILED"
            reason="MAX_ATTEMPTS_EXCEEDED" if exhausted else None
            con.execute("""
                UPDATE event_log SET status=?,error_code=?,dead_letter_reason=?,
                    next_attempt_at=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE event_id=?
            """,(status,error_code,reason,now+backoff_seconds,now,lease.event.event_id))

    def heartbeat(self, lease:EventLease, lease_seconds=30.0)->EventLease:
        if lease_seconds <= 0:
            raise a.AODSLError("AODSL-R400","lease_seconds must be > 0")
        now=self.clock()
        with self.tx() as con:
            self._assert_fence(con,lease)
            expires=now+lease_seconds
            con.execute("UPDATE event_log SET lease_expires_at=?,updated_at=? WHERE event_id=?",
                        (expires,now,lease.event.event_id))
        return EventLease(lease.event,lease.lease_owner,lease.lease_version,
                          expires,lease.attempt_count)

    def event_row(self,event_id):
        with self.connect() as con:
            return con.execute("SELECT * FROM event_log WHERE event_id=?",(event_id,)).fetchone()


def _event(eid):
    return a.GraphEvent(eid,"graph.changed","CLAIM","C1",{},None)


def inv026_029_contract_tests():
    clock=ManualClock()
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"mw.db")
        store=MultiWorkerSQLiteExecutionStore(db,clock)

        # INV-026: two workers race; only one gets the event.
        store.append_event(_event("E1"))
        barrier=threading.Barrier(2); got=[]; lock=threading.Lock()
        def race(w):
            barrier.wait()
            x=store.claim_next_event(w,10)
            with lock: got.append(x)
        ts=[threading.Thread(target=race,args=(w,)) for w in ("W1","W2")]
        [t.start() for t in ts]; [t.join() for t in ts]
        winners=[x for x in got if x is not None]
        assert len(winners)==1
        first=winners[0]
        assert store.claim_next_event("W3",10) is None

        # INV-027 + INV-028: expired lease is reclaimable; old owner is fenced.
        clock.advance(11)
        second=store.claim_next_event("W2",10)
        assert second and second.lease_version==first.lease_version+1
        try:
            store.commit_event(first); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-R410"
        store.commit_event(second)
        assert store.event_row("E1")["status"]=="COMMITTED"

        # Heartbeat extends ownership without changing fencing generation.
        store.append_event(_event("E2"))
        l=store.claim_next_event("W1",5); clock.advance(3)
        l2=store.heartbeat(l,5)
        assert l2.lease_version==l.lease_version
        clock.advance(3)
        assert store.claim_next_event("W2",5) is None
        store.commit_event(l2)

        # INV-029: bounded retry + deterministic backoff.
        store.append_event(_event("E3"),max_attempts=3)
        l1=store.claim_next_event("W1",5)
        store.fail_event(l1,"X",backoff_seconds=10)
        assert store.event_row("E3")["status"]=="FAILED"
        assert store.claim_next_event("W2",5) is None
        clock.advance(10)
        l2=store.claim_next_event("W2",5)
        store.fail_event(l2,"X",backoff_seconds=20)
        clock.advance(19)
        assert store.claim_next_event("W3",5) is None
        clock.advance(1)
        l3=store.claim_next_event("W3",5)
        store.fail_event(l3,"X",backoff_seconds=40)
        r=store.event_row("E3")
        assert r["status"]=="DEAD_LETTER"
        assert r["dead_letter_reason"]=="MAX_ATTEMPTS_EXCEEDED"
        clock.advance(1000)
        assert store.claim_next_event("W4",5) is None

        # Crash/no fail ack: lease expiry consumes an attempt and can exhaust.
        store.append_event(_event("E4"),max_attempts=2)
        c1=store.claim_next_event("W1",2); clock.advance(3)
        c2=store.claim_next_event("W2",2)
        assert c2 and c2.attempt_count==2
        clock.advance(3)
        assert store.claim_next_event("W3",2) is None
        assert store.event_row("E4")["status"]=="DEAD_LETTER"

        # Later queue work remains processable after a dead letter.
        store.append_event(_event("E5"))
        q=store.claim_next_event("W5",5)
        assert q and q.event.event_id=="E5"
        store.commit_event(q)

    print("AODSL v0.6.0 INV-026..INV-029: MULTI-WORKER LEASE/FENCING TESTS PASSED")
    print("Covered: exclusive atomic claim | lease expiry/reclaim | heartbeat | fencing")
    print("         stale-owner rejection | bounded retry/backoff | crash exhaustion | queue isolation")


if __name__=="__main__":
    inv026_029_contract_tests()

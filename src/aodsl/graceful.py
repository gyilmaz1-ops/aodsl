"""
AODSL v0.9.6 — Graceful Lease Handoff

INV-046 Graceful Lease Handoff

A worker entering graceful shutdown:
1) stops claiming new work,
2) may finish work whose lease is still valid,
3) relinquishes unfinished leases without waiting for TTL expiry,
4) cannot finalize a relinquished lease,
5) makes relinquished work immediately reclaimable by another worker.

The implementation below is a deterministic contract model for event/outbox
lease semantics and is intentionally storage-agnostic.
"""
from dataclasses import dataclass
from threading import RLock


class LeaseError(RuntimeError):
    pass


@dataclass
class Work:
    work_id: str
    kind: str
    status: str = "PENDING"
    owner: str | None = None
    lease_version: int = 0
    lease_expires_at: float | None = None


@dataclass(frozen=True)
class Lease:
    work_id: str
    kind: str
    owner: str
    version: int
    expires_at: float


class GracefulLeaseRuntime:
    def __init__(self, work):
        self.work = {w.work_id: w for w in work}
        self._draining = set()
        self._mu = RLock()

    def begin_shutdown(self, worker_id):
        with self._mu:
            self._draining.add(worker_id)

    def is_draining(self, worker_id):
        with self._mu:
            return worker_id in self._draining

    def claim(self, worker_id, now, lease_seconds, kind):
        with self._mu:
            if worker_id in self._draining:
                return None
            active = "PROCESSING" if kind == "event" else "SENDING"
            for w in self.work.values():
                if w.kind != kind:
                    continue
                eligible = (
                    w.status in ("PENDING", "FAILED")
                    or (
                        w.status == active
                        and w.lease_expires_at is not None
                        and w.lease_expires_at <= now
                    )
                )
                if not eligible:
                    continue
                w.status = active
                w.owner = worker_id
                w.lease_version += 1
                w.lease_expires_at = now + lease_seconds
                return Lease(w.work_id, kind, worker_id, w.lease_version, w.lease_expires_at)
            return None

    def finalize(self, lease, now):
        with self._mu:
            w = self.work[lease.work_id]
            active = "PROCESSING" if lease.kind == "event" else "SENDING"
            final = "COMMITTED" if lease.kind == "event" else "DISPATCHED"
            if not (
                w.status == active
                and w.owner == lease.owner
                and w.lease_version == lease.version
                and w.lease_expires_at is not None
                and w.lease_expires_at > now
            ):
                raise LeaseError("STALE_OR_RELINQUISHED_LEASE")
            w.status = final
            w.owner = None
            w.lease_expires_at = None

    def relinquish(self, lease):
        """CAS-style relinquishment. Generation is advanced to fence old token."""
        with self._mu:
            w = self.work[lease.work_id]
            active = "PROCESSING" if lease.kind == "event" else "SENDING"
            if not (
                w.status == active
                and w.owner == lease.owner
                and w.lease_version == lease.version
            ):
                return False
            w.status = "PENDING"
            w.owner = None
            w.lease_expires_at = None
            w.lease_version += 1  # immediately fence the relinquished token
            return True

    def relinquish_all(self, worker_id):
        with self._mu:
            tokens = [
                Lease(w.work_id, w.kind, worker_id, w.lease_version, w.lease_expires_at)
                for w in self.work.values()
                if w.owner == worker_id and w.lease_expires_at is not None
            ]
        return sum(1 for token in tokens if self.relinquish(token))


def inv046_contract_tests():
    rt = GracefulLeaseRuntime([
        Work("E1", "event"),
        Work("E2", "event"),
        Work("O1", "outbox"),
    ])

    # Worker may own work, then enters draining state.
    e1 = rt.claim("W1", now=0, lease_seconds=60, kind="event")
    assert e1 is not None
    rt.begin_shutdown("W1")
    assert rt.is_draining("W1")

    # INV-046.1: draining worker must not claim new work.
    assert rt.claim("W1", now=1, lease_seconds=60, kind="event") is None
    assert rt.work["E2"].status == "PENDING"

    # INV-046.2: graceful drain may complete already-owned valid work.
    rt.finalize(e1, now=1)
    assert rt.work["E1"].status == "COMMITTED"

    # Separate worker owns event + outbox and relinquishes both on shutdown.
    e2 = rt.claim("W2", now=2, lease_seconds=600, kind="event")
    o1 = rt.claim("W2", now=2, lease_seconds=600, kind="outbox")
    assert e2 and o1
    rt.begin_shutdown("W2")
    released = rt.relinquish_all("W2")
    assert released == 2

    # INV-046.3: no TTL wait — immediately reclaimable by another worker.
    e2_new = rt.claim("W3", now=3, lease_seconds=30, kind="event")
    o1_new = rt.claim("D2", now=3, lease_seconds=30, kind="outbox")
    assert e2_new and e2_new.work_id == "E2"
    assert o1_new and o1_new.work_id == "O1"

    # INV-046.4: old tokens are fenced after relinquishment.
    try:
        rt.finalize(e2, now=3)
        assert False, "old event lease unexpectedly finalized"
    except LeaseError:
        pass
    try:
        rt.finalize(o1, now=3)
        assert False, "old outbox lease unexpectedly finalized"
    except LeaseError:
        pass

    # New owners remain valid.
    rt.finalize(e2_new, now=3)
    rt.finalize(o1_new, now=3)
    assert rt.work["E2"].status == "COMMITTED"
    assert rt.work["O1"].status == "DISPATCHED"

    # INV-046.5: relinquishment is CAS/idempotent; stale duplicate is harmless.
    assert rt.relinquish(e2) is False
    assert rt.relinquish(o1) is False

    print("AODSL v0.9.6 INV-046: GRACEFUL LEASE HANDOFF TESTS PASSED")
    print("Covered: stop-new-claims | finish-valid-work | immediate relinquish/reclaim")
    print("         generation fencing | event/outbox parity | stale relinquish idempotence")


if __name__ == "__main__":
    inv046_contract_tests()

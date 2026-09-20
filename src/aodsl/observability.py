"""
AODSL v0.9.7 — Observable Runtime Health

INV-047 Observable Runtime Health

Defines bounded, deterministic runtime telemetry independent of a metrics vendor.
Required signals:
- queue depth / oldest queue age
- claim count / latency
- lease reclaim count
- retry count / dead-letter count
- dispatch count / latency
- stale-fence rejection count
- health snapshot with explicit degradation reasons
"""
from dataclasses import dataclass, field
from collections import defaultdict
import math, threading


@dataclass(frozen=True)
class HealthSnapshot:
    status: str
    queue_depth: int
    oldest_queue_age_seconds: float
    dead_letters: int
    stale_fence_rejections: int
    reasons: tuple[str, ...]


class RuntimeMetrics:
    def __init__(self):
        self._mu=threading.RLock()
        self._counters=defaultdict(int)
        self._latencies=defaultdict(list)
        self._gauges={}

    def inc(self,name,n=1):
        if n < 0: raise ValueError("counter increments must be non-negative")
        with self._mu: self._counters[name]+=n

    def observe_ms(self,name,value):
        if not math.isfinite(value) or value < 0:
            raise ValueError("latency must be finite and non-negative")
        with self._mu: self._latencies[name].append(float(value))

    def gauge(self,name,value):
        if not math.isfinite(value) or value < 0:
            raise ValueError("gauge must be finite and non-negative")
        with self._mu: self._gauges[name]=float(value)

    def counter(self,name):
        with self._mu: return self._counters[name]

    def percentile_ms(self,name,p):
        with self._mu: xs=sorted(self._latencies[name])
        if not xs: return 0.0
        k=max(0,min(len(xs)-1,math.ceil((p/100)*len(xs))-1))
        return xs[k]

    def health(self,queue_depth,oldest_age_s,
               max_queue_depth=1000,max_oldest_age_s=300,
               max_dead_letters=0):
        reasons=[]
        if queue_depth>max_queue_depth: reasons.append("QUEUE_DEPTH_HIGH")
        if oldest_age_s>max_oldest_age_s: reasons.append("QUEUE_AGE_HIGH")
        dl=self.counter("dead_letter_total")
        if dl>max_dead_letters: reasons.append("DEAD_LETTERS_PRESENT")
        stale=self.counter("stale_fence_rejection_total")
        return HealthSnapshot(
            "DEGRADED" if reasons else "HEALTHY",
            int(queue_depth),float(oldest_age_s),dl,stale,tuple(reasons)
        )

    def export(self):
        with self._mu:
            return {
                "counters":dict(sorted(self._counters.items())),
                "gauges":dict(sorted(self._gauges.items())),
                "latency_p95_ms":{
                    k:self.percentile_ms(k,95) for k in sorted(self._latencies)
                },
                "latency_p99_ms":{
                    k:self.percentile_ms(k,99) for k in sorted(self._latencies)
                },
            }


REQUIRED_COUNTERS=(
    "event_claim_total",
    "outbox_claim_total",
    "lease_reclaim_total",
    "retry_total",
    "dead_letter_total",
    "dispatch_total",
    "stale_fence_rejection_total",
)
REQUIRED_LATENCIES=("event_claim_latency","outbox_dispatch_latency")
REQUIRED_GAUGES=("queue_depth","oldest_queue_age_seconds")


def inv047_contract_tests():
    m=RuntimeMetrics()

    # Representative runtime activity.
    m.inc("event_claim_total",3)
    m.inc("outbox_claim_total",2)
    m.inc("lease_reclaim_total")
    m.inc("retry_total",2)
    m.inc("dispatch_total",2)
    m.inc("stale_fence_rejection_total")
    for x in (1.0,2.0,3.0,4.0,20.0):
        m.observe_ms("event_claim_latency",x)
    for x in (5.0,6.0,7.0):
        m.observe_ms("outbox_dispatch_latency",x)
    m.gauge("queue_depth",4)
    m.gauge("oldest_queue_age_seconds",12)

    # Contract: all required telemetry names are stable and exportable.
    out=m.export()
    for n in REQUIRED_COUNTERS:
        # Touch missing zero-valued counters, then ensure export contains them.
        m.counter(n)
    out=m.export()
    assert set(REQUIRED_COUNTERS)<=set(out["counters"])
    assert set(REQUIRED_LATENCIES)<=set(out["latency_p95_ms"])
    assert set(REQUIRED_GAUGES)<=set(out["gauges"])

    # Percentiles are deterministic and bounded.
    assert m.percentile_ms("event_claim_latency",95)==20.0
    assert m.percentile_ms("outbox_dispatch_latency",99)==7.0

    # Healthy/degraded status is explicit and reason-coded.
    h=m.health(queue_depth=4,oldest_age_s=12,max_queue_depth=10,max_oldest_age_s=30)
    assert h.status=="HEALTHY" and not h.reasons

    m.inc("dead_letter_total")
    h=m.health(queue_depth=50,oldest_age_s=120,max_queue_depth=10,max_oldest_age_s=30)
    assert h.status=="DEGRADED"
    assert h.reasons==("QUEUE_DEPTH_HIGH","QUEUE_AGE_HIGH","DEAD_LETTERS_PRESENT")
    assert h.stale_fence_rejections==1

    # Invalid telemetry cannot silently poison monitoring.
    for bad in (-1,float("nan"),float("inf")):
        try:
            m.observe_ms("event_claim_latency",bad); assert False
        except ValueError: pass
    try:
        m.inc("retry_total",-1); assert False
    except ValueError: pass

    # Thread-safe counters: no lost increments.
    m2=RuntimeMetrics()
    def bump():
        for _ in range(1000): m2.inc("event_claim_total")
    ts=[threading.Thread(target=bump) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert m2.counter("event_claim_total")==4000

    print("AODSL v0.9.7 INV-047: OBSERVABLE RUNTIME HEALTH TESTS PASSED")
    print("Covered: stable metric vocabulary | queue depth/age | latency p95/p99 | reclaim/retry")
    print("         dead letter | stale fencing | reason-coded health | invalid-value guard | concurrency")


if __name__=="__main__":
    inv047_contract_tests()

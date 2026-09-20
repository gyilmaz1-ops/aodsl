"""INV-043: globally unique, durable, process-safe logical identities."""
import os,time,uuid,re
_ID_RE=re.compile(r"^(?P<prefix>[a-z][a-z0-9_-]{0,31})_(?P<ts>[0-9a-f]{16})_(?P<uuid>[0-9a-f]{32})$")

def new_durable_id(prefix="evt", *, now_ns=None, uuid_factory=uuid.uuid4):
    """Generate an opaque durable ID without process-local counters.

    Layout is sortable by creation timestamp but uniqueness does not depend on
    the clock: UUIDv4 supplies 122 random bits, so clock rollback/equality and
    multi-process concurrency cannot create deterministic counter collisions.
    """
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}",prefix):
        raise ValueError("invalid durable-id prefix")
    ts=time.time_ns() if now_ns is None else int(now_ns)
    if ts < 0 or ts > 0xffffffffffffffff:
        raise ValueError("timestamp out of uint64 range")
    return f"{prefix}_{ts:016x}_{uuid_factory().hex}"

def parse_durable_id(value):
    m=_ID_RE.fullmatch(value)
    if not m: raise ValueError("invalid durable id")
    return {"prefix":m.group("prefix"),"timestamp_ns":int(m.group("ts"),16),"uuid":m.group("uuid")}

# Backward-compatible public aliases.
def durable_id(prefix="evt", **kwargs):
    return new_durable_id(prefix, **kwargs)

def inspect_durable_id(value):
    return parse_durable_id(value)

from aodsl.identity import new_durable_id,parse_durable_id
def main():
    ts=0x123456789ABCDEF0
    ids=[new_durable_id("evt",now_ns=ts) for _ in range(100000)]
    assert len(ids)==len(set(ids)), "INV-043 collision detected"
    for x in ids[:100]:
        p=parse_durable_id(x)
        assert p["prefix"]=="evt" and p["timestamp_ns"]==ts and len(p["uuid"])==32
    # Different logical domains retain globally unique UUID components.
    out=[new_durable_id("outbox",now_ns=ts) for _ in range(10000)]
    assert not ({x.rsplit("_",1)[-1] for x in ids} & {x.rsplit("_",1)[-1] for x in out})
    print("INV-043 GLOBAL DURABLE IDENTITY: PASSED")
    print("same-timestamp stress=110000 | collisions=0 | uniqueness source=UUIDv4")
if __name__=="__main__": main()

"""
AODSL v0.9.8 — Tamper-Evident Audit Trail

INV-048 Tamper-Evident Audit Trail

Each audit record commits to:
  previous_hash + canonical payload -> record_hash

Properties:
- deterministic canonical serialization
- append-only hash chaining
- mutation/deletion/reordering/insertion detection
- explicit genesis hash
- optional HMAC sealing for authenticity (hash chain alone proves integrity
  relative to a trusted head, not writer authenticity)
"""
from dataclasses import dataclass
import hashlib, hmac, json

GENESIS_HASH="0"*64

def canonical_json(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)

def digest(previous_hash,payload):
    body=(previous_hash+"\n"+canonical_json(payload)).encode("utf-8")
    return hashlib.sha256(body).hexdigest()

@dataclass(frozen=True)
class AuditRecord:
    seq:int
    previous_hash:str
    payload:dict
    record_hash:str
    seal:str|None=None

class TamperEvidentAuditLog:
    def __init__(self,hmac_key:bytes|None=None):
        self.records=[]
        self.hmac_key=hmac_key

    def _seal(self,seq,record_hash):
        if self.hmac_key is None: return None
        msg=f"{seq}:{record_hash}".encode()
        return hmac.new(self.hmac_key,msg,hashlib.sha256).hexdigest()

    def append(self,payload):
        prev=self.records[-1].record_hash if self.records else GENESIS_HASH
        seq=len(self.records)+1
        rh=digest(prev,payload)
        rec=AuditRecord(seq,prev,payload,rh,self._seal(seq,rh))
        self.records.append(rec)
        return rec

    def trusted_head(self):
        return self.records[-1].record_hash if self.records else GENESIS_HASH

    def verify(self,trusted_head=None):
        prev=GENESIS_HASH
        for expected_seq,r in enumerate(self.records,1):
            if r.seq!=expected_seq: return False,"SEQUENCE_BREAK",expected_seq
            if r.previous_hash!=prev: return False,"PREVIOUS_HASH_MISMATCH",expected_seq
            expected=digest(prev,r.payload)
            if not hmac.compare_digest(r.record_hash,expected):
                return False,"RECORD_HASH_MISMATCH",expected_seq
            if self.hmac_key is not None:
                seal=self._seal(r.seq,r.record_hash)
                if r.seal is None or not hmac.compare_digest(r.seal,seal):
                    return False,"SEAL_MISMATCH",expected_seq
            prev=r.record_hash
        if trusted_head is not None and not hmac.compare_digest(prev,trusted_head):
            return False,"TRUSTED_HEAD_MISMATCH",len(self.records)
        return True,"OK",len(self.records)

def inv048_contract_tests():
    key=b"test-only-audit-signing-key"
    log=TamperEvidentAuditLog(key)
    log.append({"event_id":"E1","rule":"R001","decision":"INVALIDATE"})
    log.append({"event_id":"E2","rule":"R003","decision":"DISPATCH","retry":1})
    log.append({"event_id":"E3","rule":"R008","decision":"TRANSITION"})
    head=log.trusted_head()
    assert log.verify(head)==(True,"OK",3)

    # Canonicalization: key order cannot alter the digest.
    p1={"b":2,"a":1}; p2={"a":1,"b":2}
    assert canonical_json(p1)==canonical_json(p2)
    assert digest(GENESIS_HASH,p1)==digest(GENESIS_HASH,p2)

    # Payload mutation is detected.
    x=TamperEvidentAuditLog(key); [x.append(dict(r.payload)) for r in log.records]
    r=x.records[1]
    x.records[1]=AuditRecord(r.seq,r.previous_hash,{**r.payload,"retry":99},r.record_hash,r.seal)
    assert x.verify(head)[1]=="RECORD_HASH_MISMATCH"

    # Deletion is detected by sequence/previous-hash and trusted head.
    x=TamperEvidentAuditLog(key); [x.append(dict(r.payload)) for r in log.records]
    del x.records[1]
    ok,reason,_=x.verify(head)
    assert not ok and reason in ("SEQUENCE_BREAK","PREVIOUS_HASH_MISMATCH")

    # Reordering is detected.
    x=TamperEvidentAuditLog(key); [x.append(dict(r.payload)) for r in log.records]
    x.records[0],x.records[1]=x.records[1],x.records[0]
    assert x.verify(head)[0] is False

    # Tail truncation requires a trusted external head to detect.
    x=TamperEvidentAuditLog(key); [x.append(dict(r.payload)) for r in log.records]
    x.records.pop()
    assert x.verify()[0] is True
    assert x.verify(head)[1]=="TRUSTED_HEAD_MISMATCH"

    # Forging hashes without the HMAC key cannot forge authenticity.
    x=TamperEvidentAuditLog(key); [x.append(dict(r.payload)) for r in log.records]
    r=x.records[1]; forged={**r.payload,"decision":"FORGED"}
    forged_hash=digest(r.previous_hash,forged)
    x.records[1]=AuditRecord(r.seq,r.previous_hash,forged,forged_hash,r.seal)
    assert x.verify(head)[1]=="SEAL_MISMATCH"

    print("AODSL v0.9.8 INV-048: TAMPER-EVIDENT AUDIT TRAIL TESTS PASSED")
    print("Covered: canonical hash chain | mutation/deletion/reorder detection | trusted head")
    print("         tail-truncation detection | HMAC authenticity seal | deterministic serialization")

if __name__=="__main__":
    inv048_contract_tests()

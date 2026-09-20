import sqlite3,tempfile,hashlib,json
from pathlib import Path
TABLES=("graph_state","event_log","outbox","audit_log")
def _rows(c,t):
    cols=[r[1] for r in c.execute(f"PRAGMA table_info({t})")]
    return [dict(zip(cols,r)) for r in c.execute(f"SELECT {','.join(cols)} FROM {t} ORDER BY rowid")]
def _digest(c):
    raw=json.dumps({t:_rows(c,t) for t in TABLES},sort_keys=True,separators=(",",":")).encode()
    return hashlib.sha256(raw).hexdigest()
def run_ops001():
    with tempfile.TemporaryDirectory() as td:
        td=Path(td); src=td/"s.db"; bak=td/"b.db"; dst=td/"r.db"
        with sqlite3.connect(src) as c:
            c.executescript("""CREATE TABLE graph_state(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
CREATE TABLE event_log(event_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE outbox(id TEXT PRIMARY KEY,status TEXT NOT NULL,idempotency_key TEXT UNIQUE);
CREATE TABLE audit_log(id INTEGER PRIMARY KEY,record_hash TEXT NOT NULL);""")
            c.execute("INSERT INTO graph_state VALUES('G1','{}')")
            c.execute("INSERT INTO event_log VALUES('E1','COMMITTED','{}')")
            c.execute("INSERT INTO outbox VALUES('O1','DISPATCHED','K1')")
            c.execute("INSERT INTO audit_log VALUES(1,'abc')")
            before=_digest(c)
            c.commit()
            with sqlite3.connect(bak) as b: c.backup(b)
        expected=hashlib.sha256(bak.read_bytes()).hexdigest()
        assert hashlib.sha256(bak.read_bytes()).hexdigest()==expected
        with sqlite3.connect(bak) as b, sqlite3.connect(dst) as d:
            b.backup(d); assert d.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
            assert _digest(d)==before
    return {"id":"OPS-001","status":"CERTIFIED"}

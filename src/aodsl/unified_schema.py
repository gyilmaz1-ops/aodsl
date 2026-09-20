"""
AODSL v0.9.1 — Unified Runtime Schema Authority

INV-042 Single Schema Authority
Runtime lease/outbox schema evolution is owned only by the versioned migration
framework. Runtime classes contain no startup ALTER TABLE migration logic.
"""
import inspect, tempfile
from pathlib import Path

import aodsl.runtime as a
import aodsl.multiworker as oldmw
import aodsl.outbox as oldob
import aodsl.schema as sm


RUNTIME_MIGRATIONS=(
    sm.Migration(1,"event_multiworker_columns",(
        "ALTER TABLE event_log ADD COLUMN lease_owner TEXT",
        "ALTER TABLE event_log ADD COLUMN lease_expires_at REAL",
        "ALTER TABLE event_log ADD COLUMN lease_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE event_log ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 3",
        "ALTER TABLE event_log ADD COLUMN next_attempt_at REAL",
        "ALTER TABLE event_log ADD COLUMN dead_letter_reason TEXT",
    )),
    sm.Migration(2,"outbox_multiworker_columns",(
        "ALTER TABLE outbox ADD COLUMN lease_owner TEXT",
        "ALTER TABLE outbox ADD COLUMN lease_expires_at REAL",
        "ALTER TABLE outbox ADD COLUMN lease_version INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE outbox ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 5",
        "ALTER TABLE outbox ADD COLUMN next_attempt_at REAL",
        "ALTER TABLE outbox ADD COLUMN dead_letter_reason TEXT",
        "ALTER TABLE outbox ADD COLUMN updated_at REAL",
    )),
)
RUNTIME_SCHEMA_VERSION=2


class RuntimeSchemaManager(sm.VersionedMigrationManager):
    def __init__(self,db_path):
        super().__init__(db_path,RUNTIME_MIGRATIONS)

    def migrate(self,target_version=None,fail_after_statement=None):
        if target_version is None:
            target_version=RUNTIME_SCHEMA_VERSION
        return super().migrate(target_version,fail_after_statement)


class UnifiedMultiWorkerStore(oldob.MultiWorkerOutboxStore):
    """
    Reuses v0.6/v0.7 operational lease/fencing methods, but suppresses their
    ad-hoc migration hooks. Versioned RuntimeSchemaManager is the sole authority.
    """
    def _migrate(self): pass
    def _migrate_outbox(self): pass

    def __init__(self,db_path,clock=None):
        # Base SQLiteExecutionStore creates the frozen base tables first.
        a.SQLiteExecutionStore.__init__(self,db_path)
        self.clock=clock or __import__("time").time
        RuntimeSchemaManager(db_path).migrate()

    @property
    def schema_version(self):
        return RuntimeSchemaManager(self.db_path).current_version()


def inv042_contract_tests():
    # Source-level guard: the unified runtime itself contains no ALTER TABLE.
    src=inspect.getsource(UnifiedMultiWorkerStore)
    assert "ALTER TABLE" not in src

    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"runtime.db"
        clock=oldmw.ManualClock()
        store=UnifiedMultiWorkerStore(db,clock)
        assert store.schema_version==RUNTIME_SCHEMA_VERSION

        # All event lease columns came from migration v1.
        with store.connect() as con:
            event_cols={r["name"] for r in con.execute("PRAGMA table_info(event_log)")}
            outbox_cols={r["name"] for r in con.execute("PRAGMA table_info(outbox)")}
            assert {"lease_owner","lease_expires_at","lease_version","max_attempts",
                    "next_attempt_at","dead_letter_reason"} <= event_cols
            assert {"lease_owner","lease_expires_at","lease_version","max_attempts",
                    "next_attempt_at","dead_letter_reason","updated_at"} <= outbox_cols
            rows=con.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
            assert [(r["version"],r["name"]) for r in rows]==[
                (1,"event_multiworker_columns"),(2,"outbox_multiworker_columns")]

        # Existing operational event semantics still work.
        store.append_event(oldmw._event("E1"),max_attempts=2)
        e=store.claim_next_event("EW1",5)
        assert e and e.lease_version==1
        store.commit_event(e)
        assert store.event_row("E1")["status"]=="COMMITTED"

        # Existing operational outbox semantics still work.
        oid=store.enqueue_outbox("E1","P1","VERIFY_CLAIM","Verifier","C1","TEST",
                                 {},"KEY-1",2)
        o=store.claim_next_outbox("DW1",5)
        assert o and o.outbox_id==oid and o.lease_version==1
        store.ack_dispatched(o)
        assert store.outbox_row(oid)["status"]=="DISPATCHED"

        # Repeated startup is migration-idempotent.
        store2=UnifiedMultiWorkerStore(db,clock)
        assert store2.schema_version==2
        with store2.connect() as con:
            assert con.execute("SELECT COUNT(*) n FROM schema_migrations").fetchone()["n"]==2

    print("AODSL v0.9.1 INV-042: SINGLE RUNTIME SCHEMA AUTHORITY TESTS PASSED")
    print("Covered: versioned event/outbox migrations | no runtime ALTER hook | operational compatibility")
    print("         migration history | idempotent restart | single schema authority")


if __name__=="__main__":
    inv042_contract_tests()

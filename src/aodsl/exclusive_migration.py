"""
AODSL v0.9.2 — Exclusive Migration Ownership

INV-044 Exclusive Migration Ownership
Concurrent runtime startup must serialize schema migration ownership. A losing
instance waits/retries and then validates the winner's committed migration
history; it must not execute the same migration concurrently.
"""
import sqlite3, tempfile, threading, time
from pathlib import Path
import aodsl.runtime as a
import aodsl.schema as sm


MIGRATIONS=(
    sm.Migration(1,"base",("CREATE TABLE owned_data(id INTEGER PRIMARY KEY)",)),
    sm.Migration(2,"evolve",("ALTER TABLE owned_data ADD COLUMN value TEXT",)),
)


class ExclusiveMigrationManager(sm.VersionedMigrationManager):
    def __init__(self,db_path,owner_id,lock_timeout=2.0,hold_seconds=0.0):
        super().__init__(db_path,MIGRATIONS)
        self.owner_id=owner_id
        self.lock_timeout=float(lock_timeout)
        self.hold_seconds=float(hold_seconds)
        self.executed_versions=[]

    def connect(self):
        con=sqlite3.connect(self.db_path, timeout=self.lock_timeout,
                            check_same_thread=False, isolation_level=None)
        con.row_factory=sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={int(self.lock_timeout*1000)}")
        return con

    def migrate_exclusive(self,target_version=2):
        con=self.connect()
        try:
            # One write transaction owns bootstrap + validation + all pending
            # migrations. SQLite BEGIN IMMEDIATE is the single-node ownership
            # primitive; PostgreSQL will require an advisory-lock equivalent.
            con.execute("BEGIN IMMEDIATE")
            try:
                self._bootstrap(con)
                rows=self.applied(con)
                by={m.version:m for m in self.migrations}
                if rows and max(rows)>self.migrations[-1].version:
                    raise a.AODSLError("AODSL-M410","DATABASE_SCHEMA_NEWER_THAN_RUNTIME")
                for v,row in rows.items():
                    m=by.get(v)
                    if not m or row["name"]!=m.name or row["checksum"]!=m.checksum:
                        raise a.AODSLError("AODSL-M412","MIGRATION_CHECKSUM_MISMATCH")
                current=max(rows) if rows else 0
                if target_version<current:
                    raise a.AODSLError("AODSL-M411","DOWNGRADE_NOT_SUPPORTED")
                if self.hold_seconds:
                    time.sleep(self.hold_seconds)
                for m in self.migrations:
                    if current < m.version <= target_version:
                        for stmt in m.statements:
                            con.execute(stmt)
                        con.execute("""INSERT INTO schema_migrations(version,name,checksum)
                                       VALUES(?,?,?)""",(m.version,m.name,m.checksum))
                        self.executed_versions.append(m.version)
                con.commit()
            except Exception:
                con.rollback(); raise
            return target_version
        finally:
            con.close()


def inv044_contract_tests():
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"race.db"
        barrier=threading.Barrier(2)
        results={}
        errors={}

        def runner(name,hold):
            try:
                mgr=ExclusiveMigrationManager(db,name,lock_timeout=3,hold_seconds=hold)
                barrier.wait()
                results[name]=(mgr.migrate_exclusive(),tuple(mgr.executed_versions))
            except Exception as e:
                errors[name]=e

        # Force a real startup race. Whichever acquires BEGIN IMMEDIATE first
        # becomes owner; the loser blocks, then observes committed history.
        t1=threading.Thread(target=runner,args=("A",0.20))
        t2=threading.Thread(target=runner,args=("B",0.20))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert not errors, errors
        assert set(results)=={"A","B"}
        executions=[versions for _,versions in results.values()]
        assert sorted(len(v) for v in executions)==[0,2], executions
        assert sum(1 for v in executions if v==(1,2))==1
        assert sum(1 for v in executions if v==())==1

        with sqlite3.connect(db) as con:
            rows=con.execute("SELECT version,name FROM schema_migrations ORDER BY version").fetchall()
            assert rows==[(1,"base"),(2,"evolve")]
            cols={r[1] for r in con.execute("PRAGMA table_info(owned_data)")}
            assert {"id","value"}<=cols

    # Timeout is fail-closed: contender never runs migration without ownership.
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"timeout.db"
        owner=ExclusiveMigrationManager(db,"OWNER",lock_timeout=2)
        con=owner.connect()
        con.execute("BEGIN IMMEDIATE")
        contender=ExclusiveMigrationManager(db,"CONTENDER",lock_timeout=0.05)
        try:
            try:
                contender.migrate_exclusive(); assert False
            except sqlite3.OperationalError as e:
                assert "locked" in str(e).lower()
                assert contender.executed_versions==[]
        finally:
            con.rollback(); con.close()
        assert owner.migrate_exclusive()==2

    print("AODSL v0.9.2 INV-044: EXCLUSIVE MIGRATION OWNERSHIP TESTS PASSED")
    print("Covered: concurrent startup race | single migration executor | loser validates winner")
    print("         transactional ownership | lock timeout fail-closed | idempotent history")


if __name__=="__main__":
    inv044_contract_tests()

"""
AODSL v0.9.0 — Versioned Schema Migration Framework

INV-038 Ordered Schema Evolution
INV-039 Transactional Migration Atomicity
INV-040 Migration Checksum Integrity
INV-041 Incompatible Schema Fail-Fast
"""
from dataclasses import dataclass
import hashlib, sqlite3, tempfile
from pathlib import Path

import aodsl.runtime as a


@dataclass(frozen=True)
class Migration:
    version:int
    name:str
    statements:tuple[str,...]

    @property
    def checksum(self):
        raw=(str(self.version)+"\n"+self.name+"\n"+"\n".join(self.statements)).encode()
        return hashlib.sha256(raw).hexdigest()


MIGRATIONS=(
    Migration(1,"base_schema",(
        "CREATE TABLE runtime_items(id INTEGER PRIMARY KEY, value TEXT NOT NULL)",
    )),
    Migration(2,"add_status",(
        "ALTER TABLE runtime_items ADD COLUMN status TEXT NOT NULL DEFAULT 'ACTIVE'",
    )),
    Migration(3,"add_runtime_index",(
        "CREATE INDEX idx_runtime_items_status ON runtime_items(status)",
    )),
)
CURRENT_SCHEMA_VERSION=MIGRATIONS[-1].version


class VersionedMigrationManager:
    def __init__(self,db_path,migrations=MIGRATIONS):
        self.db_path=str(db_path)
        self.migrations=tuple(migrations)
        versions=[m.version for m in self.migrations]
        if versions != sorted(versions) or len(versions)!=len(set(versions)):
            raise a.AODSLError("AODSL-M400","Migrations must be unique and ordered")
        if versions and versions != list(range(1,versions[-1]+1)):
            raise a.AODSLError("AODSL-M401","Migration versions must be contiguous")

    def connect(self):
        con=sqlite3.connect(self.db_path)
        con.row_factory=sqlite3.Row
        return con

    def _bootstrap(self,con):
        con.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")

    def applied(self,con):
        return {int(r["version"]):r for r in
                con.execute("SELECT version,name,checksum FROM schema_migrations ORDER BY version")}

    def migrate(self,target_version=None,fail_after_statement=None):
        target=CURRENT_SCHEMA_VERSION if target_version is None else int(target_version)
        max_supported=self.migrations[-1].version if self.migrations else 0
        if target<0 or target>max_supported:
            raise a.AODSLError("AODSL-M409","UNSUPPORTED_TARGET_SCHEMA_VERSION")
        con=self.connect()
        try:
            self._bootstrap(con); con.commit()
            rows=self.applied(con)
            if rows and max(rows)>max_supported:
                raise a.AODSLError("AODSL-M410","DATABASE_SCHEMA_NEWER_THAN_RUNTIME")
            by={m.version:m for m in self.migrations}
            for v,row in rows.items():
                m=by.get(v)
                if m is None:
                    raise a.AODSLError("AODSL-M410","UNKNOWN_APPLIED_MIGRATION")
                if row["name"]!=m.name or row["checksum"]!=m.checksum:
                    raise a.AODSLError("AODSL-M412","MIGRATION_CHECKSUM_MISMATCH")
            current=max(rows) if rows else 0
            if target<current:
                raise a.AODSLError("AODSL-M411","DOWNGRADE_NOT_SUPPORTED")
            executed=0
            for m in self.migrations:
                if m.version<=current or m.version>target: continue
                con.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in m.statements:
                        con.execute(stmt); executed+=1
                        if fail_after_statement is not None and executed==fail_after_statement:
                            raise RuntimeError("CHAOS_DURING_MIGRATION")
                    con.execute("""INSERT INTO schema_migrations(version,name,checksum)
                                   VALUES(?,?,?)""",(m.version,m.name,m.checksum))
                    con.commit()
                except Exception:
                    con.rollback(); raise
            return self.current_version(con)
        finally:
            con.close()

    def current_version(self,con=None):
        own=con is None
        con=con or self.connect()
        try:
            self._bootstrap(con)
            row=con.execute("SELECT COALESCE(MAX(version),0) v FROM schema_migrations").fetchone()
            return int(row["v"])
        finally:
            if own: con.close()


def inv038_041_contract_tests():
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"schema.db"
        mgr=VersionedMigrationManager(db)

        # INV-038: ordered evolution and idempotent startup.
        assert mgr.migrate()==3
        assert mgr.migrate()==3
        with mgr.connect() as con:
            rows=con.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            assert [r["version"] for r in rows]==[1,2,3]
            cols={r["name"] for r in con.execute("PRAGMA table_info(runtime_items)")}
            assert {"id","value","status"}<=cols

        # INV-040: historical migration mutation is detected.
        tampered=list(MIGRATIONS)
        tampered[1]=Migration(2,"add_status",(
            "ALTER TABLE runtime_items ADD COLUMN status TEXT NOT NULL DEFAULT 'BROKEN'",
        ))
        try:
            VersionedMigrationManager(db,tuple(tampered)).migrate(); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-M412"

        # INV-041: downgrade and newer-than-runtime both fail fast.
        try:
            mgr.migrate(target_version=2); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-M411"
        with mgr.connect() as con:
            con.execute("""INSERT INTO schema_migrations(version,name,checksum)
                           VALUES(99,'future','x')""")
            con.commit()
        try:
            mgr.migrate(); assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-M410"

    # INV-039: one migration is all-or-nothing.
    with tempfile.TemporaryDirectory() as td:
        db=Path(td)/"atomic.db"
        atomic_migs=(
            Migration(1,"base",("CREATE TABLE t(id INTEGER PRIMARY KEY)",)),
            Migration(2,"two_steps",(
                "ALTER TABLE t ADD COLUMN a TEXT",
                "ALTER TABLE t ADD COLUMN b TEXT",
            )),
        )
        mgr=VersionedMigrationManager(db,atomic_migs)
        assert mgr.migrate(target_version=1)==1
        try:
            mgr.migrate(target_version=2,fail_after_statement=1); assert False
        except RuntimeError:
            pass
        assert mgr.current_version()==1
        with mgr.connect() as con:
            cols={r["name"] for r in con.execute("PRAGMA table_info(t)")}
            assert "a" not in cols and "b" not in cols
        assert mgr.migrate(target_version=2)==2

    # Definition validation.
    try:
        VersionedMigrationManager(":memory:",(
            Migration(1,"a",("SELECT 1",)),Migration(3,"gap",("SELECT 1",))
        )); assert False
    except a.AODSLError as e:
        assert e.code=="AODSL-M401"

    print("AODSL v0.9.0 INV-038..INV-041: VERSIONED SCHEMA MIGRATION TESTS PASSED")
    print("Covered: ordered/contiguous migrations | idempotent startup | transactional rollback")
    print("         checksum drift detection | downgrade rejection | newer-schema fail-fast")


if __name__=="__main__":
    inv038_041_contract_tests()

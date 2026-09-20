from pathlib import Path
import subprocess,sys,os,re
ROOT=Path(__file__).resolve().parents[1]
files=[
 ROOT/"tests/contract/test_core_invariants.py",
 ROOT/"tests/contract/test_frozen_rules.py",
 ROOT/"tests/contract/test_runtime_extensions.py",
 ROOT/"tests/contract/test_schema_and_production.py",
]
env={**os.environ,"PYTHONPATH":str(ROOT/"src"),"PYTHONNOUSERSITE":"1","PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1"}
r=subprocess.run([sys.executable,"-m","pytest","-q",*map(str,files)],cwd=ROOT,capture_output=True,text=True,env=env)
print(r.stdout,end="")
if r.returncode: print(r.stderr); raise SystemExit(r.returncode)
m=re.search(r"(\d+) passed",r.stdout); total=int(m.group(1)) if m else 0
if total!=33: raise SystemExit(f"expected 33 package-native tests, got {total}")
i=subprocess.run([sys.executable,str(ROOT/"tests/contract/test_inv043_identity.py")],cwd=ROOT,capture_output=True,text=True,env=env)
print(i.stdout,end="")
if i.returncode: print(i.stderr); raise SystemExit(i.returncode)
print("AODSL v1 PACKAGE-NATIVE CONTRACT GATE: PASSED (33 tests + INV-043 stress contract)")

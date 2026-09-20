from pathlib import Path
import re,sys
ROOT=Path(__file__).resolve().parents[2]
PKG=ROOT/"src"/"aodsl"

bad=[]
for p in PKG.glob("*.py"):
    text=p.read_text(encoding="utf-8")
    if re.search(r"\b(?:import|from)\s+aodsl_v\d",text):
        bad.append(p.name)
assert not bad, f"Historical cross-imports remain: {bad}"

sys.path.insert(0,str(ROOT/"src"))
import aodsl
assert aodsl.__version__=="1.0.0"
for name in aodsl.__all__:
    assert hasattr(aodsl,name),name
print("AODSL v1 PACKAGE BOUNDARY: PASSED")
print("Historical cross-imports: 0")
print("Public API:",", ".join(aodsl.__all__))

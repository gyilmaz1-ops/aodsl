import argparse,sys,json
from .toolchain import validate_source,compile_file,format_diagnostic
from pathlib import Path

def main(argv=None):
    ap=argparse.ArgumentParser(prog="aodsl")
    sp=ap.add_subparsers(dest="cmd",required=True)
    v=sp.add_parser("validate",help="validate an AODSL source file")
    v.add_argument("file"); v.add_argument("--diagnostic-format",choices=["text","json"],default="text")
    c=sp.add_parser("compile",help="compile AODSL to canonical IR")
    c.add_argument("file"); c.add_argument("-o","--output"); c.add_argument("--diagnostic-format",choices=["text","json"],default="text")
    sp.add_parser("test-contract",help="run package-native contract suite")
    sp.add_parser("certify",help="run production certification gate")
    sp.add_parser("lsp",help="start AODSL language server over stdio")
    a=ap.parse_args(argv)
    if a.cmd=="validate":
        source=Path(a.file).read_text(encoding="utf-8")
        compiled,ds=validate_source(source,a.file)
        if ds:
            for d in ds: print(format_diagnostic(d,a.diagnostic_format),file=sys.stderr)
            return 1
        print(f"VALID {a.file} rules={len(compiled.rules)} ir_hash={compiled.ir_hash}")
        return 0
    if a.cmd=="compile":
        artifact,ds=compile_file(a.file,a.output)
        if ds:
            for d in ds: print(format_diagnostic(d,a.diagnostic_format),file=sys.stderr)
            return 1
        if not a.output: print(json.dumps(artifact,sort_keys=True,indent=2,ensure_ascii=False))
        else: print(f"COMPILED {a.file} -> {a.output} ir_hash={artifact['ir_hash']}")
        return 0
    if a.cmd=="test-contract":
        import subprocess,os
        root=Path(__file__).resolve().parents[2]
        return subprocess.run([sys.executable,str(root/"tests/run_contract_suite.py")],
            cwd=root,env={**os.environ,"PYTHONPATH":str(root/"src")}).returncode
    if a.cmd=="certify":
        from .certification import run_certification
        return run_certification()
    if a.cmd=="lsp":
        from .lsp import serve
        serve()
        return 0
    return 1

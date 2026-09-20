"""AODSL v1 structured diagnostics and canonical compilation."""
from dataclasses import dataclass,asdict
from pathlib import Path
import json,re
from .compiler import AODSLCompiler
from .runtime import AODSLError

@dataclass(frozen=True)
class SourceSpan:
    start_line:int
    start_column:int
    end_line:int
    end_column:int

@dataclass(frozen=True)
class Diagnostic:
    code:str
    severity:str
    message:str
    file:str|None
    span:SourceSpan|None
    def to_dict(self):
        d=asdict(self)
        return d

_TOKEN_RE=re.compile(r'"(?:\\.|[^"\\])*"|>=|<=|==|!=|->|[A-Za-z_][A-Za-z0-9_.]*|\d+(?:\.\d+)?|[^\s]')

def source_tokens(source):
    """Deterministic token/span projection for diagnostics/IDE consumers."""
    out=[]
    for m in _TOKEN_RE.finditer(source):
        a,b=m.span()
        sl=source.count("\n",0,a)+1
        last=source.rfind("\n",0,a)
        sc=a-(last+1)+1
        text=m.group(0)
        parts=text.split("\n")
        if len(parts)==1:
            el,ec=sl,sc+len(text)
        else:
            el,ec=sl+len(parts)-1,len(parts[-1])+1
        out.append({"text":text,"span":SourceSpan(sl,sc,el,ec)})
    return out

def _span_for_error(source,message):
    toks=source_tokens(source)
    patterns=[r"got '([^']+)'",r"Unexpected '([^']+)'",r"Unknown [^']*'([^']+)'"]
    needle=None
    for p in patterns:
        m=re.search(p,message)
        if m: needle=m.group(1); break
    if needle:
        for t in toks:
            if t["text"]==needle: return t["span"]
    # Fail deterministically at EOF if parser cannot expose the offending token.
    lines=source.splitlines() or [""]
    return SourceSpan(len(lines),len(lines[-1])+1,len(lines),len(lines[-1])+1)

def validate_source(source,file=None):
    try:
        c=AODSLCompiler().compile(source)
        return c,[]
    except AODSLError as e:
        msg=str(e)
        # Avoid duplicating code in structured message.
        clean=re.sub(r"^"+re.escape(e.code)+r":\s*","",msg)
        return None,[Diagnostic(e.code,"error",clean,file,_span_for_error(source,msg))]

def compile_file(path,out=None):
    p=Path(path); source=p.read_text(encoding="utf-8")
    compiled,diags=validate_source(source,str(p))
    if diags:return None,diags
    artifact={"format":"aodsl.canonical-ir.v1","source_file":p.name,
              "source_hash":compiled.source_hash,"ir_hash":compiled.ir_hash,
              "rules":list(compiled.canonical_ir)}
    text=json.dumps(artifact,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n"
    if out:Path(out).write_text(text,encoding="utf-8")
    return artifact,[]

def format_diagnostic(d,fmt="text"):
    if fmt=="json": return json.dumps(d.to_dict(),sort_keys=True,separators=(",",":"),ensure_ascii=False)
    loc=d.file or ""
    if d.span: loc+=f":{d.span.start_line}:{d.span.start_column}"
    return f"{loc+': ' if loc else ''}{d.severity.upper()} {d.code}: {d.message}"

"""Minimal dependency-free AODSL Language Server Protocol foundation."""
import json,sys,re
from .toolchain import validate_source

def _lsp_range(span):
    if span is None:
        return {"start":{"line":0,"character":0},"end":{"line":0,"character":0}}
    return {"start":{"line":span.start_line-1,"character":span.start_column-1},
            "end":{"line":span.end_line-1,"character":span.end_column-1}}

def lsp_diagnostics(text,uri=None):
    _,ds=validate_source(text,uri)
    return [{"range":_lsp_range(d.span),"severity":1,"code":d.code,
             "source":"aodsl","message":d.message} for d in ds]


KEYWORDS=["RULE","VERSION","PRIORITY","WHEN","EVENT","AND","OR","NOT","THEN","STOP","TRUE","FALSE","EXISTS","MISSING"]
ACTIONS=["DISPATCH","CREATE","INVALIDATE","TRANSITION"]
CAPABILITIES=["RESEARCH_SOURCE","CALCULATE_FINANCIAL_METRIC","VERIFY_CLAIM","ADVERSARIAL_ANALYSIS"]

def _word_at(text,line,char):
    lines=text.splitlines()
    if line<0 or line>=len(lines): return ""
    ln=lines[line]; char=max(0,min(char,len(ln)))
    a=char
    while a>0 and (ln[a-1].isalnum() or ln[a-1] in "_."): a-=1
    b=char
    while b<len(ln) and (ln[b].isalnum() or ln[b] in "_."): b+=1
    return ln[a:b]

def completions():
    return ([{"label":x,"kind":14,"detail":"AODSL keyword"} for x in KEYWORDS]+
            [{"label":x,"kind":3,"detail":"AODSL action"} for x in ACTIONS]+
            [{"label":x,"kind":12,"detail":"AODSL capability"} for x in CAPABILITIES])

def hover_for(text,line,char):
    w=_word_at(text,line,char)
    docs={
      "RULE":"Declares a deterministic AODSL rule.",
      "PRIORITY":"Higher priority rules are evaluated first.",
      "DISPATCH":"Dispatches a capability through capability indirection.",
      "CREATE":"Creates a control-plane entity.",
      "INVALIDATE":"Invalidates the target claim/control entity.",
      "TRANSITION":"Requests a deterministic stage transition.",
      "EXISTS":"Three-valued-safe existence predicate.",
      "MISSING":"Three-valued-safe missing-value predicate.",
    }
    if w in docs:return {"contents":{"kind":"markdown","value":f"**{w}**\\n\\n{docs[w]}"}}
    if w in CAPABILITIES:return {"contents":{"kind":"markdown","value":f"**Capability `{w}`**\\n\\nResolved through the Capability Registry."}}
    return None

def document_symbols(text):
    out=[]
    for i,line in enumerate(text.splitlines()):
        m=re.match(r"\s*RULE\s+([A-Za-z_][A-Za-z0-9_]*)",line)
        if m:
            start=m.start(1); end=m.end(1)
            out.append({"name":m.group(1),"kind":12,
              "range":{"start":{"line":i,"character":0},"end":{"line":i,"character":len(line)}},
              "selectionRange":{"start":{"line":i,"character":start},"end":{"line":i,"character":end}}})
    return out

def definition_for(text,line,char,uri):
    w=_word_at(text,line,char)
    for sym in document_symbols(text):
        if sym["name"]==w:
            return {"uri":uri,"range":sym["selectionRange"]}
    return None

class AODSLServer:
    def __init__(self): self.docs={}; self.shutdown=False
    def handle(self,msg):
        method=msg.get("method"); params=msg.get("params",{}); mid=msg.get("id")
        if method=="initialize":
            return {"jsonrpc":"2.0","id":mid,"result":{"capabilities":{
                "textDocumentSync":1,
                 "diagnosticProvider":{"interFileDependencies":False,"workspaceDiagnostics":False},
                "hoverProvider":True,
                "completionProvider":{"triggerCharacters":["."," "]},
                "documentSymbolProvider":True,
                "definitionProvider":True}}}
        if method=="initialized": return None
        if method=="shutdown":
            self.shutdown=True
            return {"jsonrpc":"2.0","id":mid,"result":None}
        if method=="exit": return "__EXIT__"
        if method=="textDocument/completion":
            return {"jsonrpc":"2.0","id":mid,"result":{"isIncomplete":False,"items":completions()}}
        if method=="textDocument/hover":
            uri=params["textDocument"]["uri"]; p=params["position"]
            return {"jsonrpc":"2.0","id":mid,"result":hover_for(self.docs.get(uri,""),p["line"],p["character"])}
        if method=="textDocument/documentSymbol":
            uri=params["textDocument"]["uri"]
            return {"jsonrpc":"2.0","id":mid,"result":document_symbols(self.docs.get(uri,""))}
        if method=="textDocument/definition":
            uri=params["textDocument"]["uri"]; p=params["position"]
            return {"jsonrpc":"2.0","id":mid,"result":definition_for(self.docs.get(uri,""),p["line"],p["character"],uri)}
        if method=="textDocument/didOpen":
            td=params["textDocument"]; self.docs[td["uri"]]=td["text"]
            return self._publish(td["uri"])
        if method=="textDocument/didChange":
            uri=params["textDocument"]["uri"]
            changes=params.get("contentChanges",[])
            if changes: self.docs[uri]=changes[-1]["text"]
            return self._publish(uri)
        if method=="textDocument/didClose":
            uri=params["textDocument"]["uri"]; self.docs.pop(uri,None)
            return {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics",
                    "params":{"uri":uri,"diagnostics":[]}}
        return {"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"Method not found"}} if mid is not None else None
    def _publish(self,uri):
        return {"jsonrpc":"2.0","method":"textDocument/publishDiagnostics",
                "params":{"uri":uri,"diagnostics":lsp_diagnostics(self.docs[uri],uri)}}

def _read(inp):
    headers={}
    while True:
        line=inp.readline()
        if not line:return None
        if line in (b"\r\n",b"\n"):break
        k,v=line.decode("ascii").split(":",1); headers[k.lower().strip()]=v.strip()
    n=int(headers.get("content-length","0"))
    return json.loads(inp.read(n).decode("utf-8"))

def _write(out,msg):
    raw=json.dumps(msg,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    out.write(f"Content-Length: {len(raw)}\r\n\r\n".encode("ascii")+raw); out.flush()

def serve(inp=None,out=None):
    inp=inp or sys.stdin.buffer; out=out or sys.stdout.buffer
    server=AODSLServer()
    while True:
        msg=_read(inp)
        if msg is None:break
        response=server.handle(msg)
        if response=="__EXIT__":break
        if response is not None:_write(out,response)

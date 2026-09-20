const vscode = require('vscode');
const cp = require('child_process');
let proc, buffer=Buffer.alloc(0), nextId=1;
const pending=new Map();
const diagnostics=vscode.languages.createDiagnosticCollection('aodsl');

function write(msg){
  const body=Buffer.from(JSON.stringify(msg),'utf8');
  proc.stdin.write(Buffer.from(`Content-Length: ${body.length}\r\n\r\n`,'ascii'));
  proc.stdin.write(body);
}
function request(method,params){
  const id=nextId++;
  return new Promise((resolve,reject)=>{
    pending.set(id,{resolve,reject});
    write({jsonrpc:'2.0',id,method,params});
    setTimeout(()=>{ if(pending.has(id)){pending.delete(id);reject(new Error(`AODSL LSP timeout: ${method}`));}},5000);
  });
}
function notify(method,params){ write({jsonrpc:'2.0',method,params}); }
function publish(params){
  const uri=vscode.Uri.parse(params.uri);
  diagnostics.set(uri,params.diagnostics.map(d=>{
    const r=new vscode.Range(d.range.start.line,d.range.start.character,d.range.end.line,d.range.end.character);
    const x=new vscode.Diagnostic(r,d.message,vscode.DiagnosticSeverity.Error);
    x.code=d.code;x.source='aodsl';return x;
  }));
}
function dispatch(msg){
  if(msg.id!==undefined && pending.has(msg.id)){
    const p=pending.get(msg.id);pending.delete(msg.id);
    if(msg.error)p.reject(new Error(msg.error.message));else p.resolve(msg.result);
  } else if(msg.method==='textDocument/publishDiagnostics') publish(msg.params);
}
function parse(){
  while(true){
    const sep=buffer.indexOf('\r\n\r\n'); if(sep<0)return;
    const h=buffer.slice(0,sep).toString('ascii'), m=/Content-Length:\s*(\d+)/i.exec(h);
    if(!m){buffer=Buffer.alloc(0);return;}
    const n=Number(m[1]), start=sep+4;if(buffer.length<start+n)return;
    dispatch(JSON.parse(buffer.slice(start,start+n).toString('utf8')));
    buffer=buffer.slice(start+n);
  }
}
function td(doc){return {uri:doc.uri.toString(),languageId:'aodsl',version:doc.version,text:doc.getText()};}
function pos(doc,p){return {textDocument:{uri:doc.uri.toString()},position:{line:p.line,character:p.character}};}

async function activate(context){
  const python=vscode.workspace.getConfiguration('aodsl').get('pythonPath')||'python';
  proc=cp.spawn(python,['-m','aodsl','lsp'],{stdio:['pipe','pipe','pipe']});
  proc.stdout.on('data',b=>{buffer=Buffer.concat([buffer,b]);parse();});
  proc.stderr.on('data',b=>console.error('[AODSL LSP]',b.toString()));
  await request('initialize',{capabilities:{}});
  notify('initialized',{});
  vscode.workspace.textDocuments.filter(d=>d.languageId==='aodsl').forEach(d=>notify('textDocument/didOpen',{textDocument:td(d)}));

  const selector={language:'aodsl'};
  context.subscriptions.push(
    diagnostics,
    vscode.languages.registerCompletionItemProvider(selector,{
      async provideCompletionItems(doc,p){
        const r=await request('textDocument/completion',pos(doc,p));
        return r.items.map(i=>{const x=new vscode.CompletionItem(i.label,i.kind);x.detail=i.detail;return x;});
      }}),
    vscode.languages.registerHoverProvider(selector,{
      async provideHover(doc,p){
        const r=await request('textDocument/hover',pos(doc,p)); if(!r)return null;
        return new vscode.Hover(new vscode.MarkdownString(r.contents.value));
      }}),
    vscode.languages.registerDocumentSymbolProvider(selector,{
      async provideDocumentSymbols(doc){
        const rs=await request('textDocument/documentSymbol',{textDocument:{uri:doc.uri.toString()}});
        return rs.map(r=>new vscode.DocumentSymbol(r.name,'',r.kind,
          new vscode.Range(r.range.start.line,r.range.start.character,r.range.end.line,r.range.end.character),
          new vscode.Range(r.selectionRange.start.line,r.selectionRange.start.character,r.selectionRange.end.line,r.selectionRange.end.character)));
      }}),
    vscode.languages.registerDefinitionProvider(selector,{
      async provideDefinition(doc,p){
        const r=await request('textDocument/definition',pos(doc,p)); if(!r)return null;
        return new vscode.Location(vscode.Uri.parse(r.uri),
          new vscode.Range(r.range.start.line,r.range.start.character,r.range.end.line,r.range.end.character));
      }}),
    vscode.workspace.onDidOpenTextDocument(d=>{if(d.languageId==='aodsl')notify('textDocument/didOpen',{textDocument:td(d)});}),
    vscode.workspace.onDidChangeTextDocument(e=>{if(e.document.languageId==='aodsl')notify('textDocument/didChange',{textDocument:{uri:e.document.uri.toString(),version:e.document.version},contentChanges:[{text:e.document.getText()}]});}),
    vscode.workspace.onDidCloseTextDocument(d=>{if(d.languageId==='aodsl')notify('textDocument/didClose',{textDocument:{uri:d.uri.toString()}});})
  );
}
async function deactivate(){
  if(proc){
    try{await request('shutdown',{});notify('exit',{});}catch(_){}
    proc.kill();
  }
}
module.exports={activate,deactivate};

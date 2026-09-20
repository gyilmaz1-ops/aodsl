from dataclasses import dataclass
from typing import List, Tuple
import json, re
import aodsl.runtime as a

PRODUCTION_AODSL = r'''RULE R001 VERSION "0.1.1" PRIORITY 1000
 WHEN EVENT "verification.completed" AND event.verification.status == REJECTED AND event.verification.retry_count >= 3
 THEN INVALIDATE target=event.verification.claim_id reason=MAX_RETRY_EXCEEDED
 STOP TRUE
RULE R002 VERSION "0.1.1" PRIORITY 950
 WHEN EVENT "conflict.created" AND event.conflict.type == DIRECT_CONTRADICTION AND event.conflict.materiality == CRITICAL
 THEN DISPATCH capability=ADVERSARIAL_ANALYSIS target=event.conflict.id reason=DIRECT_CONTRADICTION
 STOP TRUE
RULE R003 VERSION "0.1.1" PRIORITY 900
 WHEN EVENT "verification.completed" AND event.verification.status == REJECTED AND event.verification.retry_count < 3
 THEN DISPATCH capability=event.verification.required_capability target=event.verification.claim_id reason=event.verification.remediation_type
 STOP TRUE
RULE R004 VERSION "0.1.1" PRIORITY 800
 WHEN EVENT "graph.changed" AND EXISTS state.claim.required_calculation AND state.claim.calculation_exists == FALSE
 THEN DISPATCH capability=CALCULATE_FINANCIAL_METRIC target=state.claim.id reason=MISSING_REQUIRED_CALCULATION
 STOP TRUE
RULE R005 VERSION "0.1.1" PRIORITY 750
 WHEN EVENT "graph.changed" AND EXISTS state.claim.id AND state.claim.verification_exists == FALSE
 THEN DISPATCH capability=VERIFY_CLAIM target=state.claim.id reason=MISSING_VERIFICATION
 STOP TRUE
RULE R006 VERSION "0.1.1" PRIORITY 700
 WHEN EVENT "graph.changed" AND state.claim.data_deficit_type == DATA_NOT_DISCLOSED
 THEN CREATE entity=DATA_DEFICIT target=state.claim.id type=DATA_NOT_DISCLOSED
 STOP TRUE
RULE R007 VERSION "0.1.1" PRIORITY 650
 WHEN EVENT "graph.changed" AND state.claim.data_deficit_type == DATA_NOT_FOUND AND state.claim.search_count < 3
 THEN DISPATCH capability=RESEARCH_SOURCE target=state.claim.id reason=DATA_NOT_FOUND
 STOP TRUE
RULE R008 VERSION "0.1.1" PRIORITY 500
 WHEN EVENT "graph.changed" AND state.stage == FUNDAMENTAL AND state.gate.required_claims_complete == TRUE AND state.gate.required_evidence_complete == TRUE AND state.gate.required_calculations_complete == TRUE AND state.gate.verification_complete == TRUE AND state.gate.critical_conflicts_resolved == TRUE AND state.gate.critical_data_deficits_registered == TRUE AND state.gate.critical_confidence >= 50
 THEN TRANSITION target=stage from=FUNDAMENTAL to=VALUATION
 STOP TRUE'''

@dataclass(frozen=True)
class Token:
    kind:str; value:str; pos:int

class Lexer:
    rx=re.compile(r'(?P<WS>\s+)|(?P<STRING>"(?:\\.|[^"\\])*")|(?P<NUMBER>\d+(?:\.\d+)?)|(?P<OP>==|!=|>=|<=|>|<|=)|(?P<IDENT>[A-Za-z_][A-Za-z0-9_.-]*)|(?P<BAD>.)')
    def tokenize(self,text):
        out=[]
        for m in self.rx.finditer(text):
            if m.lastgroup=="WS": continue
            if m.lastgroup=="BAD": raise a.AODSLError("AODSL-S001",f"Unexpected {m.group()!r}")
            out.append(Token(m.lastgroup,m.group(),m.start()))
        out.append(Token("EOF","",len(text))); return out

class Parser:
    def __init__(self,t): self.t=t; self.i=0
    def cur(self): return self.t[self.i]
    def accept(self,v):
        if self.cur().value.upper()==v:
            x=self.cur(); self.i+=1; return x
    def expect(self,v):
        x=self.accept(v)
        if not x: raise a.AODSLError("AODSL-S001",f"Expected {v}, got {self.cur().value!r}")
        return x
    def take(self,k=None):
        x=self.cur()
        if k and x.kind!=k: raise a.AODSLError("AODSL-S001",f"Expected {k}, got {x.kind}:{x.value}")
        self.i+=1; return x
    def program(self):
        r=[]
        while self.cur().kind!="EOF": r.append(self.rule())
        return r
    def rule(self):
        self.expect("RULE"); rid=self.take("IDENT").value
        self.expect("VERSION"); ver=json.loads(self.take("STRING").value)
        self.expect("PRIORITY"); pri=int(self.take("NUMBER").value)
        self.expect("WHEN"); self.expect("EVENT"); trig=json.loads(self.take("STRING").value); self.expect("AND")
        pred=self.expr()
        self.expect("THEN"); acts=[self.action()]
        stop=True
        if self.accept("STOP"): stop=self.take("IDENT").value.upper()=="TRUE"
        return a.Rule(rid,ver,pri,trig,pred,tuple(acts),stop)
    def expr(self):
        x=self.term()
        while self.accept("OR"): x=a.LogicalExpr("OR",x,self.term())
        return x
    def term(self):
        x=self.atom()
        while self.cur().value.upper()=="AND":
            self.i+=1; x=a.LogicalExpr("AND",x,self.atom())
        return x
    def atom(self):
        if self.accept("NOT"): return a.NotExpr(self.atom())
        if self.accept("EXISTS"): return a.ExistsExpr(self.value())
        if self.accept("MISSING"): return a.ExistsExpr(self.value(),True)
        l=self.value(); op=self.take("OP").value; r=self.value()
        return a.CompareExpr(op,l,r)
    def value(self):
        x=self.take()
        if x.kind=="STRING": return a.LiteralExpr(json.loads(x.value))
        if x.kind=="NUMBER": return a.LiteralExpr(float(x.value) if "." in x.value else int(x.value))
        if x.kind=="IDENT":
            u=x.value.upper()
            if u=="TRUE": return a.LiteralExpr(True)
            if u=="FALSE": return a.LiteralExpr(False)
            if u=="NULL": return a.LiteralExpr(None)
            if x.value.startswith(("event.","state.")): return a.PathExpr(tuple(x.value.split(".")))
            return a.IdentifierExpr(x.value)
        raise a.AODSLError("AODSL-S001","Expected value")
    def action(self):
        typ=self.take("IDENT").value.upper(); fields={}
        while self.cur().kind!="EOF" and self.cur().value.upper() not in ("STOP","RULE"):
            k=self.take("IDENT").value; self.expect("="); fields[k]=self.value()
        return a.Action(typ,fields)

def eir(e):
    if isinstance(e,a.LiteralExpr): return ["lit",e.value]
    if isinstance(e,a.PathExpr): return ["path",".".join(e.parts)]
    if isinstance(e,a.IdentifierExpr): return ["id",e.name]
    if isinstance(e,a.CompareExpr): return ["cmp",e.op,eir(e.left),eir(e.right)]
    if isinstance(e,a.LogicalExpr): return ["logic",e.op,eir(e.left),eir(e.right)]
    if isinstance(e,a.NotExpr): return ["not",eir(e.expr)]
    if isinstance(e,a.ExistsExpr): return ["missing" if e.missing else "exists",eir(e.expr)]
    raise TypeError(type(e).__name__)

def rir(r):
    return {"rule_id":r.rule_id,"version":r.version,"priority":r.priority,"trigger":r.trigger,
            "predicate":eir(r.predicate),"actions":[{"type":x.type,"fields":{k:eir(v) for k,v in sorted(x.fields.items())}} for x in r.actions],
            "stop":r.stop}

@dataclass(frozen=True)
class CompiledRuleSet:
    rules:Tuple[a.Rule,...]; canonical_ir:Tuple[dict,...]; ir_hash:str; source_hash:str

class AODSLCompiler:
    def compile(self,source):
        parsed=Parser(Lexer().tokenize(source)).program()
        if len({r.rule_id for r in parsed})!=len(parsed): raise a.AODSLError("AODSL-E210","Duplicate rule_id")
        rules=[a.SemanticValidator().validate_rule(r) for r in parsed]
        a.ConflictAnalyzer().analyze(rules)
        rules=tuple(sorted(rules,key=lambda r:(-r.priority,r.rule_id)))
        ir=tuple(rir(r) for r in rules)
        return CompiledRuleSet(rules,ir,a.canonical_hash(ir),a.canonical_hash({"source":source}))

class CompiledRuleEngine(a.RuleEngine):
    def __init__(self,compiled,idem=None):
        if not isinstance(compiled,CompiledRuleSet): raise a.AODSLError("AODSL-E220","Production engine requires CompiledRuleSet")
        super().__init__(list(compiled.rules),idem); self.ir_hash=compiled.ir_hash; self.source_hash=compiled.source_hash

def norm(p):
    if p is None:return None
    return (p.base_state_hash,p.rule_id,p.rule_version,p.actions,p.plan_hash)

def inv023_contract_tests():
    c=AODSLCompiler(); x=c.compile(PRODUCTION_AODSL); y=c.compile(PRODUCTION_AODSL)
    assert x.ir_hash==y.ir_hash and x.canonical_ir==y.canonical_ir
    assert [r.rule_id for r in x.rules]==[f"R00{i}" for i in range(1,9)]
    old=a.RuleEngine(a.production_rules()); new=CompiledRuleEngine(x)
    cases=[
    ("E1","verification.completed",{"verification":{"status":"REJECTED","retry_count":3,"claim_id":"C1","required_capability":"RESEARCH_SOURCE","remediation_type":"MISSING_SOURCE"}},{"claim":{"id":"C1"}}),
    ("E2","verification.completed",{"verification":{"status":"REJECTED","retry_count":2,"claim_id":"C1","required_capability":"RESEARCH_SOURCE","remediation_type":"MISSING_SOURCE"}},{"claim":{"id":"C1"}}),
    ("E3","conflict.created",{"conflict":{"id":"CF1","type":"DIRECT_CONTRADICTION","materiality":"CRITICAL"}},{"claim":{"id":"C1"}}),
    ("E4","graph.changed",{},{"claim":{"id":"C1","required_calculation":"CAGR","calculation_exists":False,"verification_exists":False,"data_deficit_type":"NONE","search_count":0},"stage":"RESEARCH"}),
    ("E5","graph.changed",{},{"claim":{"id":"C1","calculation_exists":True,"verification_exists":False,"data_deficit_type":"NONE","search_count":0},"stage":"RESEARCH"}),
    ("E6","graph.changed",{},{"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"DATA_NOT_DISCLOSED","search_count":0},"stage":"RESEARCH"}),
    ("E7","graph.changed",{},{"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"DATA_NOT_FOUND","search_count":2},"stage":"RESEARCH"}),
    ("E8","graph.changed",{},{"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"NONE","search_count":0},"stage":"FUNDAMENTAL","gate":{"required_claims_complete":True,"required_evidence_complete":True,"required_calculations_complete":True,"verification_complete":True,"critical_conflicts_resolved":True,"critical_data_deficits_registered":True,"critical_confidence":50}})]
    for q in cases: assert norm(old.plan(*q))==norm(new.plan(*q)),q[0]
    try: CompiledRuleEngine(a.production_rules()); assert False
    except a.AODSLError as e: assert e.code=="AODSL-E220"
    try: c.compile(PRODUCTION_AODSL.replace("ADVERSARIAL_ANALYSIS","NO_SUCH_CAPABILITY",1)); assert False
    except a.AODSLError as e: assert e.code=="AODSL-E207"
    z=c.compile(PRODUCTION_AODSL.replace("PRIORITY 950","PRIORITY 951",1))
    assert z.source_hash!=x.source_hash and z.ir_hash!=x.ir_hash
    print("AODSL v0.3.0 INV-023: SOURCE-TO-RUNTIME INTEGRITY TESTS PASSED")
    print("Covered: lexer | parser | semantic validation | conflict analysis | canonical IR")
    print("         deterministic compile | R001-R008 equivalence | compiled-only production engine")

if __name__=="__main__": inv023_contract_tests()

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Any, Dict, List, Optional, Tuple
import json, time

class Truth(Enum):
    FALSE=0; TRUE=1; UNKNOWN=2
    def __bool__(self):
        raise TypeError('Truth is three-valued; compare explicitly')

class AODSLError(Exception):
    def __init__(self, code:str, message:str):
        super().__init__(f'{code}: {message}'); self.code=code

@dataclass(frozen=True)
class IdentifierExpr: name:str
@dataclass(frozen=True)
class PathExpr: parts:Tuple[str,...]
@dataclass(frozen=True)
class LiteralExpr: value:Any
@dataclass(frozen=True)
class CompareExpr: op:str; left:Any; right:Any
@dataclass(frozen=True)
class LogicalExpr: op:str; left:Any; right:Any
@dataclass(frozen=True)
class NotExpr: expr:Any
@dataclass(frozen=True)
class ExistsExpr: expr:Any; missing:bool=False
@dataclass(frozen=True)
class Action: type:str; fields:Dict[str,Any]
@dataclass(frozen=True)
class Rule:
    rule_id:str; version:str; priority:int; trigger:str; predicate:Any; actions:Tuple[Action,...]; stop:bool=True

# Symbol resolution is contextual, never based on identifier casing.
ENUMS={
 'VERIFICATION_STATUS': {'VERIFIED','PARTIALLY_VERIFIED','REJECTED','INCONCLUSIVE'},
 'CLAIM_STATUS': {'UNVERIFIED','VERIFIED','CONTESTED','REJECTED','SUPERSEDED','INVALIDATED'},
 'STAGE': {'RESEARCH','FUNDAMENTAL','VALUATION','BEAR_REVIEW','COMMITTEE'},
 'CONFLICT_TYPE': {'DIRECT_CONTRADICTION','TEMPORAL_CONFLICT','DEFINITION_CONFLICT','UNIT_CONFLICT','CONTEXTUAL_CONFLICT','POTENTIAL_CONFLICT'},
 'MATERIALITY': {'LOW','MEDIUM','HIGH','CRITICAL'},
 'DATA_DEFICIT_TYPE': {'NONE','DATA_NOT_FOUND','DATA_NOT_DISCLOSED','DATA_NOT_AVAILABLE'},
}
CAPABILITIES={'RESEARCH_SOURCE','CALCULATE_FINANCIAL_METRIC','VERIFY_CLAIM','ADVERSARIAL_ANALYSIS'}
PATH_TYPES={
 ('event','verification','status'):'ENUM<VERIFICATION_STATUS>',
 ('event','verification','retry_count'):'INTEGER',
 ('event','verification','claim_id'):'ENTITY_ID',
 ('event','verification','required_capability'):'CAPABILITY',
 ('event','verification','remediation_type'):'STRING',
 ('event','conflict','id'):'ENTITY_ID',
 ('event','conflict','type'):'ENUM<CONFLICT_TYPE>',
 ('event','conflict','materiality'):'ENUM<MATERIALITY>',
 ('state','claim','id'):'ENTITY_ID',
 ('state','claim','status'):'ENUM<CLAIM_STATUS>',
 ('state','claim','required_calculation'):'STRING',
 ('state','claim','calculation_exists'):'BOOLEAN',
 ('state','claim','verification_exists'):'BOOLEAN',
 ('state','claim','data_deficit_type'):'ENUM<DATA_DEFICIT_TYPE>',
 ('state','claim','search_count'):'INTEGER',
 ('state','stage'):'ENUM<STAGE>',
 ('state','gate','required_claims_complete'):'BOOLEAN',
 ('state','gate','required_evidence_complete'):'BOOLEAN',
 ('state','gate','required_calculations_complete'):'BOOLEAN',
 ('state','gate','verification_complete'):'BOOLEAN',
 ('state','gate','critical_conflicts_resolved'):'BOOLEAN',
 ('state','gate','critical_data_deficits_registered'):'BOOLEAN',
 ('state','gate','critical_confidence'):'INTEGER',
}

class SymbolResolver:
    def resolve(self, ident:IdentifierExpr, expected_type:str)->LiteralExpr:
        if expected_type.startswith('ENUM<'):
            enum=expected_type[5:-1]
            if ident.name not in ENUMS.get(enum,set()):
                raise AODSLError('AODSL-E202',f'Unknown {enum} symbol: {ident.name}')
            return LiteralExpr(ident.name)
        if expected_type=='CAPABILITY':
            if ident.name not in CAPABILITIES:
                raise AODSLError('AODSL-E207',f'Unknown capability: {ident.name}')
            return LiteralExpr(ident.name)
        # Reason codes and similar open symbols are normalized as strings.
        return LiteralExpr(ident.name)

class TypeChecker:
    def type_of(self,e)->str:
        if isinstance(e,LiteralExpr):
            if isinstance(e.value,bool): return 'BOOLEAN'
            if isinstance(e.value,int): return 'INTEGER'
            if isinstance(e.value,float): return 'DECIMAL'
            return 'STRING'
        if isinstance(e,PathExpr):
            t=PATH_TYPES.get(e.parts)
            if not t: raise AODSLError('AODSL-E201',f'Unknown path: {".".join(e.parts)}')
            return t
        if isinstance(e,IdentifierExpr): return 'IDENTIFIER'
        if isinstance(e,(CompareExpr,LogicalExpr,NotExpr,ExistsExpr)): return 'BOOLEAN'
        raise AODSLError('AODSL-E203',f'Unknown expression: {type(e).__name__}')

class SemanticValidator:
    def __init__(self): self.types=TypeChecker(); self.resolver=SymbolResolver()
    def normalize(self,e):
        if isinstance(e,CompareExpr):
            l=self.normalize(e.left); r=self.normalize(e.right)
            lt=self.types.type_of(l); rt=self.types.type_of(r)
            if isinstance(r,IdentifierExpr): r=self.resolver.resolve(r,lt); rt=self.types.type_of(r)
            if isinstance(l,IdentifierExpr): l=self.resolver.resolve(l,rt); lt=self.types.type_of(l)
            if e.op in {'>','>=','<','<='} and not ({lt,rt} <= {'INTEGER','DECIMAL'}):
                raise AODSLError('AODSL-E204',f'{e.op} requires numeric operands; got {lt}, {rt}')
            return CompareExpr(e.op,l,r)
        if isinstance(e,LogicalExpr): return LogicalExpr(e.op,self.normalize(e.left),self.normalize(e.right))
        if isinstance(e,NotExpr): return NotExpr(self.normalize(e.expr))
        if isinstance(e,ExistsExpr): return ExistsExpr(self.normalize(e.expr),e.missing)
        return e
    def validate_rule(self,r:Rule)->Rule:
        p=self.normalize(r.predicate)
        acts=[]
        required_fields={
            'DISPATCH': {'capability','target','reason'},
            'CREATE': {'entity','target','type'},
            'INVALIDATE': {'target','reason'},
            'TRANSITION': {'target','from','to'},
        }
        for a in r.actions:
            if a.type not in required_fields:
                raise AODSLError('AODSL-E208',f'Unknown action type: {a.type}')
            f=dict(a.fields)
            missing=required_fields[a.type]-set(f)
            if missing:
                raise AODSLError('AODSL-E209',f'{a.type} missing fields: {sorted(missing)}')
            if a.type=='DISPATCH' and isinstance(f.get('capability'),IdentifierExpr):
                f['capability']=self.resolver.resolve(f['capability'],'CAPABILITY')
            # Open action symbols are normalized deterministically as strings.
            for k,v in list(f.items()):
                if isinstance(v,IdentifierExpr):
                    f[k]=LiteralExpr(v.name)
            acts.append(Action(a.type,f))
        return Rule(r.rule_id,r.version,r.priority,r.trigger,p,tuple(acts),r.stop)

# Formalized MVP conflict analysis: equal priority alone is legal.
def _constraints(e, out=None):
    out={} if out is None else out
    if isinstance(e,LogicalExpr) and e.op=='AND': _constraints(e.left,out); _constraints(e.right,out)
    elif isinstance(e,CompareExpr) and e.op=='==' and isinstance(e.left,PathExpr) and isinstance(e.right,LiteralExpr): out[e.left.parts]=e.right.value
    return out

def _effects(r:Rule):
    effects=[]
    for a in r.actions:
        if a.type in {'MUTATE','TRANSITION','INVALIDATE'}:
            target=a.fields.get('target') or a.fields.get('field') or a.type
            value=a.fields.get('value') or a.fields.get('to') or 'INVALIDATED'
            effects.append((str(target),str(value)))
    return effects

class ConflictAnalyzer:
    def analyze(self,rules:List[Rule]):
        errors=[]
        for i,a in enumerate(rules):
            for b in rules[i+1:]:
                if a.trigger!=b.trigger or a.priority!=b.priority: continue
                ca,cb=_constraints(a.predicate),_constraints(b.predicate)
                overlap=all(k not in cb or cb[k]==v for k,v in ca.items())
                if not overlap: continue
                ea,eb=_effects(a),_effects(b)
                for ta,va in ea:
                    for tb,vb in eb:
                        if ta==tb and va!=vb:
                            errors.append(('AODSL-C301',a.rule_id,b.rule_id,ta,va,vb))
        if errors:
            x=errors[0]; raise AODSLError(x[0],f'Semantic rule conflict {x[1]} vs {x[2]} on {x[3]}: {x[4]} vs {x[5]}')

class Evaluator:
    def get(self,p:PathExpr,event,state):
        obj={'event':event,'state':state}
        for x in p.parts:
            if not isinstance(obj,dict) or x not in obj: return None
            obj=obj[x]
        return obj
    def value(self,e,event,state):
        if isinstance(e,LiteralExpr): return e.value
        if isinstance(e,PathExpr): return self.get(e,event,state)
        return None
    def truth(self,e,event,state)->Truth:
        if isinstance(e,CompareExpr):
            l,r=self.value(e.left,event,state),self.value(e.right,event,state)
            if l is None or r is None: return Truth.UNKNOWN
            try:
                v={'==':lambda:l==r,'!=':lambda:l!=r,'>':lambda:l>r,'>=':lambda:l>=r,'<':lambda:l<r,'<=':lambda:l<=r}[e.op]()
                return Truth.TRUE if v else Truth.FALSE
            except (TypeError,ValueError): return Truth.UNKNOWN
        if isinstance(e,ExistsExpr):
            present=self.value(e.expr,event,state) is not None
            v=(not present) if e.missing else present
            return Truth.TRUE if v else Truth.FALSE
        if isinstance(e,NotExpr):
            x=self.truth(e.expr,event,state); return Truth.UNKNOWN if x is Truth.UNKNOWN else (Truth.FALSE if x is Truth.TRUE else Truth.TRUE)
        if isinstance(e,LogicalExpr):
            a,b=self.truth(e.left,event,state),self.truth(e.right,event,state)
            if e.op=='AND':
                if Truth.FALSE in (a,b): return Truth.FALSE
                if Truth.UNKNOWN in (a,b): return Truth.UNKNOWN
                return Truth.TRUE
            if Truth.TRUE in (a,b): return Truth.TRUE
            if Truth.UNKNOWN in (a,b): return Truth.UNKNOWN
            return Truth.FALSE
        return Truth.UNKNOWN

class IdempotencyStore:
    def claim(self,key:str,ttl_seconds:int)->bool: raise NotImplementedError

class TTLIdempotencyStore(IdempotencyStore):
    # Reference adapter. Production deployment can replace it with Redis/DB SETNX semantics.
    def __init__(self,max_entries=10000): self.data={}; self.max_entries=max_entries
    def claim(self,key,ttl_seconds):
        now=time.time(); self.data={k:v for k,v in self.data.items() if v>now}
        if key in self.data:return False
        if len(self.data)>=self.max_entries:
            oldest=min(self.data,key=self.data.get); del self.data[oldest]
        self.data[key]=now+ttl_seconds; return True

@dataclass(frozen=True)
class ActionPlan:
    event_id:str; base_state_hash:str; rule_id:str; rule_version:str; actions:Tuple[Dict[str,Any],...]; plan_hash:str

def canonical_hash(x)->str:
    return sha256(json.dumps(x,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()

class RuleEngine:
    def __init__(self,rules:List[Rule],idem:Optional[IdempotencyStore]=None):
        validated=[SemanticValidator().validate_rule(r) for r in rules]
        ConflictAnalyzer().analyze(validated)
        self.rules=sorted(validated,key=lambda r:(-r.priority,r.rule_id)); self.eval=Evaluator(); self.idem=idem or TTLIdempotencyStore()
    def plan(self,event_id,event_name,event,state)->Optional[ActionPlan]:
        base=canonical_hash(state)
        for r in self.rules:
            if r.trigger!=event_name: continue
            truth=self.eval.truth(r.predicate,event,state)
            if truth is Truth.UNKNOWN:
                raise AODSLError('AODSL-R401',f'Predicate UNKNOWN in {r.rule_id}')
            if truth is not Truth.TRUE: continue
            actions=[]
            for a in r.actions:
                fields={k:(self.eval.value(v,event,state) if isinstance(v,(PathExpr,LiteralExpr)) else str(v)) for k,v in a.fields.items()}
                actions.append({'action_type':a.type,**fields})
            body={'event_id':event_id,'base_state_hash':base,'rule_id':r.rule_id,'rule_version':r.version,'actions':actions}
            return ActionPlan(event_id,base,r.rule_id,r.version,tuple(actions),canonical_hash(body))
        return None
    def commit(self,plan:ActionPlan,current_state:Dict[str,Any],ttl_seconds=86400)->List[Dict[str,Any]]:
        if canonical_hash(current_state)!=plan.base_state_hash:
            raise AODSLError('AODSL-R409','State changed after PLAN; re-plan required')
        key=canonical_hash({'rule':plan.rule_id,'version':plan.rule_version,'event':plan.event_id,'plan':plan.plan_hash})
        if not self.idem.claim(key,ttl_seconds): return []
        # Side effects are intentionally emitted, not directly applied to graph state.
        return [dict(a,source_rule=plan.rule_id,rule_version=plan.rule_version,idempotency_key=key) for a in plan.actions]

# ---------------- Contract tests ----------------

def AND(*xs):
    e=xs[0]
    for x in xs[1:]: e=LogicalExpr('AND',e,x)
    return e

def EQ(path, value): return CompareExpr('==',PathExpr(tuple(path.split('.'))),IdentifierExpr(value) if isinstance(value,str) else LiteralExpr(value))
def LT(path, value): return CompareExpr('<',PathExpr(tuple(path.split('.'))),LiteralExpr(value))
def GE(path, value): return CompareExpr('>=',PathExpr(tuple(path.split('.'))),LiteralExpr(value))
def B(path, value=True): return CompareExpr('==',PathExpr(tuple(path.split('.'))),LiteralExpr(value))

def production_rules()->List[Rule]:
    # R001 — hard safety guard: rejected claim may not retry forever.
    r1=Rule('R001','0.1.1',1000,'verification.completed',
        AND(EQ('event.verification.status','REJECTED'),GE('event.verification.retry_count',3)),
        (Action('INVALIDATE',{'target':PathExpr(('event','verification','claim_id')),
                              'reason':IdentifierExpr('MAX_RETRY_EXCEEDED')}),),True)

    # R002 — critical direct contradiction gets adversarial review.
    r2=Rule('R002','0.1.1',950,'conflict.created',
        AND(EQ('event.conflict.type','DIRECT_CONTRADICTION'),EQ('event.conflict.materiality','CRITICAL')),
        (Action('DISPATCH',{'capability':IdentifierExpr('ADVERSARIAL_ANALYSIS'),
                            'target':PathExpr(('event','conflict','id')),
                            'reason':IdentifierExpr('DIRECT_CONTRADICTION')}),),True)

    # R003 — verification remediation while retry budget remains.
    r3=Rule('R003','0.1.1',900,'verification.completed',
        AND(EQ('event.verification.status','REJECTED'),LT('event.verification.retry_count',3)),
        (Action('DISPATCH',{'capability':PathExpr(('event','verification','required_capability')),
                            'target':PathExpr(('event','verification','claim_id')),
                            'reason':PathExpr(('event','verification','remediation_type'))}),),True)

    # R004 — required calculation missing.
    r4=Rule('R004','0.1.1',800,'graph.changed',
        AND(ExistsExpr(PathExpr(('state','claim','required_calculation'))),B('state.claim.calculation_exists',False)),
        (Action('DISPATCH',{'capability':IdentifierExpr('CALCULATE_FINANCIAL_METRIC'),
                            'target':PathExpr(('state','claim','id')),
                            'reason':IdentifierExpr('MISSING_REQUIRED_CALCULATION')}),),True)

    # R005 — claim exists but has not been verified.
    r5=Rule('R005','0.1.1',750,'graph.changed',
        AND(ExistsExpr(PathExpr(('state','claim','id'))),B('state.claim.verification_exists',False)),
        (Action('DISPATCH',{'capability':IdentifierExpr('VERIFY_CLAIM'),
                            'target':PathExpr(('state','claim','id')),
                            'reason':IdentifierExpr('MISSING_VERIFICATION')}),),True)

    # R006 — company/source explicitly does not disclose the data: register deficit, do not loop.
    r6=Rule('R006','0.1.1',700,'graph.changed',
        EQ('state.claim.data_deficit_type','DATA_NOT_DISCLOSED'),
        (Action('CREATE',{'entity':IdentifierExpr('DATA_DEFICIT'),
                          'target':PathExpr(('state','claim','id')),
                          'type':IdentifierExpr('DATA_NOT_DISCLOSED')}),),True)

    # R007 — data not found is searchable; retry research only within bounded search budget.
    r7=Rule('R007','0.1.1',650,'graph.changed',
        AND(EQ('state.claim.data_deficit_type','DATA_NOT_FOUND'),LT('state.claim.search_count',3)),
        (Action('DISPATCH',{'capability':IdentifierExpr('RESEARCH_SOURCE'),
                            'target':PathExpr(('state','claim','id')),
                            'reason':IdentifierExpr('DATA_NOT_FOUND')}),),True)

    # R008 — objective Fundamental -> Valuation stage gate.
    r8=Rule('R008','0.1.1',500,'graph.changed',
        AND(EQ('state.stage','FUNDAMENTAL'),
            B('state.gate.required_claims_complete'),
            B('state.gate.required_evidence_complete'),
            B('state.gate.required_calculations_complete'),
            B('state.gate.verification_complete'),
            B('state.gate.critical_conflicts_resolved'),
            B('state.gate.critical_data_deficits_registered'),
            GE('state.gate.critical_confidence',50)),
        (Action('TRANSITION',{'target':IdentifierExpr('stage'),
                              'from':IdentifierExpr('FUNDAMENTAL'),
                              'to':IdentifierExpr('VALUATION')}),),True)
    return [r1,r2,r3,r4,r5,r6,r7,r8]

def contract_tests():
    rules=production_rules()
    eng=RuleEngine(rules)

    # R001 wins over R003 at retry_count >= 3.
    ev={'verification':{'status':'REJECTED','retry_count':3,'claim_id':'CLM-1',
                        'required_capability':'RESEARCH_SOURCE','remediation_type':'MISSING_SOURCE'}}
    s={'claim':{'id':'CLM-1'}}
    p=eng.plan('EV-001','verification.completed',ev,s)
    assert p and p.rule_id=='R001'
    out=eng.commit(p,s)
    assert out[0]['action_type']=='INVALIDATE' and out[0]['reason']=='MAX_RETRY_EXCEEDED'

    # R003 handles rejected verification while retry budget remains.
    ev['verification']['retry_count']=2
    p=eng.plan('EV-002','verification.completed',ev,s)
    assert p and p.rule_id=='R003'
    assert eng.commit(p,s)[0]['capability']=='RESEARCH_SOURCE'

    # R002 critical direct contradiction.
    cev={'conflict':{'id':'CF-1','type':'DIRECT_CONTRADICTION','materiality':'CRITICAL'}}
    p=eng.plan('EV-003','conflict.created',cev,s)
    assert p and p.rule_id=='R002'
    assert eng.commit(p,s)[0]['capability']=='ADVERSARIAL_ANALYSIS'

    # R004 before R005 due priority when both calculation and verification are missing.
    gs={'claim':{'id':'CLM-4','required_calculation':'CAGR','calculation_exists':False,
                 'verification_exists':False}}
    p=eng.plan('EV-004','graph.changed',{},gs)
    assert p and p.rule_id=='R004'

    # R005 after calculation dependency is satisfied.
    gs['claim']['calculation_exists']=True
    p=eng.plan('EV-005','graph.changed',{},gs)
    assert p and p.rule_id=='R005'

    # R006 records non-disclosure and prevents infinite research.
    ds={'claim':{'id':'CLM-6','calculation_exists':True,'verification_exists':True,
                 'data_deficit_type':'DATA_NOT_DISCLOSED'}}
    p=eng.plan('EV-006','graph.changed',{},ds)
    assert p and p.rule_id=='R006'
    assert eng.commit(p,ds)[0]['action_type']=='CREATE'

    # R007 bounded re-search.
    nf={'claim':{'id':'CLM-7','calculation_exists':True,'verification_exists':True,
                 'data_deficit_type':'DATA_NOT_FOUND','search_count':2}, 'stage':'RESEARCH'}
    p=eng.plan('EV-007','graph.changed',{},nf)
    assert p and p.rule_id=='R007'
    assert eng.commit(p,nf)[0]['capability']=='RESEARCH_SOURCE'

    # Search budget exhausted => R007 does not fire.
    nf['claim']['search_count']=3
    assert eng.plan('EV-008','graph.changed',{},nf) is None

    # R008 stage transition only when every objective gate is satisfied.
    gate_state={
        'claim':{'id':'CLM-8','calculation_exists':True,'verification_exists':True,'data_deficit_type':'NONE','search_count':0},
        'stage':'FUNDAMENTAL',
        'gate':{
            'required_claims_complete':True,
            'required_evidence_complete':True,
            'required_calculations_complete':True,
            'verification_complete':True,
            'critical_conflicts_resolved':True,
            'critical_data_deficits_registered':True,
            'critical_confidence':80,
        }
    }
    p=eng.plan('EV-009','graph.changed',{},gate_state)
    assert p and p.rule_id=='R008'
    tr=eng.commit(p,gate_state)[0]
    assert tr['action_type']=='TRANSITION' and tr['from']=='FUNDAMENTAL' and tr['to']=='VALUATION'

    # Critical confidence below threshold blocks valuation.
    low=json.loads(json.dumps(gate_state)); low['gate']['critical_confidence']=49
    assert eng.plan('EV-010','graph.changed',{},low) is None

    # UNKNOWN remains fail-safe rather than silently false.
    bad=json.loads(json.dumps(gate_state)); del bad['gate']['verification_complete']
    try:
        eng.plan('EV-011','graph.changed',{},bad)
        assert False
    except AODSLError as e:
        assert e.code=='AODSL-R401'

    # Optimistic concurrency guard still applies to production rules.
    p=eng.plan('EV-012','graph.changed',{},gate_state)
    changed=json.loads(json.dumps(gate_state)); changed['gate']['critical_confidence']=79
    try:
        eng.commit(p,changed)
        assert False
    except AODSLError as e:
        assert e.code=='AODSL-R409'

    # Idempotency remains enforced.
    fresh=RuleEngine(rules)
    p=fresh.plan('EV-013','verification.completed',ev,s)
    first=fresh.commit(p,s); second=fresh.commit(p,s)
    assert first and second==[]

    print('AODSL v0.1.1 R001-R008: ALL CONTRACT TESTS PASSED')
    print('Rules:', ', '.join(r.rule_id for r in rules))



# ============================================================
# MemoryGraph + Event-Driven Orchestration Runtime v0.1.2
# ============================================================

from dataclasses import dataclass, field
from collections import deque
from typing import Callable
import copy
import time
import hashlib


@dataclass(frozen=True)
class GraphEvent:
    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    payload: Dict[str, Any]
    caused_by: Optional[str] = None


@dataclass
class GraphNode:
    node_id: str
    node_type: str
    data: Dict[str, Any]
    version: int = 1


@dataclass(frozen=True)
class GraphEdge:
    from_id: str
    to_id: str
    edge_type: str


@dataclass
class AuditRecord:
    sequence: int
    event_id: str
    event_type: str
    snapshot_hash: str
    rule_id: Optional[str]
    plan_id: Optional[str]
    action_type: Optional[str]
    status: str
    details: Dict[str, Any] = field(default_factory=dict)


class MemoryGraph:
    """
    Deterministic in-memory graph adapter.

    Production adapters can implement the same transaction contract on top of
    PostgreSQL, Neo4j, etc. The graph itself is the source of truth; the
    RuleEngine only evaluates snapshots and emits ActionPlans.
    """

    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        self.edges: List[GraphEdge] = []
        self.stage = "RESEARCH"
        self.gate: Dict[str, Any] = {}
        self.revision = 0

    def clone(self) -> "MemoryGraph":
        return copy.deepcopy(self)

    def add_node(self, node_type: str, node_id: str, **data):
        if node_id in self.nodes:
            raise AODSLError("AODSL-G409", f"Node already exists: {node_id}")
        self.nodes[node_id] = GraphNode(node_id, node_type, dict(data))
        self.revision += 1

    def upsert_node(self, node_type: str, node_id: str, **data):
        if node_id in self.nodes:
            n = self.nodes[node_id]
            n.data.update(data)
            n.version += 1
        else:
            self.nodes[node_id] = GraphNode(node_id, node_type, dict(data))
        self.revision += 1

    def add_edge(self, from_id: str, to_id: str, edge_type: str):
        e = GraphEdge(from_id, to_id, edge_type)
        if e not in self.edges:
            self.edges.append(e)
            self.revision += 1

    def has_edge_type_from(self, from_id: str, edge_type: str) -> bool:
        return any(e.from_id == from_id and e.edge_type == edge_type for e in self.edges)

    def get(self, node_id: str) -> Optional[GraphNode]:
        return self.nodes.get(node_id)

    def canonical_state(self) -> Dict[str, Any]:
        return {
            "revision": self.revision,
            "stage": self.stage,
            "gate": copy.deepcopy(self.gate),
            "nodes": [
                {
                    "id": n.node_id,
                    "type": n.node_type,
                    "version": n.version,
                    "data": copy.deepcopy(n.data),
                }
                for n in sorted(self.nodes.values(), key=lambda x: x.node_id)
            ],
            "edges": [
                {"from": e.from_id, "to": e.to_id, "type": e.edge_type}
                for e in sorted(self.edges, key=lambda x: (x.from_id, x.edge_type, x.to_id))
            ],
        }

    def canonical_hash(self) -> str:
        raw = json.dumps(
            self.canonical_state(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def rule_state(self, entity_id: str) -> Dict[str, Any]:
        """
        Projection from MemoryGraph to the state schema consumed by R001-R008.
        Missing values remain missing intentionally so Three-Valued Logic can
        surface UNKNOWN instead of silently coercing missing data to FALSE.
        """
        state: Dict[str, Any] = {"stage": self.stage}

        if self.gate:
            state["gate"] = copy.deepcopy(self.gate)

        n = self.get(entity_id)
        if n and n.node_type == "CLAIM":
            claim = {"id": n.node_id, **copy.deepcopy(n.data)}

            # Derived dependency flags are graph facts, not LLM judgments.
            claim["calculation_exists"] = self.has_edge_type_from(n.node_id, "CALCULATION")
            claim["verification_exists"] = self.has_edge_type_from(n.node_id, "VERIFICATION")
            state["claim"] = claim

        state["edges"] = [
            {"from": e.from_id, "to": e.to_id, "type": e.edge_type}
            for e in self.edges
        ]
        return state


class EventBus:
    def __init__(self):
        self._queue = deque()
        self._seq = 0

    def emit(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        payload: Optional[Dict[str, Any]] = None,
        caused_by: Optional[str] = None,
    ) -> GraphEvent:
        self._seq += 1
        event = GraphEvent(
            event_id=f"EV-{self._seq:06d}",
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=copy.deepcopy(payload or {}),
            caused_by=caused_by,
        )
        self._queue.append(event)
        return event

    def pop(self) -> Optional[GraphEvent]:
        return self._queue.popleft() if self._queue else None

    def __len__(self):
        return len(self._queue)


class CapabilityRegistry:
    def __init__(self):
        self.providers: Dict[str, str] = {
            "RESEARCH_SOURCE": "news_research_agent_v1",
            "CALCULATE_FINANCIAL_METRIC": "calculation_agent_v1",
            "VERIFY_CLAIM": "verification_agent_v1",
            "ADVERSARIAL_ANALYSIS": "devils_advocate_agent_v1",
        }

    def resolve(self, capability: str) -> str:
        if capability not in self.providers:
            raise AODSLError("AODSL-D404", f"No provider for capability: {capability}")
        return self.providers[capability]


class AtomicGraphCommitter:
    """
    Phase 2 of Plan/Commit.

    It first checks the canonical graph hash. Mutations are applied to a clone.
    The live graph is replaced only after every action validates successfully.
    """

    def __init__(self, graph: MemoryGraph, bus: EventBus, registry: CapabilityRegistry):
        self.graph = graph
        self.bus = bus
        self.registry = registry
        self.dispatch_outbox: List[Dict[str, Any]] = []

    def commit_plan(self, plan: ActionPlan) -> List[Dict[str, Any]]:
        current_hash = self.graph.canonical_hash()
        if current_hash != plan.base_state_hash:
            raise AODSLError(
                "AODSL-R409",
                f"STALE_PLAN: expected {plan.base_state_hash}, got {current_hash}",
            )

        working = self.graph.clone()
        outbox: List[Dict[str, Any]] = []
        generated_events: List[Dict[str, Any]] = []

        for action in plan.actions:
            t = action["action_type"]

            if t == "DISPATCH":
                capability = action["capability"]
                provider = self.registry.resolve(capability)
                outbox.append({
                    **copy.deepcopy(action),
                    "provider": provider,
                    "plan_id": plan.plan_hash,
                })

            elif t == "INVALIDATE":
                target = action["target"]
                node = working.get(target)
                if not node:
                    raise AODSLError("AODSL-G404", f"INVALIDATE target not found: {target}")
                node.data["status"] = "INVALIDATED"
                node.data["invalidation_reason"] = action["reason"]
                node.version += 1
                working.revision += 1
                generated_events.append({
                    "event_type": "graph.changed",
                    "entity_type": node.node_type,
                    "entity_id": target,
                    "payload": {"mutation": "INVALIDATE", "reason": action["reason"]},
                })

            elif t == "CREATE":
                entity_type = action["entity"]
                target = action["target"]
                deficit_type = action["type"]
                new_id = f"DD-{target}-{deficit_type}"
                if not working.get(new_id):
                    working.add_node(
                        entity_type,
                        new_id,
                        target_id=target,
                        deficit_type=deficit_type,
                    )
                    working.add_edge(target, new_id, "DATA_DEFICIT")
                generated_events.append({
                    "event_type": "graph.changed",
                    "entity_type": entity_type,
                    "entity_id": target,
                    "payload": {"mutation": "CREATE", "created_id": new_id},
                })

            elif t == "TRANSITION":
                if working.stage != action["from"]:
                    raise AODSLError(
                        "AODSL-R422",
                        f"Transition source mismatch: graph={working.stage}, action={action['from']}",
                    )
                working.stage = action["to"]
                working.revision += 1
                generated_events.append({
                    "event_type": "stage.changed",
                    "entity_type": "STAGE",
                    "entity_id": "stage",
                    "payload": {"from": action["from"], "to": action["to"]},
                })

            else:
                raise AODSLError("AODSL-R400", f"Unsupported action: {t}")

        # Atomic publication of the fully validated working graph.
        self.graph.nodes = working.nodes
        self.graph.edges = working.edges
        self.graph.stage = working.stage
        self.graph.gate = working.gate
        self.graph.revision = working.revision

        # Outbox/events become visible only after the graph commit succeeds.
        self.dispatch_outbox.extend(outbox)
        for e in generated_events:
            self.bus.emit(
                e["event_type"], e["entity_type"], e["entity_id"],
                e["payload"], caused_by=plan.plan_hash
            )

        return outbox


class OrchestrationRuntime:
    """
    Event -> Graph Snapshot -> RuleEngine PLAN -> Atomic COMMIT
          -> Graph Mutation / Dispatch Outbox -> New Event
    """

    def __init__(self, rules: Optional[List[Rule]] = None):
        self.graph = MemoryGraph()
        self.bus = EventBus()
        self.registry = CapabilityRegistry()
        self.engine = RuleEngine(rules or production_rules())
        self.committer = AtomicGraphCommitter(self.graph, self.bus, self.registry)
        self.audit: List[AuditRecord] = []
        self._audit_seq = 0

    def _audit(self, event: GraphEvent, snapshot_hash: str,
               plan: Optional[ActionPlan], status: str,
               details: Optional[Dict[str, Any]] = None):
        self._audit_seq += 1
        self.audit.append(AuditRecord(
            sequence=self._audit_seq,
            event_id=event.event_id,
            event_type=event.event_type,
            snapshot_hash=snapshot_hash,
            rule_id=plan.rule_id if plan else None,
            plan_id=plan.plan_hash if plan else None,
            action_type=plan.actions[0]["action_type"] if plan and plan.actions else None,
            status=status,
            details=details or {},
        ))

    def emit(self, event_type: str, entity_type: str, entity_id: str,
             payload: Optional[Dict[str, Any]] = None):
        return self.bus.emit(event_type, entity_type, entity_id, payload)

    def process_one(self) -> Optional[Dict[str, Any]]:
        event = self.bus.pop()
        if not event:
            return None

        snapshot_hash = self.graph.canonical_hash()
        state = self.graph.rule_state(event.entity_id)

        # Event payload is already namespaced for verification/conflict events.
        event_context = copy.deepcopy(event.payload)

        try:
            plan = self.engine.plan(
                event.event_id,
                event.event_type,
                event_context,
                state,
            )
            if plan is None:
                self._audit(event, snapshot_hash, None, "NO_MATCH")
                return {"event": event, "plan": None, "outbox": []}

            # Critical invariant: the engine's snapshot must be the exact
            # MemoryGraph snapshot used by the transaction.
            body = {
                'event_id': plan.event_id,
                'base_state_hash': snapshot_hash,
                'rule_id': plan.rule_id,
                'rule_version': plan.rule_version,
                'actions': list(plan.actions),
            }
            plan = ActionPlan(
                plan.event_id,
                snapshot_hash,
                plan.rule_id,
                plan.rule_version,
                plan.actions,
                canonical_hash(body),
            )

            outbox = self.committer.commit_plan(plan)
            self._audit(event, snapshot_hash, plan, "COMMITTED", {"outbox_count": len(outbox)})
            return {"event": event, "plan": plan, "outbox": outbox}

        except AODSLError as e:
            self._audit(
                event, snapshot_hash, None, "FAILED",
                {"code": e.code, "message": str(e)}
            )
            raise

    def drain(self, max_events: int = 100) -> List[Dict[str, Any]]:
        results = []
        while len(self.bus) and len(results) < max_events:
            results.append(self.process_one())
        if len(self.bus):
            raise AODSLError(
                "AODSL-R429",
                f"Event loop exceeded max_events={max_events}; possible orchestration loop",
            )
        return results

    def replay_decision(self, audit_sequence: int, event: GraphEvent,
                        graph_snapshot: MemoryGraph) -> Optional[str]:
        """
        Deterministic replay helper: same event + same graph snapshot + same
        rule set must select the same rule_id.
        """
        record = next(x for x in self.audit if x.sequence == audit_sequence)
        if graph_snapshot.canonical_hash() != record.snapshot_hash:
            raise AODSLError("AODSL-A409", "Replay snapshot hash mismatch")

        state = graph_snapshot.rule_state(event.entity_id)
        plan = self.engine.plan(
            event.event_id + "-REPLAY",
            event.event_type,
            copy.deepcopy(event.payload),
            state,
        )
        replay_rule = plan.rule_id if plan else None
        if replay_rule != record.rule_id:
            raise AODSLError(
                "AODSL-A500",
                f"Replay divergence: recorded={record.rule_id}, replay={replay_rule}",
            )
        return replay_rule


# ============================================================
# MemoryGraph / State-Machine Contract Tests
# ============================================================

def memorygraph_contract_tests():
    # 1) R004: graph event -> calculation dispatch, no graph mutation.
    rt = OrchestrationRuntime()
    rt.graph.stage = "RESEARCH"
    rt.graph.add_node(
        "CLAIM", "CLM-100",
        status="UNVERIFIED",
        required_calculation="CAGR",
        data_deficit_type="NONE",
        search_count=0,
    )
    before = rt.graph.clone()
    ev = rt.emit("graph.changed", "CLAIM", "CLM-100", {})
    result = rt.process_one()
    assert result["plan"].rule_id == "R004"
    assert result["outbox"][0]["capability"] == "CALCULATE_FINANCIAL_METRIC"
    assert result["outbox"][0]["provider"] == "calculation_agent_v1"
    assert rt.graph.canonical_hash() == before.canonical_hash()

    # Replay exact decision from exact snapshot.
    assert rt.replay_decision(1, ev, before) == "R004"

    # 2) Add calculation evidence -> next graph.changed now selects R005.
    rt.graph.add_node("CALCULATION", "CALC-100", metric="CAGR", value=12.4)
    rt.graph.add_edge("CLM-100", "CALC-100", "CALCULATION")
    rt.emit("graph.changed", "CLAIM", "CLM-100", {})
    result = rt.process_one()
    assert result["plan"].rule_id == "R005"
    assert result["outbox"][0]["capability"] == "VERIFY_CLAIM"

    # 3) Verification rejected at retry=2 -> R003 remediation dispatch.
    rt.emit(
        "verification.completed", "CLAIM", "CLM-100",
        {"verification":{
            "status":"REJECTED",
            "retry_count":2,
            "claim_id":"CLM-100",
            "required_capability":"RESEARCH_SOURCE",
            "remediation_type":"MISSING_SOURCE",
        }}
    )
    result = rt.process_one()
    assert result["plan"].rule_id == "R003"
    assert result["outbox"][0]["provider"] == "news_research_agent_v1"

    # 4) Verification rejected at retry=3 -> R001 atomic INVALIDATE.
    rt.emit(
        "verification.completed", "CLAIM", "CLM-100",
        {"verification":{
            "status":"REJECTED",
            "retry_count":3,
            "claim_id":"CLM-100",
            "required_capability":"RESEARCH_SOURCE",
            "remediation_type":"MISSING_SOURCE",
        }}
    )
    result = rt.process_one()
    assert result["plan"].rule_id == "R001"
    assert rt.graph.get("CLM-100").data["status"] == "INVALIDATED"
    assert len(rt.bus) == 1  # graph.changed emitted only after commit.

    # Drain generated graph.changed. Existing dependencies prevent R004/R005;
    # RESEARCH stage prevents R008.
    rt.graph.add_node("VERIFICATION", "VER-100", status="REJECTED")
    rt.graph.add_edge("CLM-100", "VER-100", "VERIFICATION")
    rt.drain()

    # 5) R006 CREATE DATA_DEFICIT is an atomic graph mutation.
    rt2 = OrchestrationRuntime()
    rt2.graph.stage = "RESEARCH"
    rt2.graph.add_node(
        "CLAIM", "CLM-200",
        status="UNVERIFIED",
        data_deficit_type="DATA_NOT_DISCLOSED",
        search_count=0,
    )
    # Satisfy higher-priority R005 so R006 becomes eligible.
    rt2.graph.add_node("VERIFICATION", "VER-200", status="INCONCLUSIVE")
    rt2.graph.add_edge("CLM-200", "VER-200", "VERIFICATION")
    rt2.emit("graph.changed", "CLAIM", "CLM-200", {})
    result = rt2.process_one()
    assert result["plan"].rule_id == "R006"
    dd = rt2.graph.get("DD-CLM-200-DATA_NOT_DISCLOSED")
    assert dd is not None and dd.node_type == "DATA_DEFICIT"
    assert rt2.graph.has_edge_type_from("CLM-200", "DATA_DEFICIT")

    # 6) R008: all objective gates -> atomic FUNDAMENTAL -> VALUATION transition.
    rt3 = OrchestrationRuntime()
    rt3.graph.stage = "FUNDAMENTAL"
    rt3.graph.add_node(
        "CLAIM", "CLM-300",
        status="VERIFIED",
        data_deficit_type="NONE",
        search_count=0,
    )
    rt3.graph.add_node("VERIFICATION", "VER-300", status="VERIFIED")
    rt3.graph.add_edge("CLM-300", "VER-300", "VERIFICATION")
    rt3.graph.gate = {
        "required_claims_complete": True,
        "required_evidence_complete": True,
        "required_calculations_complete": True,
        "verification_complete": True,
        "critical_conflicts_resolved": True,
        "critical_data_deficits_registered": True,
        "critical_confidence": 80,
    }
    rt3.emit("graph.changed", "CLAIM", "CLM-300", {})
    result = rt3.process_one()
    assert result["plan"].rule_id == "R008"
    assert rt3.graph.stage == "VALUATION"
    assert len(rt3.bus) == 1
    stage_event = rt3.bus.pop()
    assert stage_event.event_type == "stage.changed"
    assert stage_event.payload == {"from":"FUNDAMENTAL","to":"VALUATION"}

    # 7) Stale plan must never mutate graph or publish dispatch.
    rt4 = OrchestrationRuntime()
    rt4.graph.stage = "RESEARCH"
    rt4.graph.add_node(
        "CLAIM", "CLM-400",
        status="UNVERIFIED",
        required_calculation="CAGR",
        data_deficit_type="NONE",
        search_count=0,
    )
    state = rt4.graph.rule_state("CLM-400")
    plan = rt4.engine.plan("EV-X", "graph.changed", {}, state)
    # Bind plan to the graph snapshot exactly as runtime does.
    body = {'event_id':plan.event_id,'base_state_hash':rt4.graph.canonical_hash(),
            'rule_id':plan.rule_id,'rule_version':plan.rule_version,'actions':list(plan.actions)}
    plan = ActionPlan(plan.event_id, rt4.graph.canonical_hash(), plan.rule_id,
                      plan.rule_version, plan.actions, canonical_hash(body))
    rt4.graph.get("CLM-400").data["status"] = "CONTESTED"
    rt4.graph.get("CLM-400").version += 1
    rt4.graph.revision += 1
    outbox_before = len(rt4.committer.dispatch_outbox)
    try:
        rt4.committer.commit_plan(plan)
        assert False, "stale plan was committed"
    except AODSLError as e:
        assert e.code == "AODSL-R409"
    assert len(rt4.committer.dispatch_outbox) == outbox_before

    # 8) Atomicity: invalid second action rolls back first mutation.
    rt5 = OrchestrationRuntime()
    rt5.graph.add_node("CLAIM", "CLM-500", status="UNVERIFIED")
    original = rt5.graph.canonical_hash()
    bad_actions = (
        {"action_type":"INVALIDATE","target":"CLM-500","reason":"TEST"},
        {"action_type":"TRANSITION","target":"stage","from":"FUNDAMENTAL","to":"VALUATION"},
    )
    bad_body={'event_id':'EV-BAD','base_state_hash':original,'rule_id':'TEST',
              'rule_version':'0.1.2','actions':list(bad_actions)}
    bad_plan = ActionPlan(
        "EV-BAD", original, "TEST", "0.1.2", bad_actions, canonical_hash(bad_body)
    )
    try:
        rt5.committer.commit_plan(bad_plan)
        assert False, "invalid transaction committed"
    except AODSLError as e:
        assert e.code == "AODSL-R422"
    assert rt5.graph.canonical_hash() == original
    assert rt5.graph.get("CLM-500").data["status"] == "UNVERIFIED"

    print("AODSL v0.1.2 MemoryGraph: ALL STATE-MACHINE CONTRACT TESTS PASSED")
    print("Pipeline: Event -> Snapshot -> Plan -> Validate -> Atomic Commit -> Mutation/Outbox -> New Event")




# ============================================================
# v0.1.3 — Persistent Event Log + Transactional Outbox
#          + Crash Recovery
# ============================================================

import sqlite3
from pathlib import Path
from contextlib import contextmanager


class SQLiteExecutionStore:
    """
    Durable control-plane journal.

    Guarantees:
      1. Graph snapshot + event completion + outbox rows commit atomically.
      2. Dispatch is decoupled from graph commit.
      3. Outbox rows survive process crashes.
      4. Event IDs and idempotency keys are unique.
      5. Pending/processing events can be recovered after restart.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_schema()

    def connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    def _init_schema(self):
        with self.connect() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS graph_state (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                revision INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                state_hash TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                caused_by TEXT,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                snapshot_hash TEXT,
                rule_id TEXT,
                plan_hash TEXT,
                error_code TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outbox (
                outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
                idempotency_key TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL,
                plan_hash TEXT NOT NULL,
                capability TEXT NOT NULL,
                provider TEXT NOT NULL,
                target TEXT,
                reason TEXT,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at REAL NOT NULL,
                dispatched_at REAL,
                FOREIGN KEY(event_id) REFERENCES event_log(event_id)
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                snapshot_hash TEXT NOT NULL,
                rule_id TEXT,
                plan_hash TEXT,
                action_type TEXT,
                status TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """)

    @contextmanager
    def tx(self):
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def persist_initial_graph(self, graph: MemoryGraph):
        state = graph.canonical_state()
        h = graph.canonical_hash()
        with self.connect() as con:
            con.execute("""
                INSERT INTO graph_state(singleton,revision,state_json,state_hash,updated_at)
                VALUES(1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision=excluded.revision,
                    state_json=excluded.state_json,
                    state_hash=excluded.state_hash,
                    updated_at=excluded.updated_at
            """, (graph.revision, json.dumps(state, sort_keys=True), h, time.time()))

    def load_graph(self) -> Optional[MemoryGraph]:
        with self.connect() as con:
            row = con.execute("SELECT state_json FROM graph_state WHERE singleton=1").fetchone()
        if not row:
            return None
        raw = json.loads(row["state_json"])
        g = MemoryGraph()
        g.stage = raw["stage"]
        g.gate = raw["gate"]
        g.revision = raw["revision"]
        for n in raw["nodes"]:
            g.nodes[n["id"]] = GraphNode(n["id"], n["type"], n["data"], n["version"])
        g.edges = [GraphEdge(e["from"], e["to"], e["type"]) for e in raw["edges"]]
        return g

    def append_event(self, event: GraphEvent):
        now = time.time()
        with self.connect() as con:
            con.execute("""
                INSERT OR IGNORE INTO event_log(
                    event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                    status,created_at,updated_at
                ) VALUES(?,?,?,?,?,?, 'PENDING', ?, ?)
            """, (
                event.event_id, event.event_type, event.entity_type, event.entity_id,
                json.dumps(event.payload, sort_keys=True), event.caused_by, now, now
            ))

    def claim_next_event(self) -> Optional[GraphEvent]:
        with self.tx() as con:
            row = con.execute("""
                SELECT * FROM event_log
                WHERE status IN ('PENDING','PROCESSING')
                ORDER BY created_at,event_id
                LIMIT 1
            """).fetchone()
            if not row:
                return None
            con.execute("""
                UPDATE event_log
                SET status='PROCESSING', attempt_count=attempt_count+1, updated_at=?
                WHERE event_id=?
            """, (time.time(), row["event_id"]))
            return GraphEvent(
                row["event_id"], row["event_type"], row["entity_type"],
                row["entity_id"], json.loads(row["payload_json"]), row["caused_by"]
            )

    def pending_outbox(self):
        with self.connect() as con:
            return con.execute("""
                SELECT * FROM outbox
                WHERE status IN ('PENDING','SENDING')
                ORDER BY outbox_id
            """).fetchall()

    def outbox_status(self, key: str) -> Optional[str]:
        with self.connect() as con:
            row = con.execute(
                "SELECT status FROM outbox WHERE idempotency_key=?", (key,)
            ).fetchone()
            return row["status"] if row else None


class DurableEventBus:
    def __init__(self, store: SQLiteExecutionStore):
        self.store = store
        self._seq = 0

    def emit(self, event_type, entity_type, entity_id, payload=None, caused_by=None):
        self._seq += 1
        event = GraphEvent(
            f"EV-{int(time.time()*1000000)}-{self._seq:06d}",
            event_type, entity_type, entity_id,
            copy.deepcopy(payload or {}), caused_by
        )
        self.store.append_event(event)
        return event


class DurableAtomicCommitter:
    def __init__(self, graph: MemoryGraph, store: SQLiteExecutionStore,
                 bus: DurableEventBus, registry: CapabilityRegistry):
        self.graph = graph
        self.store = store
        self.bus = bus
        self.registry = registry

    def commit_plan(self, event: GraphEvent, plan: ActionPlan):
        if self.graph.canonical_hash() != plan.base_state_hash:
            raise AODSLError("AODSL-R409", "STALE_PLAN")

        working = self.graph.clone()
        outbox = []
        generated_events = []

        for index, action in enumerate(plan.actions):
            t = action["action_type"]
            if t == "DISPATCH":
                provider = self.registry.resolve(action["capability"])
                idem = canonical_hash({
                    "plan_hash": plan.plan_hash,
                    "action_index": index,
                    "action": action,
                })
                outbox.append({
                    **copy.deepcopy(action),
                    "provider": provider,
                    "idempotency_key": idem,
                })

            elif t == "INVALIDATE":
                n = working.get(action["target"])
                if not n:
                    raise AODSLError("AODSL-G404", f"Missing target {action['target']}")
                n.data["status"] = "INVALIDATED"
                n.data["invalidation_reason"] = action["reason"]
                n.version += 1
                working.revision += 1
                generated_events.append(
                    ("graph.changed", n.node_type, n.node_id,
                     {"mutation":"INVALIDATE","reason":action["reason"]})
                )

            elif t == "CREATE":
                new_id = f"DD-{action['target']}-{action['type']}"
                if not working.get(new_id):
                    working.add_node(
                        action["entity"], new_id,
                        target_id=action["target"], deficit_type=action["type"]
                    )
                    working.add_edge(action["target"], new_id, "DATA_DEFICIT")
                generated_events.append(
                    ("graph.changed", action["entity"], action["target"],
                     {"mutation":"CREATE","created_id":new_id})
                )

            elif t == "TRANSITION":
                if working.stage != action["from"]:
                    raise AODSLError("AODSL-R422", "Transition source mismatch")
                working.stage = action["to"]
                working.revision += 1
                generated_events.append(
                    ("stage.changed","STAGE","stage",
                     {"from":action["from"],"to":action["to"]})
                )
            else:
                raise AODSLError("AODSL-R400", f"Unsupported action {t}")

        now = time.time()
        state_json = json.dumps(working.canonical_state(), sort_keys=True)
        state_hash = working.canonical_hash()

        # THE transaction boundary:
        # graph + event result + outbox + generated events + audit.
        with self.store.tx() as con:
            con.execute("""
                INSERT INTO graph_state(singleton,revision,state_json,state_hash,updated_at)
                VALUES(1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision=excluded.revision,
                    state_json=excluded.state_json,
                    state_hash=excluded.state_hash,
                    updated_at=excluded.updated_at
            """, (working.revision, state_json, state_hash, now))

            for o in outbox:
                con.execute("""
                    INSERT OR IGNORE INTO outbox(
                        idempotency_key,event_id,plan_hash,capability,provider,target,
                        reason,payload_json,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?, 'PENDING', ?)
                """, (
                    o["idempotency_key"], event.event_id, plan.plan_hash,
                    o["capability"], o["provider"], str(o.get("target")),
                    str(o.get("reason")), json.dumps(o, sort_keys=True), now
                ))

            for event_type, entity_type, entity_id, payload in generated_events:
                generated_id = canonical_hash({
                    "caused_by":plan.plan_hash,
                    "event_type":event_type,
                    "entity_type":entity_type,
                    "entity_id":entity_id,
                    "payload":payload,
                })[:24]
                con.execute("""
                    INSERT OR IGNORE INTO event_log(
                        event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                        status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?, 'PENDING', ?, ?)
                """, (
                    f"EV-{generated_id}", event_type, entity_type, entity_id,
                    json.dumps(payload, sort_keys=True), plan.plan_hash, now, now
                ))

            con.execute("""
                UPDATE event_log SET
                    status='COMMITTED', snapshot_hash=?, rule_id=?, plan_hash=?,
                    error_code=NULL, updated_at=?
                WHERE event_id=?
            """, (
                plan.base_state_hash, plan.rule_id, plan.plan_hash, now, event.event_id
            ))

            con.execute("""
                INSERT INTO audit_log(
                    event_id,event_type,snapshot_hash,rule_id,plan_hash,
                    action_type,status,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """, (
                event.event_id, event.event_type, plan.base_state_hash,
                plan.rule_id, plan.plan_hash,
                plan.actions[0]["action_type"] if plan.actions else None,
                "COMMITTED", json.dumps({"outbox_count":len(outbox)}), now
            ))

        self.graph.nodes = working.nodes
        self.graph.edges = working.edges
        self.graph.stage = working.stage
        self.graph.gate = working.gate
        self.graph.revision = working.revision
        return outbox


class OutboxDispatcher:
    """
    At-least-once transport + idempotency key.

    A real provider/queue must honor idempotency_key. This combination yields
    effectively-once agent task creation despite crash/retry windows.
    """

    def __init__(self, store: SQLiteExecutionStore, send: Callable[[Dict[str,Any]], None]):
        self.store = store
        self.send = send

    def flush(self, crash_after_send=False):
        sent = 0
        for row in self.store.pending_outbox():
            key = row["idempotency_key"]
            payload = json.loads(row["payload_json"])

            with self.store.connect() as con:
                con.execute("""
                    UPDATE outbox SET status='SENDING',attempt_count=attempt_count+1
                    WHERE idempotency_key=? AND status IN ('PENDING','SENDING')
                """, (key,))

            # External side effect. Receiver gets stable idempotency_key.
            self.send(payload)
            sent += 1

            if crash_after_send:
                raise RuntimeError("SIMULATED_CRASH_AFTER_SEND")

            with self.store.connect() as con:
                con.execute("""
                    UPDATE outbox SET status='DISPATCHED',dispatched_at=?,last_error=NULL
                    WHERE idempotency_key=?
                """, (time.time(), key))
        return sent


class DurableOrchestrationRuntime:
    def __init__(self, db_path: str, rules: Optional[List[Rule]]=None):
        self.store = SQLiteExecutionStore(db_path)
        loaded = self.store.load_graph()
        self.graph = loaded or MemoryGraph()
        if loaded is None:
            self.store.persist_initial_graph(self.graph)
        self.bus = DurableEventBus(self.store)
        self.registry = CapabilityRegistry()
        self.engine = RuleEngine(rules or production_rules())
        self.committer = DurableAtomicCommitter(
            self.graph, self.store, self.bus, self.registry
        )

    def persist_graph(self):
        self.store.persist_initial_graph(self.graph)

    def emit(self, event_type, entity_type, entity_id, payload=None):
        return self.bus.emit(event_type, entity_type, entity_id, payload)

    def process_next(self):
        event = self.store.claim_next_event()
        if not event:
            return None

        snapshot_hash = self.graph.canonical_hash()
        state = self.graph.rule_state(event.entity_id)

        try:
            plan = self.engine.plan(
                event.event_id, event.event_type,
                copy.deepcopy(event.payload), state
            )
            if plan is None:
                now=time.time()
                with self.store.tx() as con:
                    con.execute("""
                        UPDATE event_log SET status='NO_MATCH',snapshot_hash=?,updated_at=?
                        WHERE event_id=?
                    """,(snapshot_hash,now,event.event_id))
                    con.execute("""
                        INSERT INTO audit_log(
                            event_id,event_type,snapshot_hash,rule_id,plan_hash,
                            action_type,status,details_json,created_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                    """,(event.event_id,event.event_type,snapshot_hash,None,None,
                         None,"NO_MATCH","{}",now))
                return {"event":event,"plan":None,"outbox":[]}

            body={
                "event_id":plan.event_id,
                "base_state_hash":snapshot_hash,
                "rule_id":plan.rule_id,
                "rule_version":plan.rule_version,
                "actions":list(plan.actions),
            }
            plan=ActionPlan(
                plan.event_id,snapshot_hash,plan.rule_id,plan.rule_version,
                plan.actions,canonical_hash(body)
            )
            outbox=self.committer.commit_plan(event,plan)
            return {"event":event,"plan":plan,"outbox":outbox}

        except Exception as e:
            code=e.code if isinstance(e,AODSLError) else type(e).__name__
            with self.store.connect() as con:
                con.execute("""
                    UPDATE event_log SET status='FAILED',error_code=?,updated_at=?
                    WHERE event_id=?
                """,(code,time.time(),event.event_id))
            raise

    def recover(self):
        """
        Restart behavior:
          - graph is loaded from last committed transaction;
          - PENDING/PROCESSING events remain claimable;
          - PENDING/SENDING outbox rows remain dispatchable.
        """
        loaded=self.store.load_graph()
        if loaded:
            self.graph.nodes=loaded.nodes
            self.graph.edges=loaded.edges
            self.graph.stage=loaded.stage
            self.graph.gate=loaded.gate
            self.graph.revision=loaded.revision
        return {
            "recoverable_outbox":len(self.store.pending_outbox())
        }


# ============================================================
# v0.1.3 Crash-Recovery Contract Tests
# ============================================================

def durable_runtime_contract_tests():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"aodsl.db")

        # 1) Commit graph decision and outbox atomically.
        rt=DurableOrchestrationRuntime(db)
        rt.graph.stage="RESEARCH"
        rt.graph.add_node(
            "CLAIM","CLM-D1",
            status="UNVERIFIED",
            required_calculation="CAGR",
            data_deficit_type="NONE",
            search_count=0,
        )
        rt.persist_graph()
        ev=rt.emit("graph.changed","CLAIM","CLM-D1",{})
        result=rt.process_next()
        assert result["plan"].rule_id=="R004"
        rows=rt.store.pending_outbox()
        assert len(rows)==1
        key=rows[0]["idempotency_key"]

        # 2) Simulate process death AFTER external send but BEFORE marking
        #    the outbox row DISPATCHED.
        receiver_seen=set()
        receiver_deliveries=[]

        def idempotent_receiver(payload):
            k=payload["idempotency_key"]
            if k in receiver_seen:
                return
            receiver_seen.add(k)
            receiver_deliveries.append(payload)

        dispatcher=OutboxDispatcher(rt.store,idempotent_receiver)
        try:
            dispatcher.flush(crash_after_send=True)
            assert False
        except RuntimeError as e:
            assert str(e)=="SIMULATED_CRASH_AFTER_SEND"

        assert len(receiver_deliveries)==1
        assert rt.store.outbox_status(key)=="SENDING"

        # 3) New process recovers same durable graph and SENDING outbox row.
        rt2=DurableOrchestrationRuntime(db)
        recovery=rt2.recover()
        assert recovery["recoverable_outbox"]==1
        assert rt2.graph.get("CLM-D1") is not None

        # Retry sends same idempotency key. Receiver suppresses duplicate.
        dispatcher2=OutboxDispatcher(rt2.store,idempotent_receiver)
        assert dispatcher2.flush()==1
        assert len(receiver_deliveries)==1
        assert rt2.store.outbox_status(key)=="DISPATCHED"

        # 4) Event itself is durably marked COMMITTED and cannot be reprocessed.
        with rt2.store.connect() as con:
            row=con.execute(
                "SELECT status,rule_id,plan_hash FROM event_log WHERE event_id=?",
                (ev.event_id,)
            ).fetchone()
        assert row["status"]=="COMMITTED"
        assert row["rule_id"]=="R004"
        assert row["plan_hash"]

        # 5) Pending event survives restart before processing.
        ev2=rt2.emit(
            "verification.completed","CLAIM","CLM-D1",
            {"verification":{
                "status":"REJECTED","retry_count":2,"claim_id":"CLM-D1",
                "required_capability":"RESEARCH_SOURCE",
                "remediation_type":"MISSING_SOURCE",
            }}
        )
        rt3=DurableOrchestrationRuntime(db)
        result=rt3.process_next()
        assert result["event"].event_id==ev2.event_id
        assert result["plan"].rule_id=="R003"

        # 6) R001 graph mutation is durable across restart.
        ev3=rt3.emit(
            "verification.completed","CLAIM","CLM-D1",
            {"verification":{
                "status":"REJECTED","retry_count":3,"claim_id":"CLM-D1",
                "required_capability":"RESEARCH_SOURCE",
                "remediation_type":"MISSING_SOURCE",
            }}
        )
        result=rt3.process_next()
        assert result["plan"].rule_id=="R001"
        assert rt3.graph.get("CLM-D1").data["status"]=="INVALIDATED"

        rt4=DurableOrchestrationRuntime(db)
        assert rt4.graph.get("CLM-D1").data["status"]=="INVALIDATED"

        # 7) Generated graph.changed from R001 is in same durable event log.
        with rt4.store.connect() as con:
            generated=con.execute("""
                SELECT COUNT(*) AS c FROM event_log
                WHERE caused_by=? AND event_type='graph.changed'
            """,(result["plan"].plan_hash,)).fetchone()["c"]
        assert generated==1

        # 8) Audit journal is durable.
        with rt4.store.connect() as con:
            audit_count=con.execute(
                "SELECT COUNT(*) AS c FROM audit_log WHERE status='COMMITTED'"
            ).fetchone()["c"]
        assert audit_count>=3

    print("AODSL v0.1.3 Durable Runtime: ALL CRASH-RECOVERY CONTRACT TESTS PASSED")
    print("Semantics: Atomic Graph+Event+Outbox | Persistent Event Log | Crash Recovery | Idempotent Dispatch")




# ============================================================
# v0.1.4 — Failure-Injection / Chaos Contract Suite
# ============================================================

class InjectedCrash(RuntimeError):
    pass


class ChaosController:
    """Deterministic one-shot failpoint injector."""
    def __init__(self, *failpoints):
        self.armed = set(failpoints)
        self.hits = []

    def hit(self, name: str):
        self.hits.append(name)
        if name in self.armed:
            self.armed.remove(name)
            raise InjectedCrash(name)


class ChaosDurableAtomicCommitter(DurableAtomicCommitter):
    """
    Same production transaction semantics, with deterministic failpoints:
      BEFORE_TX
      AFTER_GRAPH_WRITE
      AFTER_OUTBOX_WRITE
      AFTER_EVENT_COMMITTED
      AFTER_TX_COMMIT
    """

    def __init__(self, graph, store, bus, registry, chaos: ChaosController):
        super().__init__(graph, store, bus, registry)
        self.chaos = chaos

    def commit_plan(self, event: GraphEvent, plan: ActionPlan):
        if self.graph.canonical_hash() != plan.base_state_hash:
            raise AODSLError("AODSL-R409", "STALE_PLAN")

        working = self.graph.clone()
        outbox = []
        generated_events = []

        for index, action in enumerate(plan.actions):
            t = action["action_type"]
            if t == "DISPATCH":
                provider = self.registry.resolve(action["capability"])
                idem = canonical_hash({
                    "plan_hash": plan.plan_hash,
                    "action_index": index,
                    "action": action,
                })
                outbox.append({**copy.deepcopy(action), "provider":provider,
                               "idempotency_key":idem})
            elif t == "INVALIDATE":
                n = working.get(action["target"])
                if not n:
                    raise AODSLError("AODSL-G404", f"Missing target {action['target']}")
                n.data["status"]="INVALIDATED"
                n.data["invalidation_reason"]=action["reason"]
                n.version += 1
                working.revision += 1
                generated_events.append(
                    ("graph.changed",n.node_type,n.node_id,
                     {"mutation":"INVALIDATE","reason":action["reason"]})
                )
            elif t == "CREATE":
                new_id=f"DD-{action['target']}-{action['type']}"
                if not working.get(new_id):
                    working.add_node(action["entity"],new_id,
                                     target_id=action["target"],
                                     deficit_type=action["type"])
                    working.add_edge(action["target"],new_id,"DATA_DEFICIT")
                generated_events.append(
                    ("graph.changed",action["entity"],action["target"],
                     {"mutation":"CREATE","created_id":new_id})
                )
            elif t == "TRANSITION":
                if working.stage != action["from"]:
                    raise AODSLError("AODSL-R422","Transition source mismatch")
                working.stage=action["to"]
                working.revision += 1
                generated_events.append(
                    ("stage.changed","STAGE","stage",
                     {"from":action["from"],"to":action["to"]})
                )
            else:
                raise AODSLError("AODSL-R400",f"Unsupported action {t}")

        now=time.time()
        state_json=json.dumps(working.canonical_state(),sort_keys=True)
        state_hash=working.canonical_hash()

        self.chaos.hit("BEFORE_TX")
        with self.store.tx() as con:
            con.execute("""
                INSERT INTO graph_state(singleton,revision,state_json,state_hash,updated_at)
                VALUES(1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision=excluded.revision,state_json=excluded.state_json,
                    state_hash=excluded.state_hash,updated_at=excluded.updated_at
            """,(working.revision,state_json,state_hash,now))
            self.chaos.hit("AFTER_GRAPH_WRITE")

            for o in outbox:
                con.execute("""
                    INSERT OR IGNORE INTO outbox(
                        idempotency_key,event_id,plan_hash,capability,provider,target,
                        reason,payload_json,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?, 'PENDING', ?)
                """,(o["idempotency_key"],event.event_id,plan.plan_hash,
                     o["capability"],o["provider"],str(o.get("target")),
                     str(o.get("reason")),json.dumps(o,sort_keys=True),now))
            self.chaos.hit("AFTER_OUTBOX_WRITE")

            for event_type,entity_type,entity_id,payload in generated_events:
                generated_id=canonical_hash({
                    "caused_by":plan.plan_hash,"event_type":event_type,
                    "entity_type":entity_type,"entity_id":entity_id,"payload":payload
                })[:24]
                con.execute("""
                    INSERT OR IGNORE INTO event_log(
                        event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                        status,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?, 'PENDING', ?, ?)
                """,(f"EV-{generated_id}",event_type,entity_type,entity_id,
                     json.dumps(payload,sort_keys=True),plan.plan_hash,now,now))

            con.execute("""
                UPDATE event_log SET status='COMMITTED',snapshot_hash=?,rule_id=?,
                    plan_hash=?,error_code=NULL,updated_at=? WHERE event_id=?
            """,(plan.base_state_hash,plan.rule_id,plan.plan_hash,now,event.event_id))
            self.chaos.hit("AFTER_EVENT_COMMITTED")

            con.execute("""
                INSERT INTO audit_log(
                    event_id,event_type,snapshot_hash,rule_id,plan_hash,
                    action_type,status,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """,(event.event_id,event.event_type,plan.base_state_hash,
                 plan.rule_id,plan.plan_hash,
                 plan.actions[0]["action_type"] if plan.actions else None,
                 "COMMITTED",json.dumps({"outbox_count":len(outbox)}),now))

        self.chaos.hit("AFTER_TX_COMMIT")

        self.graph.nodes=working.nodes
        self.graph.edges=working.edges
        self.graph.stage=working.stage
        self.graph.gate=working.gate
        self.graph.revision=working.revision
        return outbox


def _prepare_dispatch_runtime(db):
    rt=DurableOrchestrationRuntime(db)
    rt.graph.stage="RESEARCH"
    rt.graph.add_node("CLAIM","CLM-C",
                      status="UNVERIFIED",required_calculation="CAGR",
                      data_deficit_type="NONE",search_count=0)
    rt.persist_graph()
    ev=rt.emit("graph.changed","CLAIM","CLM-C",{})
    return rt,ev


def _build_plan(rt, event):
    state=rt.graph.rule_state(event.entity_id)
    p=rt.engine.plan(event.event_id,event.event_type,event.payload,state)
    snapshot=rt.graph.canonical_hash()
    body={"event_id":p.event_id,"base_state_hash":snapshot,"rule_id":p.rule_id,
          "rule_version":p.rule_version,"actions":list(p.actions)}
    return ActionPlan(p.event_id,snapshot,p.rule_id,p.rule_version,
                      p.actions,canonical_hash(body))


def chaos_contract_tests():
    import tempfile

    # --------------------------------------------------------
    # C01: Crash before transaction -> no partial state/outbox.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c01.db")
        rt,ev=_prepare_dispatch_runtime(db)
        claimed=rt.store.claim_next_event()
        plan=_build_plan(rt,claimed)
        committer=ChaosDurableAtomicCommitter(
            rt.graph,rt.store,rt.bus,rt.registry,ChaosController("BEFORE_TX"))
        try:
            committer.commit_plan(claimed,plan)
            assert False
        except InjectedCrash:
            pass
        restarted=DurableOrchestrationRuntime(db)
        assert len(restarted.store.pending_outbox())==0
        with restarted.store.connect() as con:
            status=con.execute("SELECT status FROM event_log WHERE event_id=?",
                               (ev.event_id,)).fetchone()["status"]
        assert status=="PROCESSING"
        # PROCESSING is recoverable.
        result=restarted.process_next()
        assert result["plan"].rule_id=="R004"
        assert len(restarted.store.pending_outbox())==1

    # --------------------------------------------------------
    # C02/C03/C04: Crash inside DB transaction at three points.
    # Every write must rollback as one unit.
    # --------------------------------------------------------
    for fp in ("AFTER_GRAPH_WRITE","AFTER_OUTBOX_WRITE","AFTER_EVENT_COMMITTED"):
        with tempfile.TemporaryDirectory() as td:
            db=str(Path(td)/f"{fp}.db")
            rt,ev=_prepare_dispatch_runtime(db)
            claimed=rt.store.claim_next_event()
            plan=_build_plan(rt,claimed)
            before=rt.graph.canonical_hash()
            committer=ChaosDurableAtomicCommitter(
                rt.graph,rt.store,rt.bus,rt.registry,ChaosController(fp))
            try:
                committer.commit_plan(claimed,plan)
                assert False
            except InjectedCrash:
                pass
            restarted=DurableOrchestrationRuntime(db)
            assert restarted.graph.canonical_hash()==before
            assert len(restarted.store.pending_outbox())==0
            with restarted.store.connect() as con:
                row=con.execute("SELECT status,plan_hash FROM event_log WHERE event_id=?",
                                (ev.event_id,)).fetchone()
            assert row["status"]=="PROCESSING"
            assert row["plan_hash"] is None

    # --------------------------------------------------------
    # C05: Crash after DB commit but before in-memory publication.
    # Restart must load committed graph/event/outbox and NOT rerun event.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c05.db")
        rt,ev=_prepare_dispatch_runtime(db)
        claimed=rt.store.claim_next_event()
        plan=_build_plan(rt,claimed)
        committer=ChaosDurableAtomicCommitter(
            rt.graph,rt.store,rt.bus,rt.registry,ChaosController("AFTER_TX_COMMIT"))
        try:
            committer.commit_plan(claimed,plan)
            assert False
        except InjectedCrash:
            pass
        restarted=DurableOrchestrationRuntime(db)
        with restarted.store.connect() as con:
            row=con.execute("SELECT status FROM event_log WHERE event_id=?",
                            (ev.event_id,)).fetchone()
        assert row["status"]=="COMMITTED"
        assert len(restarted.store.pending_outbox())==1
        # No recoverable event remains.
        assert restarted.store.claim_next_event() is None

    # --------------------------------------------------------
    # C06: Duplicate input event ID -> single durable event.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c06.db")
        rt=DurableOrchestrationRuntime(db)
        e=GraphEvent("EV-DUP","graph.changed","CLAIM","X",{},None)
        rt.store.append_event(e)
        rt.store.append_event(e)
        with rt.store.connect() as con:
            c=con.execute("SELECT COUNT(*) AS c FROM event_log WHERE event_id='EV-DUP'").fetchone()["c"]
        assert c==1

    # --------------------------------------------------------
    # C07: Duplicate dispatch after crash -> receiver-side
    # idempotency creates one logical task.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c07.db")
        rt,_=_prepare_dispatch_runtime(db)
        rt.process_next()
        logical_tasks={}
        def receiver(payload):
            logical_tasks.setdefault(payload["idempotency_key"],payload)
        d=OutboxDispatcher(rt.store,receiver)
        try:
            d.flush(crash_after_send=True)
            assert False
        except RuntimeError:
            pass
        OutboxDispatcher(rt.store,receiver).flush()
        assert len(logical_tasks)==1

    # --------------------------------------------------------
    # C08: Stale snapshot -> reject without mutation/outbox.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c08.db")
        rt,ev=_prepare_dispatch_runtime(db)
        claimed=rt.store.claim_next_event()
        plan=_build_plan(rt,claimed)
        rt.graph.get("CLM-C").data["status"]="CONTESTED"
        rt.graph.get("CLM-C").version += 1
        rt.graph.revision += 1
        before=len(rt.store.pending_outbox())
        try:
            rt.committer.commit_plan(claimed,plan)
            assert False
        except AODSLError as e:
            assert e.code=="AODSL-R409"
        assert len(rt.store.pending_outbox())==before

    # --------------------------------------------------------
    # C09: Agent timeout/failure leaves outbox retryable.
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c09.db")
        rt,_=_prepare_dispatch_runtime(db)
        rt.process_next()
        attempts={"n":0}
        def flaky(payload):
            attempts["n"] += 1
            if attempts["n"]==1:
                raise TimeoutError("agent timeout")
        try:
            OutboxDispatcher(rt.store,flaky).flush()
            assert False
        except TimeoutError:
            pass
        row=rt.store.pending_outbox()[0]
        assert row["status"]=="SENDING"
        # Next dispatcher can retry.
        OutboxDispatcher(rt.store,flaky).flush()
        assert attempts["n"]==2
        assert len(rt.store.pending_outbox())==0

    # --------------------------------------------------------
    # C10: Poison event is isolated as FAILED; later events
    # remain processable (no global queue deadlock).
    # --------------------------------------------------------
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"c10.db")
        rt=DurableOrchestrationRuntime(db)
        rt.graph.stage="RESEARCH"
        rt.graph.add_node("CLAIM","GOOD",status="UNVERIFIED",
                          required_calculation="CAGR",
                          data_deficit_type="NONE",search_count=0)
        rt.persist_graph()
        poison=rt.emit("verification.completed","CLAIM","GOOD",
                       {"verification":{"status":"REJECTED"}})  # missing runtime paths
        good=rt.emit("graph.changed","CLAIM","GOOD",{})
        try:
            rt.process_next()
            assert False
        except AODSLError as e:
            assert e.code=="AODSL-R401"
        with rt.store.connect() as con:
            ps=con.execute("SELECT status FROM event_log WHERE event_id=?",
                           (poison.event_id,)).fetchone()["status"]
        assert ps=="FAILED"
        result=rt.process_next()
        assert result["event"].event_id==good.event_id
        assert result["plan"].rule_id=="R004"

    print("AODSL v0.1.4 Chaos Suite: ALL FAILURE-INJECTION CONTRACT TESTS PASSED")
    print("Covered: pre-tx crash | in-tx rollback | post-commit crash | duplicate event")
    print("         duplicate dispatch | stale snapshot | agent timeout | poison event")




# ============================================================
# AODSL v0.2.0 — Bounded Causal Propagation / INV-021
# ============================================================

@dataclass(frozen=True)
class CausalGraphEvent:
    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    payload: Dict[str, Any]
    caused_by: Optional[str] = None
    correlation_id: Optional[str] = None
    causation_id: Optional[str] = None
    root_event_id: Optional[str] = None
    depth: int = 0
    max_depth: int = 32

    def fingerprint(self) -> str:
        return canonical_hash({
            "event_type": self.event_type,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "payload": self.payload,
        })


class CausalSQLiteExecutionStore(SQLiteExecutionStore):
    def _init_schema(self):
        super()._init_schema()
        with self.connect() as con:
            cols={r["name"] for r in con.execute("PRAGMA table_info(event_log)").fetchall()}
            additions={
                "correlation_id":"TEXT",
                "causation_id":"TEXT",
                "root_event_id":"TEXT",
                "depth":"INTEGER NOT NULL DEFAULT 0",
                "max_depth":"INTEGER NOT NULL DEFAULT 32",
                "fingerprint":"TEXT",
                "dead_letter_reason":"TEXT",
            }
            for name,ddl in additions.items():
                if name not in cols:
                    con.execute(f"ALTER TABLE event_log ADD COLUMN {name} {ddl}")

    def append_causal_event(self, event: CausalGraphEvent, status="PENDING",
                            dead_letter_reason=None):
        now=time.time()
        with self.connect() as con:
            con.execute("""
                INSERT OR IGNORE INTO event_log(
                    event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                    status,created_at,updated_at,correlation_id,causation_id,
                    root_event_id,depth,max_depth,fingerprint,dead_letter_reason
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,(
                event.event_id,event.event_type,event.entity_type,event.entity_id,
                json.dumps(event.payload,sort_keys=True),event.caused_by,status,now,now,
                event.correlation_id,event.causation_id,event.root_event_id,
                event.depth,event.max_depth,event.fingerprint(),dead_letter_reason
            ))

    def claim_next_causal_event(self):
        with self.tx() as con:
            row=con.execute("""
                SELECT * FROM event_log
                WHERE status IN ('PENDING','PROCESSING')
                ORDER BY created_at,event_id LIMIT 1
            """).fetchone()
            if not row:
                return None
            con.execute("""
                UPDATE event_log SET status='PROCESSING',
                    attempt_count=attempt_count+1,updated_at=? WHERE event_id=?
            """,(time.time(),row["event_id"]))
            return CausalGraphEvent(
                row["event_id"],row["event_type"],row["entity_type"],row["entity_id"],
                json.loads(row["payload_json"]),row["caused_by"],
                row["correlation_id"],row["causation_id"],row["root_event_id"],
                row["depth"],row["max_depth"]
            )

    def fingerprint_count(self, correlation_id: str, fingerprint: str) -> int:
        with self.connect() as con:
            return con.execute("""
                SELECT COUNT(*) AS c FROM event_log
                WHERE correlation_id=? AND fingerprint=?
            """,(correlation_id,fingerprint)).fetchone()["c"]

    def mark_dead_letter(self, event_id: str, reason: str, code: str):
        with self.connect() as con:
            con.execute("""
                UPDATE event_log SET status='DEAD_LETTER',
                    dead_letter_reason=?,error_code=?,updated_at=?
                WHERE event_id=?
            """,(reason,code,time.time(),event_id))


class CausalEventBus:
    def __init__(self, store: CausalSQLiteExecutionStore,
                 default_max_depth=32, cycle_repeat_limit=3):
        self.store=store
        self.default_max_depth=default_max_depth
        self.cycle_repeat_limit=cycle_repeat_limit
        self._seq=0

    def _id(self):
        self._seq += 1
        return f"EV-{int(time.time()*1000000)}-{self._seq:06d}"

    def emit_root(self,event_type,entity_type,entity_id,payload=None,max_depth=None):
        eid=self._id()
        ev=CausalGraphEvent(
            eid,event_type,entity_type,entity_id,copy.deepcopy(payload or {}),
            None,eid,None,eid,0,
            self.default_max_depth if max_depth is None else max_depth
        )
        self.store.append_causal_event(ev)
        return ev

    def emit_child(self,parent:CausalGraphEvent,event_type,entity_type,entity_id,
                   payload=None,caused_by=None):
        eid=self._id()
        depth=parent.depth+1
        ev=CausalGraphEvent(
            eid,event_type,entity_type,entity_id,copy.deepcopy(payload or {}),
            caused_by,parent.correlation_id,parent.event_id,parent.root_event_id,
            depth,parent.max_depth
        )

        if depth > parent.max_depth:
            self.store.append_causal_event(
                ev,status="DEAD_LETTER",
                dead_letter_reason="MAX_CAUSAL_DEPTH_EXCEEDED"
            )
            with self.store.connect() as con:
                con.execute("""
                    UPDATE event_log SET error_code='AODSL-R429'
                    WHERE event_id=?
                """,(eid,))
            return ev

        prior=self.store.fingerprint_count(parent.correlation_id,ev.fingerprint())
        if prior >= self.cycle_repeat_limit:
            self.store.append_causal_event(
                ev,status="DEAD_LETTER",
                dead_letter_reason="CAUSAL_CYCLE_DETECTED"
            )
            with self.store.connect() as con:
                con.execute("""
                    UPDATE event_log SET error_code='AODSL-R430'
                    WHERE event_id=?
                """,(eid,))
            return ev

        self.store.append_causal_event(ev)
        return ev


class CausalDurableAtomicCommitter(DurableAtomicCommitter):
    def __init__(self,graph,store:CausalSQLiteExecutionStore,
                 bus:CausalEventBus,registry):
        self.graph=graph
        self.store=store
        self.bus=bus
        self.registry=registry

    def commit_plan(self,event:CausalGraphEvent,plan:ActionPlan):
        if self.graph.canonical_hash()!=plan.base_state_hash:
            raise AODSLError("AODSL-R409","STALE_PLAN")

        working=self.graph.clone()
        outbox=[]
        generated=[]

        for index,action in enumerate(plan.actions):
            t=action["action_type"]
            if t=="DISPATCH":
                provider=self.registry.resolve(action["capability"])
                idem=canonical_hash({
                    "plan_hash":plan.plan_hash,"action_index":index,"action":action
                })
                outbox.append({**copy.deepcopy(action),"provider":provider,
                               "idempotency_key":idem})
            elif t=="INVALIDATE":
                n=working.get(action["target"])
                if not n:
                    raise AODSLError("AODSL-G404",f"Missing target {action['target']}")
                n.data["status"]="INVALIDATED"
                n.data["invalidation_reason"]=action["reason"]
                n.version+=1
                working.revision+=1
                generated.append(("graph.changed",n.node_type,n.node_id,
                                  {"mutation":"INVALIDATE","reason":action["reason"]}))
            elif t=="CREATE":
                new_id=f"DD-{action['target']}-{action['type']}"
                if not working.get(new_id):
                    working.add_node(action["entity"],new_id,
                                     target_id=action["target"],
                                     deficit_type=action["type"])
                    working.add_edge(action["target"],new_id,"DATA_DEFICIT")
                generated.append(("graph.changed",action["entity"],action["target"],
                                  {"mutation":"CREATE","created_id":new_id}))
            elif t=="TRANSITION":
                if working.stage!=action["from"]:
                    raise AODSLError("AODSL-R422","Transition source mismatch")
                working.stage=action["to"]
                working.revision+=1
                generated.append(("stage.changed","STAGE","stage",
                                  {"from":action["from"],"to":action["to"]}))
            else:
                raise AODSLError("AODSL-R400",f"Unsupported action {t}")

        now=time.time()
        state_json=json.dumps(working.canonical_state(),sort_keys=True)
        state_hash=working.canonical_hash()

        # Graph/event/outbox/generated causal events/audit remain one transaction.
        with self.store.tx() as con:
            con.execute("""
                INSERT INTO graph_state(singleton,revision,state_json,state_hash,updated_at)
                VALUES(1,?,?,?,?)
                ON CONFLICT(singleton) DO UPDATE SET
                    revision=excluded.revision,state_json=excluded.state_json,
                    state_hash=excluded.state_hash,updated_at=excluded.updated_at
            """,(working.revision,state_json,state_hash,now))

            for o in outbox:
                con.execute("""
                    INSERT OR IGNORE INTO outbox(
                        idempotency_key,event_id,plan_hash,capability,provider,target,
                        reason,payload_json,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?, 'PENDING', ?)
                """,(o["idempotency_key"],event.event_id,plan.plan_hash,
                     o["capability"],o["provider"],str(o.get("target")),
                     str(o.get("reason")),json.dumps(o,sort_keys=True),now))

            for event_type,entity_type,entity_id,payload in generated:
                child_depth=event.depth+1
                child_id="EV-"+canonical_hash({
                    "parent":event.event_id,"plan":plan.plan_hash,
                    "event_type":event_type,"entity_type":entity_type,
                    "entity_id":entity_id,"payload":payload
                })[:24]
                child=CausalGraphEvent(
                    child_id,event_type,entity_type,entity_id,payload,plan.plan_hash,
                    event.correlation_id,event.event_id,event.root_event_id,
                    child_depth,event.max_depth
                )
                fp=child.fingerprint()
                prior=con.execute("""
                    SELECT COUNT(*) AS c FROM event_log
                    WHERE correlation_id=? AND fingerprint=?
                """,(event.correlation_id,fp)).fetchone()["c"]

                status="PENDING"
                reason=None
                error=None
                if child_depth>event.max_depth:
                    status="DEAD_LETTER"
                    reason="MAX_CAUSAL_DEPTH_EXCEEDED"
                    error="AODSL-R429"
                elif prior>=self.bus.cycle_repeat_limit:
                    status="DEAD_LETTER"
                    reason="CAUSAL_CYCLE_DETECTED"
                    error="AODSL-R430"

                con.execute("""
                    INSERT OR IGNORE INTO event_log(
                        event_id,event_type,entity_type,entity_id,payload_json,caused_by,
                        status,created_at,updated_at,correlation_id,causation_id,
                        root_event_id,depth,max_depth,fingerprint,dead_letter_reason,error_code
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,(child.event_id,child.event_type,child.entity_type,child.entity_id,
                     json.dumps(child.payload,sort_keys=True),child.caused_by,status,now,now,
                     child.correlation_id,child.causation_id,child.root_event_id,
                     child.depth,child.max_depth,fp,reason,error))

            con.execute("""
                UPDATE event_log SET status='COMMITTED',snapshot_hash=?,rule_id=?,
                    plan_hash=?,error_code=NULL,updated_at=? WHERE event_id=?
            """,(plan.base_state_hash,plan.rule_id,plan.plan_hash,now,event.event_id))

            con.execute("""
                INSERT INTO audit_log(
                    event_id,event_type,snapshot_hash,rule_id,plan_hash,
                    action_type,status,details_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
            """,(event.event_id,event.event_type,plan.base_state_hash,plan.rule_id,
                 plan.plan_hash,plan.actions[0]["action_type"] if plan.actions else None,
                 "COMMITTED",json.dumps({"outbox_count":len(outbox)}),now))

        self.graph.nodes=working.nodes
        self.graph.edges=working.edges
        self.graph.stage=working.stage
        self.graph.gate=working.gate
        self.graph.revision=working.revision
        return outbox


class CausalDurableRuntime:
    def __init__(self,db_path,rules=None,max_depth=32,cycle_repeat_limit=3):
        self.store=CausalSQLiteExecutionStore(db_path)
        loaded=self.store.load_graph()
        self.graph=loaded or MemoryGraph()
        if loaded is None:
            self.store.persist_initial_graph(self.graph)
        self.bus=CausalEventBus(self.store,max_depth,cycle_repeat_limit)
        self.registry=CapabilityRegistry()
        self.engine=RuleEngine(rules or production_rules())
        self.committer=CausalDurableAtomicCommitter(
            self.graph,self.store,self.bus,self.registry
        )

    def persist_graph(self):
        self.store.persist_initial_graph(self.graph)

    def emit(self,event_type,entity_type,entity_id,payload=None,max_depth=None):
        return self.bus.emit_root(event_type,entity_type,entity_id,payload,max_depth)

    def process_next(self):
        event=self.store.claim_next_causal_event()
        if not event:
            return None
        snapshot=self.graph.canonical_hash()
        state=self.graph.rule_state(event.entity_id)
        try:
            p=self.engine.plan(event.event_id,event.event_type,
                               copy.deepcopy(event.payload),state)
            if p is None:
                with self.store.connect() as con:
                    con.execute("""
                        UPDATE event_log SET status='NO_MATCH',snapshot_hash=?,updated_at=?
                        WHERE event_id=?
                    """,(snapshot,time.time(),event.event_id))
                return {"event":event,"plan":None,"outbox":[]}
            body={"event_id":p.event_id,"base_state_hash":snapshot,"rule_id":p.rule_id,
                  "rule_version":p.rule_version,"actions":list(p.actions)}
            p=ActionPlan(p.event_id,snapshot,p.rule_id,p.rule_version,
                         p.actions,canonical_hash(body))
            out=self.committer.commit_plan(event,p)
            return {"event":event,"plan":p,"outbox":out}
        except Exception as exc:
            code=exc.code if isinstance(exc,AODSLError) else type(exc).__name__
            with self.store.connect() as con:
                con.execute("""
                    UPDATE event_log SET status='FAILED',error_code=?,updated_at=?
                    WHERE event_id=?
                """,(code,time.time(),event.event_id))
            raise


# ------------------------------------------------------------
# INV-021 Machine-Enforced Contract Tests
# ------------------------------------------------------------

def inv021_contract_tests():
    import tempfile

    # 1. Child inherits correlation/root, causation=parent, depth+1.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c1.db"),max_depth=4)
        root=rt.emit("graph.changed","CLAIM","C1",{"x":1})
        child=rt.bus.emit_child(root,"graph.changed","CLAIM","C2",{"x":2})
        assert child.correlation_id==root.event_id
        assert child.root_event_id==root.event_id
        assert child.causation_id==root.event_id
        assert child.depth==1 and child.max_depth==4

    # 2. Depth boundary accepted, boundary+1 dead-lettered.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c2.db"),max_depth=2)
        r=rt.emit("x.e","X","1",{})
        c1=rt.bus.emit_child(r,"x.e","X","2",{})
        c2=rt.bus.emit_child(c1,"x.e","X","3",{})
        c3=rt.bus.emit_child(c2,"x.e","X","4",{})
        with rt.store.connect() as con:
            row=con.execute("SELECT status,error_code,dead_letter_reason FROM event_log WHERE event_id=?",
                            (c3.event_id,)).fetchone()
        assert row["status"]=="DEAD_LETTER"
        assert row["error_code"]=="AODSL-R429"
        assert row["dead_letter_reason"]=="MAX_CAUSAL_DEPTH_EXCEEDED"

    # 3. Repeated fingerprint in same correlation chain is cycle-limited.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c3.db"),max_depth=20,cycle_repeat_limit=2)
        r=rt.emit("loop.e","X","A",{"v":1})
        c1=rt.bus.emit_child(r,"loop.e","X","A",{"v":1})
        # root + c1 already count as two identical fingerprints.
        c2=rt.bus.emit_child(c1,"loop.e","X","A",{"v":1})
        with rt.store.connect() as con:
            row=con.execute("SELECT status,error_code,dead_letter_reason FROM event_log WHERE event_id=?",
                            (c2.event_id,)).fetchone()
        assert row["status"]=="DEAD_LETTER"
        assert row["error_code"]=="AODSL-R430"
        assert row["dead_letter_reason"]=="CAUSAL_CYCLE_DETECTED"

    # 4. Same fingerprint in different correlation chain is independent.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c4.db"),cycle_repeat_limit=1)
        r1=rt.emit("same.e","X","A",{"v":1})
        r2=rt.emit("same.e","X","A",{"v":1})
        with rt.store.connect() as con:
            s1=con.execute("SELECT status FROM event_log WHERE event_id=?",(r1.event_id,)).fetchone()["status"]
            s2=con.execute("SELECT status FROM event_log WHERE event_id=?",(r2.event_id,)).fetchone()["status"]
        assert s1=="PENDING" and s2=="PENDING"

    # 5. Generated mutation event inherits causal metadata transactionally.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c5.db"),max_depth=8)
        rt.graph.stage="RESEARCH"
        rt.graph.add_node("CLAIM","C5",status="UNVERIFIED",
                          data_deficit_type="NONE",search_count=0)
        rt.persist_graph()
        root=rt.emit("verification.completed","CLAIM","C5",{
            "verification":{
                "status":"REJECTED","retry_count":3,"claim_id":"C5",
                "required_capability":"RESEARCH_SOURCE","remediation_type":"MISSING_SOURCE"
            }
        })
        result=rt.process_next()
        assert result["plan"].rule_id=="R001"
        with rt.store.connect() as con:
            row=con.execute("""
                SELECT * FROM event_log
                WHERE causation_id=? AND event_type='graph.changed'
            """,(root.event_id,)).fetchone()
        assert row is not None
        assert row["correlation_id"]==root.event_id
        assert row["root_event_id"]==root.event_id
        assert row["depth"]==1

    # 6. DEAD_LETTER events are never claimable.
    with tempfile.TemporaryDirectory() as td:
        rt=CausalDurableRuntime(str(Path(td)/"c6.db"),max_depth=0)
        root=rt.emit("root.e","X","1",{})
        dead=rt.bus.emit_child(root,"child.e","X","2",{})
        # Claim root, then no child because it is dead-lettered.
        claimed=rt.store.claim_next_causal_event()
        assert claimed.event_id==root.event_id
        with rt.store.connect() as con:
            con.execute("UPDATE event_log SET status='COMMITTED' WHERE event_id=?",(root.event_id,))
        assert rt.store.claim_next_causal_event() is None

    print("AODSL v0.2.0 INV-021: ALL BOUNDED-CAUSAL-PROPAGATION TESTS PASSED")
    print("Covered: correlation | causation | root | depth guard | cycle fingerprint | dead letter")




# ============================================================
# AODSL v0.2.1 — Bounded TTL/LRU Idempotency Resource Safety
# INV-022
# ============================================================

from collections import OrderedDict
import threading


class BoundedTTLIdempotencyStore(IdempotencyStore):
    """
    Process-local evaluation deduplication store.

    Guarantees:
      - TTL expiration
      - hard max_entries memory bound
      - LRU eviction when capacity is reached
      - explicit purge_expired()
      - optional background sweeper
      - thread-safe claim/contains/size
    This store is NOT the durable external-side-effect idempotency boundary.
    Durable dispatch identity remains protected by the persistent outbox UNIQUE key.
    """

    def __init__(self, ttl_seconds=3600.0, max_entries=10000,
                 sweep_interval_seconds=None, clock=None):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self.ttl_seconds=float(ttl_seconds)
        self.max_entries=int(max_entries)
        self._clock=clock or time.time
        self._entries=OrderedDict()  # key -> expires_at; order = LRU
        self._lock=threading.RLock()
        self._stop=threading.Event()
        self._thread=None
        if sweep_interval_seconds is not None:
            if sweep_interval_seconds <= 0:
                raise ValueError("sweep_interval_seconds must be > 0")
            self._thread=threading.Thread(
                target=self._sweeper,
                args=(float(sweep_interval_seconds),),
                name="aodsl-idempotency-sweeper",
                daemon=True,
            )
            self._thread.start()

    def _purge_expired_locked(self, now):
        expired=[k for k,exp in self._entries.items() if exp <= now]
        for k in expired:
            self._entries.pop(k,None)
        return len(expired)

    def purge_expired(self):
        with self._lock:
            return self._purge_expired_locked(self._clock())

    def claim(self, key: str) -> bool:
        now=self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            if key in self._entries:
                # Touch duplicate access to make eviction truly LRU.
                self._entries.move_to_end(key)
                return False
            while len(self._entries) >= self.max_entries:
                self._entries.popitem(last=False)
            self._entries[key]=now+self.ttl_seconds
            return True

    def contains(self, key: str) -> bool:
        now=self._clock()
        with self._lock:
            self._purge_expired_locked(now)
            if key not in self._entries:
                return False
            self._entries.move_to_end(key)
            return True

    def size(self) -> int:
        with self._lock:
            self._purge_expired_locked(self._clock())
            return len(self._entries)

    def _sweeper(self, interval):
        while not self._stop.wait(interval):
            self.purge_expired()

    def close(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class ManualClock:
    """Deterministic clock for resource-safety contract tests."""
    def __init__(self,start=0.0):
        self.now=float(start)
    def __call__(self):
        return self.now
    def advance(self,seconds):
        self.now += float(seconds)


# ------------------------------------------------------------
# INV-022 — Bounded Ephemeral Idempotency State
# ------------------------------------------------------------

def inv022_contract_tests():
    # 1) Hard memory bound: size never exceeds max_entries.
    c=ManualClock()
    s=BoundedTTLIdempotencyStore(ttl_seconds=100,max_entries=3,clock=c)
    for k in ("A","B","C","D","E"):
        assert s.claim(k) is True
        assert s.size() <= 3
    assert s.size()==3
    assert not s.contains("A")
    assert not s.contains("B")
    assert s.contains("C") and s.contains("D") and s.contains("E")
    s.close()

    # 2) True LRU: touching A protects it; B is evicted.
    c=ManualClock()
    s=BoundedTTLIdempotencyStore(ttl_seconds=100,max_entries=3,clock=c)
    assert s.claim("A") and s.claim("B") and s.claim("C")
    assert s.contains("A")  # touch A => B,C,A
    assert s.claim("D")     # evict B
    assert not s.contains("B")
    assert s.contains("A") and s.contains("C") and s.contains("D")
    s.close()

    # 3) Duplicate claim is rejected during TTL.
    c=ManualClock()
    s=BoundedTTLIdempotencyStore(ttl_seconds=10,max_entries=10,clock=c)
    assert s.claim("X") is True
    assert s.claim("X") is False
    c.advance(9.999)
    assert s.claim("X") is False
    s.close()

    # 4) Expired key becomes claimable again.
    c=ManualClock()
    s=BoundedTTLIdempotencyStore(ttl_seconds=10,max_entries=10,clock=c)
    assert s.claim("X")
    c.advance(10)
    assert not s.contains("X")
    assert s.claim("X")
    s.close()

    # 5) Explicit purge works without a new claim.
    c=ManualClock()
    s=BoundedTTLIdempotencyStore(ttl_seconds=5,max_entries=10,clock=c)
    s.claim("A"); s.claim("B")
    c.advance(6)
    assert s.purge_expired()==2
    assert s.size()==0
    s.close()

    # 6) Background sweeper removes expired entries even with no claim traffic.
    with BoundedTTLIdempotencyStore(
        ttl_seconds=0.05,max_entries=10,sweep_interval_seconds=0.02
    ) as s:
        assert s.claim("BG")
        deadline=time.time()+1.0
        while time.time()<deadline and s.size()!=0:
            time.sleep(0.02)
        assert s.size()==0

    # 7) Thread safety: concurrent claims for same key yield exactly one winner.
    s=BoundedTTLIdempotencyStore(ttl_seconds=10,max_entries=100)
    winners=[]
    threads=[]
    def worker():
        winners.append(s.claim("SAME"))
    for _ in range(32):
        t=threading.Thread(target=worker)
        threads.append(t); t.start()
    for t in threads: t.join()
    assert sum(1 for x in winners if x)==1
    assert s.size()==1
    s.close()

    # 8) Durable outbox idempotency remains independent from ephemeral eviction.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"durable.db")
        rt,_=_prepare_dispatch_runtime(db)
        rt.process_next()
        row=rt.store.pending_outbox()[0]
        durable_key=row["idempotency_key"]

        ephemeral=BoundedTTLIdempotencyStore(
            ttl_seconds=1,max_entries=1,clock=ManualClock()
        )
        assert ephemeral.claim(durable_key)
        assert ephemeral.claim("OTHER")  # evicts durable_key from ephemeral store
        assert not ephemeral.contains(durable_key)

        # Persistent UNIQUE-backed outbox record is still intact.
        with rt.store.connect() as con:
            count=con.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE idempotency_key=?",
                (durable_key,)
            ).fetchone()["c"]
        assert count==1
        ephemeral.close()

    # 9) Invalid resource configuration is rejected.
    for kwargs in (
        {"ttl_seconds":0,"max_entries":1},
        {"ttl_seconds":1,"max_entries":0},
        {"ttl_seconds":1,"max_entries":1,"sweep_interval_seconds":0},
    ):
        try:
            BoundedTTLIdempotencyStore(**kwargs)
            assert False
        except ValueError:
            pass

    print("AODSL v0.2.1 INV-022: ALL IDEMPOTENCY RESOURCE-SAFETY TESTS PASSED")
    print("Covered: TTL | explicit purge | background sweep | hard bound | LRU | concurrency")
    print("         durable-outbox independence | invalid configuration")


if __name__ == "__main__":
    contract_tests()
    memorygraph_contract_tests()
    durable_runtime_contract_tests()
    chaos_contract_tests()
    inv021_contract_tests()
    inv022_contract_tests()

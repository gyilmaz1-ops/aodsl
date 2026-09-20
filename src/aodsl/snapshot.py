"""
AODSL v0.4.0 — Snapshot Contract
Compiler-derived Required Path Manifest + deterministic Snapshot Hydrator.

INV-024 — Declared Snapshot Dependency
Every runtime predicate/action data dependency is declared by the compiled
manifest. Hydration is deterministic; absent required data remains absent and
therefore preserves three-valued UNKNOWN/R401 semantics rather than silently
becoming FALSE.
"""
from dataclasses import dataclass
from typing import Tuple
import copy

import aodsl.runtime as a
import aodsl.compiler as c


def _collect_expr_paths(e, out):
    if isinstance(e,a.PathExpr):
        out.add(".".join(e.parts)); return
    if isinstance(e,a.CompareExpr) or isinstance(e,a.LogicalExpr):
        _collect_expr_paths(e.left,out); _collect_expr_paths(e.right,out); return
    if isinstance(e,a.NotExpr):
        _collect_expr_paths(e.expr,out); return
    if isinstance(e,a.ExistsExpr):
        _collect_expr_paths(e.expr,out); return
    # LiteralExpr / resolved IdentifierExpr have no snapshot dependency.


@dataclass(frozen=True)
class RequiredPathManifest:
    event_paths: Tuple[str,...]
    state_paths: Tuple[str,...]
    by_rule: Tuple[tuple,...]
    manifest_hash: str


def build_required_path_manifest(compiled:c.CompiledRuleSet)->RequiredPathManifest:
    all_event=set(); all_state=set(); rows=[]
    for r in compiled.rules:
        paths=set()
        _collect_expr_paths(r.predicate,paths)
        for act in r.actions:
            for value in act.fields.values():
                _collect_expr_paths(value,paths)
        ev=tuple(sorted(p for p in paths if p.startswith("event.")))
        st=tuple(sorted(p for p in paths if p.startswith("state.")))
        all_event.update(ev); all_state.update(st)
        rows.append((r.rule_id,ev,st))
    payload={
        "event_paths":sorted(all_event),
        "state_paths":sorted(all_state),
        "by_rule":[{"rule_id":rid,"event_paths":list(ev),"state_paths":list(st)}
                   for rid,ev,st in rows]
    }
    return RequiredPathManifest(
        tuple(payload["event_paths"]),tuple(payload["state_paths"]),
        tuple(rows),a.canonical_hash(payload)
    )


_MISSING=object()

def _get(root,path_parts):
    cur=root
    for part in path_parts:
        if not isinstance(cur,dict) or part not in cur: return _MISSING
        cur=cur[part]
    return cur

def _set(root,path_parts,value):
    cur=root
    for part in path_parts[:-1]:
        cur=cur.setdefault(part,{})
    cur[path_parts[-1]]=copy.deepcopy(value)


class SnapshotHydrator:
    """
    Projects raw event/state inputs to exactly the paths declared by a rule's
    compiled manifest. Missing paths are not synthesized.
    """
    def __init__(self,manifest:RequiredPathManifest):
        self.manifest=manifest
        self._by_rule={rid:(ev,st) for rid,ev,st in manifest.by_rule}

    def hydrate_for_rule(self,rule_id,event_data,state_data):
        if rule_id not in self._by_rule:
            raise a.AODSLError("AODSL-R404",f"No snapshot manifest for rule {rule_id}")
        ev_paths,st_paths=self._by_rule[rule_id]
        ev_out={}; st_out={}
        for p in ev_paths:
            parts=p.split(".")[1:]
            v=_get(event_data,parts)
            if v is not _MISSING: _set(ev_out,parts,v)
        for p in st_paths:
            parts=p.split(".")[1:]
            v=_get(state_data,parts)
            if v is not _MISSING: _set(st_out,parts,v)
        return ev_out,st_out


class ManifestBoundRuleEngine(c.CompiledRuleEngine):
    """
    Evaluates each candidate against its compiler-declared projection.
    This prevents undeclared ambient snapshot data from affecting a rule.
    """
    def __init__(self,compiled,idem=None):
        super().__init__(compiled,idem)
        self.manifest=build_required_path_manifest(compiled)
        self.hydrator=SnapshotHydrator(self.manifest)

    def plan(self,event_id,event_name,event_data,state_data):
        # Preserve base state identity from the authoritative full state.
        base_hash=a.canonical_hash(state_data)
        candidates=[r for r in self.rules if r.trigger==event_name]
        candidates=sorted(candidates,key=lambda r:(-r.priority,r.rule_id))
        for r in candidates:
            ev,st=self.hydrator.hydrate_for_rule(r.rule_id,event_data,state_data)
            truth=self.eval.truth(r.predicate,ev,st)
            if truth is a.Truth.UNKNOWN:
                raise a.AODSLError("AODSL-R401",f"UNKNOWN predicate in {r.rule_id}")
            if truth is a.Truth.TRUE:
                actions=[]
                for act in r.actions:
                    fields={k:(self.eval.value(v,ev,st) if isinstance(v,(a.PathExpr,a.LiteralExpr)) else str(v))
                            for k,v in act.fields.items()}
                    actions.append({"action_type":act.type,**fields})
                actions=tuple(actions)
                ph=a.canonical_hash({
                    "event_id":event_id,"base_state_hash":base_hash,
                    "rule_id":r.rule_id,"rule_version":r.version,
                    "actions":actions
                })
                return a.ActionPlan(event_id,base_hash,r.rule_id,r.version,actions,ph)
        return None


def inv024_contract_tests():
    compiled=c.AODSLCompiler().compile(c.PRODUCTION_AODSL)
    manifest=build_required_path_manifest(compiled)
    manifest2=build_required_path_manifest(c.AODSLCompiler().compile(c.PRODUCTION_AODSL))
    assert manifest==manifest2

    # Compiler extracts both predicate and action dependencies.
    r3=dict((rid,(ev,st)) for rid,ev,st in manifest.by_rule)["R003"]
    assert "event.verification.status" in r3[0]
    assert "event.verification.required_capability" in r3[0]
    assert "event.verification.remediation_type" in r3[0]
    r8=dict((rid,(ev,st)) for rid,ev,st in manifest.by_rule)["R008"]
    assert "state.gate.critical_confidence" in r8[1]
    assert "state.stage" in r8[1]

    # Hydrator strips ambient undeclared data.
    h=SnapshotHydrator(manifest)
    ev,st=h.hydrate_for_rule("R004",
        {"noise":{"secret":123}},
        {"claim":{"id":"C1","required_calculation":"CAGR","calculation_exists":False,
                  "verification_exists":False,"ambient":"MUST_NOT_LEAK"},
         "unrelated":{"x":1}})
    assert "noise" not in ev and "unrelated" not in st
    assert "ambient" not in st["claim"]
    assert st["claim"]["id"]=="C1"

    # Missing required comparison operand remains absent -> UNKNOWN/R401.
    eng=ManifestBoundRuleEngine(compiled)
    try:
        eng.plan("E-MISS","verification.completed",
                 {"verification":{"status":"REJECTED","claim_id":"C1",
                                  "required_capability":"RESEARCH_SOURCE",
                                  "remediation_type":"MISSING_SOURCE"}},
                 {"claim":{"id":"C1"}})
        assert False
    except a.AODSLError as e:
        assert e.code=="AODSL-R401"

    # EXISTS keeps intentional missing-data semantics: R004 does not fire if
    # required_calculation is absent; evaluation proceeds to R005.
    p=eng.plan("E-EXISTS","graph.changed",{},
               {"claim":{"id":"C1","calculation_exists":False,"verification_exists":False,
                         "data_deficit_type":"NONE","search_count":0},
                "stage":"RESEARCH"})
    assert p.rule_id=="R005"

    # Full source-to-runtime behavior remains equivalent for a representative rule.
    p=eng.plan("E-R4","graph.changed",{},
               {"claim":{"id":"C1","required_calculation":"CAGR","calculation_exists":False,
                         "verification_exists":False,"data_deficit_type":"NONE","search_count":0},
                "stage":"RESEARCH","ambient":{"ignored":True}})
    assert p.rule_id=="R004"
    assert p.actions[0]["capability"]=="CALCULATE_FINANCIAL_METRIC"

    # Manifest identity changes if a real data dependency changes.
    changed=c.PRODUCTION_AODSL.replace(
        "state.gate.critical_confidence >= 50",
        "state.gate.critical_confidence >= state.claim.search_count",1)
    m3=build_required_path_manifest(c.AODSLCompiler().compile(changed))
    assert m3.manifest_hash!=manifest.manifest_hash
    assert "state.claim.search_count" in m3.state_paths

    print("AODSL v0.4.0 INV-024: DECLARED SNAPSHOT DEPENDENCY TESTS PASSED")
    print("Covered: required-path extraction | predicate+action dependencies | deterministic manifest")
    print("         exact projection | ambient-data isolation | missing=>UNKNOWN/R401 | EXISTS semantics")


if __name__=="__main__":
    inv024_contract_tests()

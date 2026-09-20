"""AODSL v0.5.0 — Bounded Research Exhaustion Semantics."""
import aodsl.runtime as a
import aodsl.compiler as c
import aodsl.snapshot as sc

R009_SOURCE = '''
RULE R009 VERSION "0.5.0" PRIORITY 640
 WHEN EVENT "graph.changed" AND state.claim.data_deficit_type == DATA_NOT_FOUND AND state.claim.search_count >= 3
 THEN CREATE entity=DATA_DEFICIT target=state.claim.id type=RESEARCH_EXHAUSTED
 STOP TRUE
'''
PRODUCTION_AODSL_V050 = c.PRODUCTION_AODSL + R009_SOURCE

def compile_v050():
    return c.AODSLCompiler().compile(PRODUCTION_AODSL_V050)

class ResearchBoundRuleEngine(sc.ManifestBoundRuleEngine):
    pass

def inv025_contract_tests():
    compiled=compile_v050()
    eng=ResearchBoundRuleEngine(compiled)
    assert [r.rule_id for r in compiled.rules] == ["R001","R002","R003","R004","R005","R006","R007","R009","R008"]
    for count in (0,1,2):
        p=eng.plan(f"E-{count}","graph.changed",{},
            {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"DATA_NOT_FOUND","search_count":count},"stage":"RESEARCH"})
        assert p.rule_id=="R007"
        assert p.actions[0]["action_type"]=="DISPATCH"
        assert p.actions[0]["capability"]=="RESEARCH_SOURCE"
    for count in (3,4,100):
        p=eng.plan(f"E-X-{count}","graph.changed",{},
            {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"DATA_NOT_FOUND","search_count":count},"stage":"RESEARCH"})
        assert p.rule_id=="R009"
        assert p.actions == ({"action_type":"CREATE","entity":"DATA_DEFICIT","target":"C1","type":"RESEARCH_EXHAUSTED"},)
        assert all(x.get("capability")!="RESEARCH_SOURCE" for x in p.actions)
    p=eng.plan("E-DISC","graph.changed",{},
        {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"DATA_NOT_DISCLOSED","search_count":99},"stage":"RESEARCH"})
    assert p.rule_id=="R006" and p.actions[0]["type"]=="DATA_NOT_DISCLOSED"
    p=eng.plan("E-NONE","graph.changed",{},
        {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,"data_deficit_type":"NONE","search_count":99},"stage":"RESEARCH"})
    assert p is None
    m=sc.build_required_path_manifest(compiled)
    by=dict((rid,(ev,st)) for rid,ev,st in m.by_rule)
    assert "state.claim.data_deficit_type" in by["R009"][1]
    assert "state.claim.search_count" in by["R009"][1]
    assert "state.claim.id" in by["R009"][1]
    old=c.AODSLCompiler().compile(c.PRODUCTION_AODSL)
    assert compiled.source_hash != old.source_hash and compiled.ir_hash != old.ir_hash
    print("AODSL v0.5.0 INV-025: BOUNDED RESEARCH TERMINATION TESTS PASSED")
    print("Covered: R007 retry counts 0..2 | R009 exhaustion >=3 | no post-budget research dispatch")
    print("         DATA_NOT_DISCLOSED separation | terminal DATA_DEFICIT | manifest coverage")

if __name__=="__main__":
    inv025_contract_tests()

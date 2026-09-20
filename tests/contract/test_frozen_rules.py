from ._support import *

def test_frozen_r001_r008_baseline():
    rules={r.rule_id:r for r in a.production_rules()}
    assert set(rules)=={"R001","R002","R003","R004","R005","R006","R007","R008"}
    assert rules["R001"].priority > rules["R003"].priority
    assert rules["R004"].priority > rules["R005"].priority > rules["R006"].priority > rules["R007"].priority > rules["R008"].priority



# INV-021 — authoritative bounded causal propagation contract.
# The implementation-owned suite is called rather than duplicating its logic here.

def test_frozen_r001_r009_baseline():
    compiled = re050.compile_v050()
    ids = {r.rule_id for r in compiled.rules}
    assert ids == {f"R00{i}" for i in range(1, 10)}
    eng = re050.ResearchBoundRuleEngine(compiled)

    retry = eng.plan("BASE-R007", "graph.changed", {},
        {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,
                  "data_deficit_type":"DATA_NOT_FOUND","search_count":2},
         "stage":"RESEARCH"})
    assert retry.rule_id == "R007"
    assert retry.actions[0]["capability"] == "RESEARCH_SOURCE"

    exhausted = eng.plan("BASE-R009", "graph.changed", {},
        {"claim":{"id":"C1","calculation_exists":True,"verification_exists":True,
                  "data_deficit_type":"DATA_NOT_FOUND","search_count":3},
         "stage":"RESEARCH"})
    assert exhausted.rule_id == "R009"
    assert exhausted.actions[0]["action_type"] == "CREATE"
    assert exhausted.actions[0]["type"] == "RESEARCH_EXHAUSTED"


# INV-026..INV-029 — authoritative multi-worker lease/fencing contracts.


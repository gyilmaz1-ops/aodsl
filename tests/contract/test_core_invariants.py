from ._support import *

def test_inv001_deterministic_plan():
    rules = a.production_rules()
    e = {"verification":{
        "status":"REJECTED","retry_count":2,"claim_id":"C1",
        "required_capability":"RESEARCH_SOURCE","remediation_type":"MISSING_SOURCE"
    }}
    s = {"claim":{"id":"C1"}}
    p1 = a.RuleEngine(rules).plan("EV-1","verification.completed",e,s)
    p2 = a.RuleEngine(rules).plan("EV-1","verification.completed",e,s)
    assert p1 == p2


# INV-002/003 — evaluation and planning cannot mutate authoritative state.

def test_inv002_003_rule_engine_isolation_and_plan_before_mutation():
    rt = a.OrchestrationRuntime()
    rt.graph = graph_for_r004()
    before = rt.graph.canonical_state()
    state = rt.graph.rule_state("CLM-A")
    p = rt.engine.plan("EV-X","graph.changed",{},state)
    assert p.rule_id == "R004"
    assert rt.graph.canonical_state() == before


# INV-004 — canonical identity ignores dictionary insertion order.

def test_inv004_canonical_state_identity():
    x = {"b":2,"a":{"y":1,"x":0}}
    y = {"a":{"x":0,"y":1},"b":2}
    assert a.canonical_hash(x) == a.canonical_hash(y)


# INV-005 — stale plan rejected.

def test_inv005_stale_plan_rejected():
    rt = a.OrchestrationRuntime()
    rt.graph = graph_for_r004()
    rt.committer.graph = rt.graph
    state = rt.graph.rule_state("CLM-A")
    p = rt.engine.plan("EV-X","graph.changed",{},state)
    body={"event_id":p.event_id,"base_state_hash":rt.graph.canonical_hash(),
          "rule_id":p.rule_id,"rule_version":p.rule_version,"actions":list(p.actions)}
    p = a.ActionPlan(p.event_id,rt.graph.canonical_hash(),p.rule_id,p.rule_version,
                     p.actions,a.canonical_hash(body))
    rt.graph.get("CLM-A").data["status"]="CONTESTED"
    rt.graph.revision += 1
    try:
        rt.committer.commit_plan(p)
        assert False
    except a.AODSLError as e:
        assert e.code == "AODSL-R409"


# INV-006/009 — graph/event/outbox transaction is atomic and external dispatch
# is not performed inside that transaction.

def test_inv006_009_atomic_commit_and_transactional_outbox():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"arc.db")
        rt,ev=a._prepare_dispatch_runtime(db)
        claimed=rt.store.claim_next_event()
        plan=a._build_plan(rt,claimed)
        chaos=a.ChaosController("AFTER_OUTBOX_WRITE")
        c=a.ChaosDurableAtomicCommitter(rt.graph,rt.store,rt.bus,rt.registry,chaos)
        before=rt.graph.canonical_hash()
        try:
            c.commit_plan(claimed,plan)
            assert False
        except a.InjectedCrash:
            pass
        restarted=a.DurableOrchestrationRuntime(db)
        assert restarted.graph.canonical_hash()==before
        assert len(restarted.store.pending_outbox())==0


# INV-007/008 — durable event lifecycle and committed finality.

def test_inv007_008_event_durability_and_finality():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"events.db")
        rt,ev=a._prepare_dispatch_runtime(db)
        result=rt.process_next()
        assert result["plan"].rule_id=="R004"
        with rt.store.connect() as con:
            row=con.execute("SELECT status FROM event_log WHERE event_id=?",
                            (ev.event_id,)).fetchone()
        assert row["status"]=="COMMITTED"
        assert rt.store.claim_next_event() is None


# INV-010/011 — physical retry may happen; logical task identity remains stable.

def test_inv010_011_at_least_once_plus_stable_idempotency():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"idem.db")
        rt,_=a._prepare_dispatch_runtime(db)
        rt.process_next()
        rows=rt.store.pending_outbox()
        assert len(rows)==1
        key=rows[0]["idempotency_key"]
        logical={}
        def receiver(payload):
            logical.setdefault(payload["idempotency_key"],payload)
        try:
            a.OutboxDispatcher(rt.store,receiver).flush(crash_after_send=True)
            assert False
        except RuntimeError:
            pass
        assert rt.store.pending_outbox()[0]["idempotency_key"]==key
        a.OutboxDispatcher(rt.store,receiver).flush()
        assert len(logical)==1


# INV-012 — committed durable state survives process restart.

def test_inv012_crash_recoverability():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"recover.db")
        rt,_=a._prepare_dispatch_runtime(db)
        rt.process_next()
        rt2=a.DurableOrchestrationRuntime(db)
        rec=rt2.recover()
        assert rt2.graph.get("CLM-C") is not None
        assert rec["recoverable_outbox"]==1


# INV-013 — missing operand => UNKNOWN / R401, never silently FALSE.

def test_inv013_three_valued_missing_runtime_value():
    eng=a.RuleEngine(a.production_rules())
    ev={"verification":{"status":"REJECTED"}}  # retry_count missing
    try:
        eng.plan("EV-U","verification.completed",ev,{"claim":{"id":"X"}})
        assert False
    except a.AODSLError as e:
        assert e.code=="AODSL-R401"


# INV-014 — contextual symbol resolution, not capitalization heuristic.

def test_inv014_contextual_symbol_resolution():
    r=a.Rule(
        "R900","0.1.4",10,"verification.completed",
        a.CompareExpr("==",a.PathExpr(("event","verification","status")),
                      a.IdentifierExpr("REJECTED")),
        (a.Action("DISPATCH",{
            "capability":a.IdentifierExpr("VERIFY_CLAIM"),
            "target":a.PathExpr(("event","verification","claim_id")),
            "reason":a.IdentifierExpr("TEST")
        }),),True
    )
    typed=a.SemanticValidator().validate_rule(r)
    assert isinstance(typed.predicate.right,a.LiteralExpr)
    assert typed.predicate.right.value=="REJECTED"


# INV-015 — same priority alone is legal; overlapping incompatible effects are not.

def test_inv015_semantic_conflict_definition():
    p1=a.CompareExpr("==",a.PathExpr(("state","stage")),a.IdentifierExpr("FUNDAMENTAL"))
    p2=a.CompareExpr("==",a.PathExpr(("state","stage")),a.IdentifierExpr("RESEARCH"))
    act1=(a.Action("TRANSITION",{"target":a.IdentifierExpr("stage"),
                                 "from":a.IdentifierExpr("FUNDAMENTAL"),
                                 "to":a.IdentifierExpr("VALUATION")}),)
    act2=(a.Action("TRANSITION",{"target":a.IdentifierExpr("stage"),
                                 "from":a.IdentifierExpr("RESEARCH"),
                                 "to":a.IdentifierExpr("VALUATION")}),)
    r1=a.Rule("R901","0.1.4",100,"graph.changed",p1,act1,True)
    r2=a.Rule("R902","0.1.4",100,"graph.changed",p2,act2,True)
    # Provably disjoint equal-priority rules must compile.
    a.RuleEngine([r1,r2])

    # Same predicate, same priority, conflicting transitions must fail.
    r3=a.Rule("R903","0.1.4",100,"graph.changed",p1,
              (a.Action("TRANSITION",{"target":a.IdentifierExpr("stage"),
                                      "from":a.IdentifierExpr("FUNDAMENTAL"),
                                      "to":a.IdentifierExpr("BEAR_REVIEW")}),),True)
    try:
        a.RuleEngine([r1,r3])
        assert False
    except a.AODSLError as e:
        assert e.code=="AODSL-C301"


# INV-016 — first-match priority determinism: R001 beats R003.

def test_inv016_first_match_priority():
    eng=a.RuleEngine(a.production_rules())
    ev={"verification":{
        "status":"REJECTED","retry_count":3,"claim_id":"C1",
        "required_capability":"RESEARCH_SOURCE","remediation_type":"MISSING_SOURCE"
    }}
    p=eng.plan("EV-P","verification.completed",ev,{"claim":{"id":"C1"}})
    assert p.rule_id=="R001"


# INV-017 — rule dispatches capability; registry resolves provider.

def test_inv017_capability_indirection():
    rt=a.OrchestrationRuntime()
    rt.graph=graph_for_r004()
    rt.committer.graph=rt.graph
    rt.emit("graph.changed","CLAIM","CLM-A",{})
    result=rt.process_one()
    assert result["plan"].rule_id=="R004"
    assert result["outbox"][0]["capability"]=="CALCULATE_FINANCIAL_METRIC"
    assert result["outbox"][0]["provider"]=="calculation_agent_v1"


# INV-018 — committed durable decisions carry audit provenance.

def test_inv018_auditability():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"audit.db")
        rt,ev=a._prepare_dispatch_runtime(db)
        result=rt.process_next()
        with rt.store.connect() as con:
            row=con.execute("""
                SELECT event_id,snapshot_hash,rule_id,plan_hash,action_type,status
                FROM audit_log WHERE event_id=?
            """,(ev.event_id,)).fetchone()
        assert row["event_id"]==ev.event_id
        assert row["snapshot_hash"]
        assert row["rule_id"]=="R004"
        assert row["plan_hash"]==result["plan"].plan_hash
        assert row["action_type"]=="DISPATCH"
        assert row["status"]=="COMMITTED"


# INV-019 — deterministic replay selects same rule on exact snapshot.

def test_inv019_deterministic_replay():
    rt=a.OrchestrationRuntime()
    rt.graph=graph_for_r004()
    rt.committer.graph=rt.graph
    snap=rt.graph.clone()
    ev=rt.emit("graph.changed","CLAIM","CLM-A",{})
    rt.process_one()
    assert rt.replay_decision(1,ev,snap)=="R004"


# INV-020 — poison event failure is isolated; next event remains processable.

def test_inv020_poison_event_isolation():
    with tempfile.TemporaryDirectory() as td:
        db=str(Path(td)/"poison.db")
        rt=a.DurableOrchestrationRuntime(db)
        rt.graph.stage="RESEARCH"
        rt.graph.add_node("CLAIM","GOOD",status="UNVERIFIED",
                          required_calculation="CAGR",
                          data_deficit_type="NONE",search_count=0)
        rt.persist_graph()
        poison=rt.emit("verification.completed","CLAIM","GOOD",
                       {"verification":{"status":"REJECTED"}})
        good=rt.emit("graph.changed","CLAIM","GOOD",{})
        try:
            rt.process_next()
            assert False
        except a.AODSLError as e:
            assert e.code=="AODSL-R401"
        result=rt.process_next()
        assert result["event"].event_id==good.event_id
        assert result["plan"].rule_id=="R004"


# Frozen R001-R008 behavioral baseline.


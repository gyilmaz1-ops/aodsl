"""
AODSL-ARC-0.9.8 Machine-Enforced Architecture Contract Tests

These tests are release blockers for INV-001..INV-048.
Run:
    aodsl test-contract
"""
import copy
import json
import tempfile
from pathlib import Path

import aodsl.runtime as a
import aodsl.compiler as c
import aodsl.snapshot as sc
import aodsl.research as re050
import aodsl.multiworker as mw060
import aodsl.outbox as ob070
import aodsl.e2e as e2e080
import aodsl.schema as sm090
import aodsl.unified_schema as usr091
import aodsl.exclusive_migration as em092
import aodsl.postgres_contract as pg093
import aodsl.postgres_store as pga094
import aodsl.graceful as gh096
import aodsl.observability as oh097
import aodsl.audit as ta098


def bind_plan(rt, event):
    state = rt.graph.rule_state(event.entity_id)
    p = rt.engine.plan(event.event_id, event.event_type, event.payload, state)
    if p is None:
        return None
    snapshot = rt.graph.canonical_hash()
    body = {
        "event_id": p.event_id,
        "base_state_hash": snapshot,
        "rule_id": p.rule_id,
        "rule_version": p.rule_version,
        "actions": list(p.actions),
    }
    return a.ActionPlan(
        p.event_id, snapshot, p.rule_id, p.rule_version,
        p.actions, a.canonical_hash(body)
    )


def graph_for_r004():
    g = a.MemoryGraph()
    g.stage = "RESEARCH"
    g.add_node(
        "CLAIM", "CLM-A",
        status="UNVERIFIED",
        required_calculation="CAGR",
        data_deficit_type="NONE",
        search_count=0,
    )
    return g


# INV-001 — same RuleSet + Event + State => same ActionPlan.

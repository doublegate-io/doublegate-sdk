"""The operation table: every ``dg.*`` verb of both gate catalogs, with what a
call needs (ADR-0074 d1). The catalog is the schema (I7); this table is its
client-side reading and the allowlist every transport enforces before I/O.

``scope`` is the widest client that may emit the verb:

* ``read`` — answers questions, changes nothing;
* ``knowledge`` — what an agent may do: propose, annotate, weight for an
  audience; never decide (the gate is not callable by the thing being gated);
* ``curation`` — the operator's verbs, most of them under an operator proof.
"""
from __future__ import annotations

from dataclasses import dataclass

READ, KNOWLEDGE, CURATION = 'read', 'knowledge', 'curation'
SCOPES: tuple[str, ...] = (READ, KNOWLEDGE, CURATION)
_RANK = {READ: 0, KNOWLEDGE: 1, CURATION: 2}


@dataclass(frozen=True, slots=True)
class Operation:
    rpc: str
    mutates: bool
    proof: str      # none | presence | optional | operator
    role: str       # client | org | both
    scope: str      # read | knowledge | curation


def _op(rpc: str, *, mutates: bool, proof: str = 'none', role: str = 'both', scope: str) -> Operation:
    return Operation(rpc, mutates, proof, role, scope)


OPERATIONS: dict[str, Operation] = {op.rpc: op for op in (
    # ---- reads (client-gate catalog) ----
    _op('dg.ping', mutates=False, scope=READ),
    _op('dg.describe', mutates=False, scope=READ),
    _op('dg.status', mutates=False, scope=READ),
    _op('dg.pending', mutates=False, scope=READ),
    _op('dg.inventory', mutates=False, scope=READ),
    _op('dg.recall', mutates=False, scope=READ),
    _op('dg.why', mutates=False, scope=READ),
    _op('dg.audit', mutates=False, scope=READ),
    _op('dg.tip', mutates=False, scope=READ),
    _op('dg.relations', mutates=False, scope=READ),
    _op('dg.approval', mutates=False, scope=READ),
    _op('dg.graph', mutates=False, scope=READ),
    _op('dg.node', mutates=False, scope=READ),
    _op('dg.comments', mutates=False, scope=READ),
    _op('dg.review', mutates=False, scope=READ),
    _op('dg.review_runs', mutates=False, scope=READ),
    _op('dg.engagement', mutates=False, scope=READ),
    _op('dg.bans', mutates=False, scope=READ),
    _op('dg.settings', mutates=False, scope=READ),
    _op('dg.verify', mutates=False, scope=READ),
    _op('dg.challenge', mutates=False, scope=READ),
    # ---- what an agent may do ----
    _op('dg.ingest', mutates=True, scope=KNOWLEDGE),
    _op('dg.ingest_bundle', mutates=True, scope=KNOWLEDGE),
    _op('dg.comment', mutates=True, proof='optional', scope=KNOWLEDGE),
    _op('dg.comment_retract', mutates=True, proof='optional', scope=KNOWLEDGE),
    _op('dg.defer', mutates=True, scope=KNOWLEDGE),
    _op('dg.rank', mutates=True, proof='optional', scope=KNOWLEDGE),
    _op('dg.hide', mutates=True, proof='optional', scope=KNOWLEDGE),
    # `{action: get}` is the crawler's read of its own robots exception; `issue`
    # needs an operator proof the knowledge client never carries, so the gate
    # refuses that action from an agent regardless of this table.
    _op('dg.crawler_policy', mutates=True, proof='operator', scope=KNOWLEDGE),
    # ---- the operator's verbs ----
    _op('dg.operator_proof', mutates=False, proof='presence', scope=CURATION),
    _op('dg.sign', mutates=True, proof='operator', scope=CURATION),
    _op('dg.promote', mutates=True, proof='operator', scope=CURATION),
    _op('dg.demote', mutates=True, proof='operator', scope=CURATION),
    _op('dg.reject', mutates=True, proof='operator', scope=CURATION),
    _op('dg.relate', mutates=True, proof='operator', scope=CURATION),
    _op('dg.override_tip', mutates=True, proof='operator', scope=CURATION),
    _op('dg.ban', mutates=True, proof='operator', scope=CURATION),
    _op('dg.away', mutates=True, proof='operator', scope=CURATION),
    _op('dg.back', mutates=True, proof='operator', scope=CURATION),
    _op('dg.semantic_review', mutates=True, scope=CURATION),
    _op('dg.scan', mutates=True, scope=CURATION),
    _op('dg.scan_pending', mutates=True, scope=CURATION),
    _op('dg.grade', mutates=True, scope=CURATION),
    _op('dg.grade_pending', mutates=True, scope=CURATION),
    _op('dg.review_pending', mutates=True, scope=CURATION),
    _op('dg.settle_pending', mutates=True, scope=CURATION),
    _op('dg.submit', mutates=True, role='client', scope=CURATION),
    _op('dg.submit_drain', mutates=True, role='client', scope=CURATION),
    _op('dg.publish', mutates=True, proof='presence', scope=CURATION),
    _op('dg.reconcile', mutates=True, proof='presence', scope=CURATION),
    _op('dg.rebuild', mutates=True, proof='presence', scope=CURATION),
    _op('dg.settings_validate', mutates=False, scope=CURATION),
    _op('dg.settings_save', mutates=True, scope=CURATION),
    # ---- the organization gate's own verbs ----
    _op('dg.collection', mutates=False, role='org', scope=READ),
    _op('dg.policy', mutates=False, role='org', scope=READ),
    _op('dg.countersign_status', mutates=False, role='org', scope=READ),
    _op('dg.audit_chain', mutates=False, role='org', scope=READ),
    _op('dg.org_review', mutates=False, role='org', scope=READ),
    _op('dg.keys', mutates=True, proof='operator', role='org', scope=CURATION),
    _op('dg.work', mutates=True, proof='presence', role='org', scope=CURATION),
)}


def allows(scope: str, rpc: str) -> bool:
    """True when a transport of ``scope`` may emit ``rpc``; unknown verbs are never allowed."""
    op = OPERATIONS.get(rpc)
    return op is not None and _RANK[op.scope] <= _RANK[scope]


def in_scope(scope: str) -> frozenset[str]:
    return frozenset(rpc for rpc in OPERATIONS if allows(scope, rpc))


def check_scope(scope: str) -> str:
    if scope not in SCOPES:
        raise ValueError(f"scope must be one of {SCOPES}, got {scope!r}")
    return scope

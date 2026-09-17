"""The operation table: every ``dg.*`` verb of both gate catalogs, with what a
call needs (ADR-0074 d1). The catalog is the schema (I7); this table is its
client-side reading and the allowlist every transport enforces before I/O.

``role`` is the authority the gate holds the verb to (ADR-0082 d5, AUTH-4),
ranked ``reader < agent < reviewer < admin``. It is mirrored from the gate's own
verb catalog — ``doublegate.apidoc.RPC_VERBS`` and ``doublegate_org.apidoc`` —
which is where the column is written, and it is what the caller's credential is
measured against at the door. The client never enforces it: a transport carries
a bearer, the gate assigns that credential a role, and the gate decides.

``scope`` is the widest client that may emit the verb, which is a different
question — what this SDK's two clients are allowed to send at all:

* ``read`` — answers questions, changes nothing;
* ``knowledge`` — what an agent may do: propose, annotate, weight for an
  audience; never decide (the gate is not callable by the thing being gated);
* ``curation`` — the decisions, which need a ``reviewer`` or an ``admin``.

``service`` is which gate answers the verb: ``client``, ``org`` or ``both``.
"""
from __future__ import annotations

from dataclasses import dataclass

READ, KNOWLEDGE, CURATION = 'read', 'knowledge', 'curation'
SCOPES: tuple[str, ...] = (READ, KNOWLEDGE, CURATION)
_RANK = {READ: 0, KNOWLEDGE: 1, CURATION: 2}

#: AUTH-4, ranked. A role reaches its own row and every row below it.
ROLES: tuple[str, ...] = ('reader', 'agent', 'reviewer', 'admin')


@dataclass(frozen=True, slots=True)
class Operation:
    rpc: str
    mutates: bool
    role: str       # reader | agent | reviewer | admin (AUTH-4)
    service: str    # client | org | both
    scope: str      # read | knowledge | curation


def _op(rpc: str, *, mutates: bool, role: str, service: str = 'both', scope: str) -> Operation:
    return Operation(rpc, mutates, role, service, scope)


OPERATIONS: dict[str, Operation] = {op.rpc: op for op in (
    # ---- reads (client-gate catalog) ----
    _op('dg.ping', mutates=False, role='reader', scope=READ),
    _op('dg.describe', mutates=False, role='reader', scope=READ),
    _op('dg.status', mutates=False, role='reader', scope=READ),
    _op('dg.pending', mutates=False, role='reader', scope=READ),
    _op('dg.inventory', mutates=False, role='reader', scope=READ),
    _op('dg.recall', mutates=False, role='reader', scope=READ),
    _op('dg.why', mutates=False, role='reader', scope=READ),
    _op('dg.audit', mutates=False, role='reader', scope=READ),
    _op('dg.tip', mutates=False, role='reader', scope=READ),
    _op('dg.relations', mutates=False, role='reader', scope=READ),
    _op('dg.approval', mutates=False, role='reader', scope=READ),
    _op('dg.graph', mutates=False, role='reader', scope=READ),
    _op('dg.node', mutates=False, role='reader', scope=READ),
    _op('dg.comments', mutates=False, role='reader', scope=READ),
    # a read this SDK lets any client emit, which the gate still holds to a
    # reviewer: the queue is the reviewer's work list, not a public page.
    _op('dg.review', mutates=False, role='reviewer', scope=READ),
    _op('dg.review_runs', mutates=False, role='reader', scope=READ),
    _op('dg.engagement', mutates=False, role='reader', scope=READ),
    _op('dg.bans', mutates=False, role='reader', scope=READ),
    _op('dg.settings', mutates=False, role='admin', scope=READ),
    _op('dg.verify', mutates=False, role='reader', scope=READ),
    # ---- what an agent may do ----
    _op('dg.ingest', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.ingest_bundle', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.comment', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.comment_retract', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.defer', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.rank', mutates=True, role='agent', scope=KNOWLEDGE),
    _op('dg.hide', mutates=True, role='agent', scope=KNOWLEDGE),
    # `{action: get}` is the crawler's read of its own robots exception; the
    # gate holds the whole verb to a reviewer, so an agent's `issue` is refused
    # at the door regardless of this table's scope.
    _op('dg.crawler_policy', mutates=True, role='reviewer', scope=KNOWLEDGE),
    # ---- the decisions ----
    _op('dg.sign', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.promote', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.demote', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.reject', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.relate', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.override_tip', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.ban', mutates=True, role='admin', scope=CURATION),
    _op('dg.away', mutates=True, role='admin', scope=CURATION),
    _op('dg.back', mutates=True, role='admin', scope=CURATION),
    _op('dg.semantic_review', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.scan', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.scan_pending', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.grade', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.grade_pending', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.review_pending', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.settle_pending', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.submit', mutates=True, role='agent', service='client', scope=CURATION),
    _op('dg.submit_drain', mutates=True, role='agent', service='client', scope=CURATION),
    _op('dg.publish', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.reconcile', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.rebuild', mutates=True, role='admin', scope=CURATION),
    _op('dg.settings_validate', mutates=False, role='admin', scope=CURATION),
    _op('dg.settings_save', mutates=True, role='admin', scope=CURATION),
    # ---- the admin's two doors on every gate (AUTH-3, AUTH-4) ----
    _op('dg.keys', mutates=True, role='reviewer', scope=CURATION),
    _op('dg.people', mutates=True, role='admin', scope=CURATION),
    # ---- the organization gate's own verbs ----
    _op('dg.collection', mutates=False, role='reader', service='org', scope=READ),
    _op('dg.policy', mutates=False, role='reader', service='org', scope=READ),
    _op('dg.countersign_status', mutates=False, role='reader', service='org', scope=READ),
    _op('dg.audit_chain', mutates=False, role='reader', service='org', scope=READ),
    _op('dg.org_review', mutates=False, role='reviewer', service='org', scope=READ),
    _op('dg.work', mutates=True, role='reviewer', service='org', scope=CURATION),
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

"""The curation client: what the operator does (ADR-0074 d2, d3).

Every decision here is the human's own signature: the transport must be
curation-scoped and the client carries a ``ProofProvider`` that mints one
single-use operator proof per verb (ADR-0028 d1). Admin scope on an
organization gate is not operator standing (ADR-0040): without a provider
the operator verbs are refused before I/O with ``proof_required``.
"""
from __future__ import annotations

from typing import Any

from doublegate_sdk.errors import GateError
from doublegate_sdk.knowledge import KnowledgeClient
from doublegate_sdk.operations import CURATION, OPERATIONS
from doublegate_sdk.proof import ProofProvider
from doublegate_sdk.transport import GateTransport

RELATIONS: frozenset[str] = frozenset({'supersedes', 'restates', 'corrects', 'contradicts', 'extends'})
DECISIONS: frozenset[str] = frozenset({'promote', 'reject', 'hold', 'clear', 'archive'})


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a nonempty string')
    return value


class CurationClient:
    """The operator's view of a gate: decisions, relations, serving policy, keys."""

    def __init__(self, transport: GateTransport, proof: ProofProvider | None = None):
        scope = getattr(transport, 'scope', CURATION)
        if scope != CURATION:
            raise ValueError('the curation client needs a curation-scoped transport')
        self._transport = transport
        self._proof = proof
        #: the same gate, read the agent's way
        self.knowledge = _Reader(transport)

    def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        op = OPERATIONS[method]
        if op.proof == 'operator':
            if self._proof is None:
                raise GateError('proof_required', detail=f'{method} is the operator\'s: supply a ProofProvider')
            params = {**params, 'operator_proof': self._proof.prove(self._transport)}
        elif op.proof == 'optional' and self._proof is not None:
            params = {**params, 'operator_proof': self._proof.prove(self._transport)}
        result = self._transport.call(method, params)
        if not isinstance(result, dict):
            raise GateError('invalid_response')
        return result

    # ---- decisions (ADR-0073: approval is a sign, veto is a demote) ----

    def sign(self, artifact_id: str, decision: str, *, note: str = '', promote: bool = False) -> dict[str, Any]:
        if decision not in DECISIONS:
            raise ValueError(f'decision must be one of {sorted(DECISIONS)}')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'decision': decision}
        if note:
            params['note'] = note
        if promote:
            params['promote'] = True
        return self._call('dg.sign', params)

    def approve(self, artifact_id: str, *, note: str = '') -> dict[str, Any]:
        """Sign ``promote`` and promote under one proof; a promotion the boundary
        refuses is reported in ``promote.refused`` and the signature stands."""
        return self.sign(artifact_id, 'promote', note=note, promote=True)

    def hold(self, artifact_id: str, *, note: str = '') -> dict[str, Any]:
        return self.sign(artifact_id, 'hold', note=note)

    def clear_finding(self, artifact_id: str, *, note: str = '') -> dict[str, Any]:
        return self.sign(artifact_id, 'clear', note=note)

    def reject(self, artifact_id: str, reason: str, *, in_favour_of: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'reason': _text(reason, 'reason')}
        if in_favour_of is not None:
            params['in_favour_of'] = _text(in_favour_of, 'in_favour_of')
        return self._call('dg.reject', params)

    def veto(self, artifact_id: str, reason: str) -> dict[str, Any]:
        """The human veto of an admitted row: ``dg.demote`` (ADR-0073). Ends the
        claim; keeps the content."""
        return self._call('dg.demote', {'artifact_id': _text(artifact_id, 'artifact_id'), 'reason': _text(reason, 'reason')})

    def promote(self, artifact_id: str, *, importance: float | None = None, supersedes: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id')}
        if importance is not None:
            if type(importance) not in (int, float) or not 0 <= importance <= 1:
                raise ValueError('importance must be in 0..1')
            params['importance'] = float(importance)
        if supersedes is not None:
            params['supersedes'] = _text(supersedes, 'supersedes')
        return self._call('dg.promote', params)

    # ---- the lifecycle passes an operator runs by hand ----

    def scan(self, artifact_id: str) -> dict[str, Any]:
        return self._call('dg.scan', {'artifact_id': _text(artifact_id, 'artifact_id')})

    def grade(self, artifact_id: str) -> dict[str, Any]:
        """One model call per stance on one row; the gate engine, not this client, judges."""
        return self._call('dg.grade', {'artifact_id': _text(artifact_id, 'artifact_id')})

    def grade_pending(self, *, limit: int = 50) -> dict[str, Any]:
        return self._call('dg.grade_pending', {'limit': int(limit)})

    def settle_pending(self) -> dict[str, Any]:
        """Promote what quorum already settled; a row under an open human objection stays (ADR-0072)."""
        return self._call('dg.settle_pending', {})

    def semantic_review(self, artifact_id: str, *, settle: bool = False) -> dict[str, Any]:
        return self._call('dg.semantic_review', {'artifact_id': _text(artifact_id, 'artifact_id'), 'settle': bool(settle)})

    # ---- relations (ADR-0035): only supersedes hides ----

    def relate(self, child: str, kind: str, parent: str) -> dict[str, Any]:
        if kind not in RELATIONS:
            raise ValueError(f'kind must be one of {sorted(RELATIONS)}')
        return self._call('dg.relate', {'artifact_id': _text(child, 'child'), 'kind': kind, 'parent': _text(parent, 'parent')})

    def supersede(self, new: str, old: str) -> dict[str, Any]:
        return self.relate(new, 'supersedes', old)

    def override_tip(self, artifact_id: str, *, reason: str = '') -> dict[str, Any]:
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id')}
        if reason:
            params['reason'] = reason
        return self._call('dg.override_tip', params)

    # ---- serving policy (ADR-0052) ----

    def rank(self, artifact_id: str, *, audience: str, weight: float, reason: str = '') -> dict[str, Any]:
        if type(weight) not in (int, float) or not 0 < weight <= 10.0:
            raise ValueError('weight must be a number in (0, 10]')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'audience': _text(audience, 'audience'),
                                  'weight': float(weight)}
        if reason:
            params['reason'] = reason
        return self._call('dg.rank', params)

    def hide(self, artifact_id: str, *, reason: str = '', audience: str = 'all', lift: bool = False) -> dict[str, Any]:
        if not lift and not reason:
            raise ValueError('a hide needs a reason; lift=True reverses one')
        params: dict[str, Any] = {'artifact_id': _text(artifact_id, 'artifact_id'), 'audience': _text(audience, 'audience')}
        if lift:
            params['lift'] = True
        else:
            params['reason'] = reason
        return self._call('dg.hide', params)

    def ban(self, kind: str, subject: str, *, reason: str = '', until_ms: int | None = None, lift: bool = False) -> dict[str, Any]:
        if kind not in ('writer', 'source'):
            raise ValueError("kind must be 'writer' or 'source'")
        if not lift and not reason:
            raise ValueError('a ban needs a reason; lift=True reverses one')
        params: dict[str, Any] = {'kind': kind, 'subject': _text(subject, 'subject')}
        if lift:
            params['lift'] = True
        else:
            params['reason'] = reason
            if until_ms is not None:
                if type(until_ms) is not int or until_ms <= 0:
                    raise ValueError('until_ms must be a positive integer')
                params['until'] = until_ms
        return self._call('dg.ban', params)

    def bans(self) -> dict[str, Any]:
        return self._call('dg.bans', {})

    # ---- the operator's presence and the handoff ----

    def away(self, *, until: str | None = None, note: str = '') -> dict[str, Any]:
        params: dict[str, Any] = {}
        if until is not None:
            params['until'] = _text(until, 'until')
        if note:
            params['note'] = note
        return self._call('dg.away', params)

    def back(self) -> dict[str, Any]:
        return self._call('dg.back', {})

    def defer(self, artifact_id: str) -> dict[str, Any]:
        return self._call('dg.defer', {'artifact_id': _text(artifact_id, 'artifact_id')})

    def submit(self, artifact_id: str) -> dict[str, Any]:
        return self._call('dg.submit', {'artifact_id': _text(artifact_id, 'artifact_id')})

    def submit_drain(self) -> dict[str, Any]:
        return self._call('dg.submit_drain', {})

    def review(self, *, cap: int | None = None) -> dict[str, Any]:
        return self._call('dg.review', {} if cap is None else {'cap': int(cap)})

    # ---- the organization gate's keys (ADR-0033, ADR-0040) ----

    def keys(self, action: str = 'list', **fields: Any) -> dict[str, Any]:
        """``dg.keys``: list | show | issue | revoke. Issue and revoke are the
        operator's; a client gate answers ``unsupported_operation``."""
        if action not in ('list', 'show', 'issue', 'revoke'):
            raise ValueError('action must be list | show | issue | revoke')
        params = {'action': action, **fields}
        if action in ('issue', 'revoke'):
            if self._proof is None:
                raise GateError('proof_required', detail='issuing or revoking a key is the operator\'s (ADR-0040)')
            params['operator_proof'] = self._proof.prove(self._transport)
        result = self._transport.call('dg.keys', params)
        if not isinstance(result, dict):
            raise GateError('invalid_response')
        return result


class _Reader(KnowledgeClient):
    """A knowledge client over a curation transport: the constructor's scope
    refusal is for agents; the operator reads through the same door."""

    def __init__(self, transport: GateTransport):
        self._transport = transport
        self._probe = transport
        self._capabilities = None

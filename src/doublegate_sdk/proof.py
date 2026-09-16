"""The operator proof seam (ADR-0074 d3).

An operator verb carries ``{nonce, sig}``: a fresh ``dg.challenge`` nonce and
the operator key's signature over it (ADR-0028 d1). The SDK never holds that
key. It asks a ``ProofProvider`` — a caller-supplied signer, or the client
daemon's own ``dg.operator_proof`` on the host where the key lives — and
forwards what it gets (ADR-0040: wrappers forward proof, they do not
synthesize it). Nonces are single-use: one proof per verb, minted just
before the call.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypedDict

from doublegate_sdk.errors import GateError


class OperatorProof(TypedDict):
    nonce: str
    sig: str


class ProofProvider(Protocol):
    def prove(self, transport: Any) -> OperatorProof: ...


class SignerProof:
    """Fetch a nonce from the gate and sign it with the caller's function
    (``bytes -> bytes``, a detached Ed25519 signature). The key stays with the caller."""

    def __init__(self, sign: Callable[[bytes], bytes]):
        if not callable(sign):
            raise ValueError('sign must be callable')
        self._sign = sign

    def prove(self, transport: Any) -> OperatorProof:
        challenge = transport.call('dg.challenge', {})
        nonce = challenge.get('nonce') if isinstance(challenge, dict) else None
        if not isinstance(nonce, str) or not nonce:
            raise GateError('invalid_response')
        sig = self._sign(nonce.encode('ascii'))
        if not isinstance(sig, (bytes, bytearray)) or not sig:
            raise GateError('proof_required', detail='the signer returned no signature')
        return {'nonce': nonce, 'sig': bytes(sig).hex()}


class ServerMinted:
    """``dg.operator_proof``: the client daemon signs the nonce with the key on
    its host. Presence on that host is the whole authorization; a gate whose
    key is unreadable, or an organization gate, refuses and this says so."""

    def prove(self, transport: Any) -> OperatorProof:
        try:
            answer = transport.call('dg.operator_proof', {})
        except GateError as exc:
            if exc.kind in ('invalid_params', 'unsupported_operation', 'refused'):
                raise GateError('proof_required', exc.code, detail=exc.detail) from None
            raise
        proof = answer.get('operator_proof', answer) if isinstance(answer, dict) else None
        if not isinstance(proof, dict) or not isinstance(proof.get('nonce'), str) or not isinstance(proof.get('sig'), str):
            raise GateError('invalid_response')
        return {'nonce': proof['nonce'], 'sig': proof['sig']}

"""Untrusted attribution wire decoding; NO signature or authority verification.

These results are data, not capabilities. Gates must independently authenticate
signers and verify enrollment, retained evidence and current authority before effects.
"""
from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass

from doublegate_sdk.identity import (
    AttributionError, AttributionV1, decode_attribution, _strict_json, _closed,
    _identifier, _digest,
)
from doublegate_sdk.envelope import canonical_json


def evidence_manifest_digest(manifest: dict) -> str:
    """Hash the closed production manifest; does NOT check retained evidence bytes."""
    _closed(manifest, {'manifest_version', 'evidence_refs'})
    if type(manifest['manifest_version']) is not int or manifest['manifest_version'] != 1:
        raise AttributionError('manifest_version must be integer 1')
    refs = manifest['evidence_refs']
    if not isinstance(refs, list) or not refs:
        raise AttributionError('evidence_refs must be a nonempty list')
    for ref in refs:
        _digest(ref)
    if refs != sorted(set(refs)):
        raise AttributionError('evidence_refs must be sorted and unique')
    return hashlib.sha256(canonical_json(dict(manifest))).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedAttributionJWS:
    """Exact compact bytes and untrusted claims, including an UNVERIFIED signature."""

    compact: str
    payload: AttributionV1
    signature: bytes
    _header_bytes: bytes

    @property
    def header(self) -> dict:
        return _strict_json(self._header_bytes)

    @property
    def signing_input(self) -> bytes:
        return self.compact.rsplit('.', 1)[0].encode('ascii')

    @property
    def statement_digest(self) -> str:
        return hashlib.sha256(self.compact.encode('ascii')).hexdigest()


@dataclass(frozen=True, slots=True)
class ParsedAttributionChain:
    """Structurally linked, UNTRUSTED statements; never an authority capability."""

    statements: tuple[ParsedAttributionJWS, ParsedAttributionJWS]

    @property
    def chain_digest(self) -> str:
        return hashlib.sha256(canonical_json(
            [statement.compact for statement in self.statements])).hexdigest()


def decode_attribution_chain(
    chain: list[str], *, expected_envelope_digest: str, expected_evidence_digest: str,
) -> ParsedAttributionChain:
    """Parse the two-hop profile without authenticating signatures or authority.

    Expected digests must come from the caller's actual envelope and production
    manifest. This function does not resolve or check retained evidence bytes.
    """
    if not isinstance(chain, list) or len(chain) != 2:
        raise AttributionError('chain must be a list of exactly two compact JWS strings')
    root, submission = (decode_attribution_jws(s) for s in chain)
    production_claims = root.payload.to_dict()
    submission_claims = submission.payload.to_dict()
    if (production_claims['phase'], submission_claims['phase']) != ('production', 'submission'):
        raise AttributionError('chain phases must be production then submission')
    if submission_claims['parent_statement_digest'] != root.statement_digest:
        raise AttributionError('submission parent does not match exact root JWS digest')
    for field in ('tenant_id', 'operation_id', 'envelope_digest', 'evidence_digest',
                  'requester_id', 'contributor_id', 'home_team_id'):
        if production_claims[field] != submission_claims[field]:
            raise AttributionError(f'chain {field} does not match across phases')
    for field, expected in (('envelope_digest', expected_envelope_digest),
                            ('evidence_digest', expected_evidence_digest)):
        _digest(expected)
        if production_claims[field] != expected:
            raise AttributionError(f'chain {field} does not match expected {field}')
    return ParsedAttributionChain((root, submission))


def decode_attribution_jws(compact: str) -> ParsedAttributionJWS:
    """Decode structural wire data only. Even zero signature bytes may parse."""
    if not isinstance(compact, str) or len(compact.split('.')) != 3:
        raise AttributionError('compact JWS must contain three segments')
    header, payload, signature = compact.split('.')

    def decode(segment):
        if not segment or any(c not in
                'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
                for c in segment):
            raise AttributionError('JWS segments must be unpadded base64url')
        try:
            raw = base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4))
        except ValueError as exc:
            raise AttributionError('invalid base64url') from exc
        if base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii') != segment:
            raise AttributionError('noncanonical base64url')
        return raw

    header_bytes = decode(header)
    fields = _strict_json(header_bytes)
    _closed(fields, {'alg', 'kid', 'typ'})
    if fields['alg'] != 'EdDSA' or fields['typ'] != 'doublegate-attribution+jws':
        raise AttributionError('unsupported attribution JWS profile')
    _identifier(fields['kid'])
    signature_bytes = decode(signature)
    if len(signature_bytes) != 64:
        raise AttributionError('Ed25519 signature must contain 64 bytes')
    return ParsedAttributionJWS(compact, decode_attribution(decode(payload)),
                                signature_bytes, header_bytes)

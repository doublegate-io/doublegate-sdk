"""Client-to-organization byte contract, shared by sender, door and worker.

The artifact ID and detached signature commit to the complete event document.
Its envelope_digest identifies the frozen envelope; content_digest identifies raw
bytes. Phase signatures and event signatures are not authorization. This module
does not extract archives, resolve retained authority records or admit content.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from dataclasses import dataclass

from doublegate_sdk.envelope import Envelope
from doublegate_sdk.identity_wire import (
    ParsedAttributionChain, decode_attribution_chain, evidence_manifest_digest,
)

__all__ = ["encode_submission", "decode_submission", "SubmissionTooLarge",
           "SubmissionEvent", "canonical_submission", "sign_submission",
           "verify_submission_signature"]

# Restricted RFC 8785: integers only, so no floating-point serializer is needed.
# Envelope v1 retains its own existing canonicalization for envelope_digest.
from doublegate_sdk.identity import _closed, _digest, _identifier

DOMAIN = b'doublegate.submission.v1\0'
EVENT_FIELDS = frozenset({'contract', 'envelope', 'content_digest',
    'evidence_manifest_digest', 'tenant_id', 'team_id', 'principal', 'membership',
    'submitted_at', 'review_refs', 'attribution_chain'})


def canonical_submission(value: Any) -> bytes:
    """Restricted JCS bytes; refuse non-NFC, invalid Unicode and non-safe numbers."""
    def encode(v):
        if v is None or type(v) is bool:
            return json.dumps(v)
        if isinstance(v, str):
            if unicodedata.normalize('NFC', v) != v:
                raise ValueError('strings must be NFC')
            v.encode('utf-8')
            return json.dumps(v, ensure_ascii=False)
        if type(v) is int and 0 <= v <= 9007199254740991:
            return str(v)
        if isinstance(v, list):
            return '[' + ','.join(encode(x) for x in v) + ']'
        if isinstance(v, dict) and all(isinstance(k, str) for k in v):
            return '{' + ','.join(encode(k)+':'+encode(v[k]) for k in
                sorted(v, key=lambda k: k.encode('utf-16-be'))) + '}'
        raise ValueError('unsupported JSON type or non-safe integer')
    try:
        return encode(value).encode('utf-8')
    except (UnicodeError, RecursionError) as exc:
        raise ValueError('invalid Unicode or nesting') from exc


def _prefixed_digest(value):
    if not isinstance(value, str) or not value.startswith('sha256:'):
        raise ValueError('digest must use sha256: prefix')
    _digest(value[7:])


@dataclass(frozen=True, slots=True)
class SubmissionEvent:
    """One immutable whole-document commitment. Parsing is NOT authorization."""

    _canonical: bytes

    @classmethod
    def from_mapping(cls, document: dict[str, Any]) -> SubmissionEvent:
        _closed(document, EVENT_FIELDS)
        if document['contract'] != 'doublegate.submission/1':
            raise ValueError('unsupported submission contract')
        canonical = canonical_submission(document)
        if not isinstance(document['envelope'], dict):
            raise ValueError('envelope must be an object')
        env = Envelope.from_mapping(document['envelope'])
        if env.to_dict() != document['envelope']:
            raise ValueError('envelope optional fields must use canonical omission')
        for field in ('content_digest', 'evidence_manifest_digest'):
            _prefixed_digest(document[field])
        if document['content_digest'] != 'sha256:'+env.content_hash:
            raise ValueError('content_digest disagrees with envelope content_hash')
        for field in ('tenant_id', 'team_id'):
            _identifier(document[field])
        principal = document['principal']
        _closed(principal, {'kind', 'id', 'sponsor'})
        if principal['kind'] not in ('person', 'workload'):
            raise ValueError('principal kind must be person or workload')
        _identifier(principal['id'])
        if principal['sponsor'] is not None:
            _identifier(principal['sponsor'])
            if principal['kind'] == 'person':
                raise ValueError('person sponsor must be null')
        membership = document['membership']
        _closed(membership, {'issuer', 'revision', 'assertion_digest'})
        _identifier(membership['issuer'])
        if not re.match(r'^https?://[^/\s]+', membership['issuer']):
            raise ValueError('membership issuer must be a URL')
        if type(membership['revision']) is not int or not 0 <= membership['revision'] <= 9007199254740991:
            raise ValueError('membership revision must be a safe integer')
        _digest(membership['assertion_digest'])
        timestamp = document['submitted_at']
        if not isinstance(timestamp, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z', timestamp):
            raise ValueError('submitted_at must be UTC with millisecond precision')
        submitted = datetime.strptime(timestamp, '%Y-%m-%dT%H:%M:%S.%fZ').replace(tzinfo=timezone.utc)
        refs = document['review_refs']
        if not isinstance(refs, list):
            raise ValueError('review_refs must be an array')
        for ref in refs:
            _closed(ref, {'stage', 'record_digest'})
            _identifier(ref['stage'])
            _digest(ref['record_digest'])
        chain = decode_attribution_chain(document['attribution_chain'],
            expected_envelope_digest=env.envelope_digest,
            expected_evidence_digest=document['evidence_manifest_digest'][7:])
        production, submission = [s.payload.to_dict() for s in chain.statements]
        if not production['issued_at'] <= submission['issued_at'] <= submitted.timestamp():
            raise ValueError('phase/submission chronology is invalid')
        for field, expected in (('tenant_id', document['tenant_id']),
                                ('home_team_id', document['team_id']),
                                ('actor_id', principal['id'])):
            if submission[field] != expected:
                raise ValueError(f'submission phase {field} disagrees with event')
        return cls(canonical)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical)

    def canonical_bytes(self) -> bytes:
        return self._canonical

    @property
    def artifact_id(self) -> str:
        return 'sha256:'+hashlib.sha256(self._canonical).hexdigest()

    @property
    def content_digest(self) -> str:
        return self.to_dict()['content_digest']

    @property
    def signing_input(self) -> bytes:
        return DOMAIN+self._canonical


def _signature_bytes(signature: str) -> bytes:
    if not isinstance(signature, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', signature):
        raise ValueError('signature must be unpadded base64url')
    try:
        raw = base64.urlsafe_b64decode(signature+'='*(-len(signature)%4))
    except ValueError as exc:
        raise ValueError('invalid signature encoding') from exc
    if len(raw) != 64 or base64.urlsafe_b64encode(raw).decode().rstrip('=') != signature:
        raise ValueError('invalid canonical Ed25519 signature')
    return raw


def sign_submission(event: SubmissionEvent, *, private_key: bytes) -> str:
    """Sign with caller-held raw Ed25519 seed; no key generation or enrollment."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    event = SubmissionEvent.from_mapping(event.to_dict())
    signature = Ed25519PrivateKey.from_private_bytes(private_key).sign(event.signing_input)
    return base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')


def verify_submission_signature(event: SubmissionEvent, signature: str, *,
                                public_key: bytes, expected_artifact_id: str) -> None:
    """Check route and crypto only. Caller owns key purpose/principal enrollment.

    Does not verify either phase signature, historical records, local promotion,
    selected scope, current authority or operation uniqueness.
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.exceptions import InvalidSignature
    event = SubmissionEvent.from_mapping(event.to_dict())
    if event.artifact_id != expected_artifact_id:
        raise ValueError('route names a different artifact')
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(_signature_bytes(signature), event.signing_input)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError('invalid submission signature or public key') from exc


@dataclass(frozen=True, slots=True)
class _ParsedSubmission:
    """Decoded bytes and UNTRUSTED identity, not signature or authority verification.

    Local verdict/promotion validation, enrolled signer checks, retained evidence
    verification and current space/team/grant authorization remain gate obligations.
    """

    content: bytes
    envelope: Envelope
    space: str
    production_evidence_digest: str
    untrusted_identity: ParsedAttributionChain
    event: SubmissionEvent


def _parse_submission(body: dict[str, Any], artifact_id: str | None = None,
                      *, max_content_bytes: int) -> _ParsedSubmission:
    """Parse the sole event transport profile; all identity remains UNTRUSTED.

    Gates must resolve review_refs to exact retained local verdict/promotion bytes,
    verify both phases, and compare signed retained scope to the selected space.
    """
    if type(max_content_bytes) is not int or max_content_bytes < 0:
        raise ValueError('max_content_bytes must be a non-negative integer')
    _closed(body, {'document', 'artifact_id', 'signature', 'content', 'space',
                  'local_verdicts', 'local_promotion', 'production_evidence_manifest'}, {'encoding'})
    try:
        _identifier(body['space'])
    except ValueError as exc:
        raise ValueError('space must be a nonempty valid Unicode identifier') from exc
    if (not isinstance(body['local_verdicts'], list)
            or any(not isinstance(v, dict) for v in body['local_verdicts'])):
        raise ValueError('local_verdicts must be a list of objects')
    if not isinstance(body['local_promotion'], dict):
        raise ValueError('local_promotion must be an object')
    if not isinstance(body['document'], dict):
        raise ValueError('document must be an object')
    # Preserve the byte-limit/hash/size checks before phase-resource checks.
    content = _decode_content({**body, 'envelope': body['document'].get('envelope')},
                              max_content_bytes=max_content_bytes)
    event = SubmissionEvent.from_mapping(body['document'])
    if body['artifact_id'] != event.artifact_id or (artifact_id is not None and artifact_id != event.artifact_id):
        raise ValueError('submission URL/lease names a different artifact than the event')
    _signature_bytes(body['signature'])
    env = Envelope.from_mapping(body['document']['envelope'])
    digest = evidence_manifest_digest(body['production_evidence_manifest'])
    if event.to_dict()['evidence_manifest_digest'] != 'sha256:'+digest:
        raise ValueError('production evidence manifest digest does not match event')
    identity = decode_attribution_chain(body['document']['attribution_chain'],
        expected_envelope_digest=env.envelope_digest, expected_evidence_digest=digest)
    return _ParsedSubmission(content, env, body['space'], digest, identity, event)


class SubmissionTooLarge(ValueError):
    """A wire or decoded-content bound was exceeded (HTTP 413)."""


def decode_submission(body: dict[str, Any], artifact_id: str | None = None,
                      *, max_content_bytes: int) -> bytes:
    """Return exact content bytes after strict, UNTRUSTED submission parsing.

    Only doublegate.submission/1 is accepted. Parsing checks shape and byte bindings,
    not signatures, retained evidence, membership, grants or current authority.
    """
    return _parse_submission(body, artifact_id,
                             max_content_bytes=max_content_bytes).content


def _decode_content(body: dict[str, Any],
                    *, max_content_bytes: int) -> bytes:
    """Strictly decode and verify original bytes before any storage or review.

    Absent encoding means UTF-8 text. The only explicit encoding is canonical
    RFC 4648 base64: no whitespace, surplus padding, or nonzero padding bits.
    """
    if type(max_content_bytes) is not int or max_content_bytes < 0:
        raise ValueError("max_content_bytes must be a non-negative integer")
    if not isinstance(body, dict) or not isinstance(body.get("envelope"), dict):
        raise ValueError("submission must contain an envelope object")
    env = Envelope.from_mapping(body["envelope"])
    if env.size_bytes > max_content_bytes:
        raise SubmissionTooLarge("decoded content exceeds configured byte limit")
    raw = body.get("content")
    if not isinstance(raw, str):
        raise ValueError("content must be a string")
    if "encoding" in body:
        if body["encoding"] != "base64":
            raise ValueError("encoding must be base64 or omitted for UTF-8 text")
        if len(raw) > 4 * ((max_content_bytes + 2) // 3):
            raise SubmissionTooLarge("base64 content exceeds encoded limit")
        try:
            content = base64.b64decode(raw, validate=True)
        except (ValueError, binascii.Error) as e:
            raise ValueError("content must be strict base64") from e
        if base64.b64encode(content).decode("ascii") != raw:
            raise ValueError("content must be canonical base64")
    else:
        if len(raw) > max_content_bytes:
            raise SubmissionTooLarge("decoded content exceeds configured byte limit")
        try:
            content = raw.encode("utf-8")
        except UnicodeEncodeError as e:
            raise ValueError("content must be valid UTF-8 text") from e
    if len(content) > max_content_bytes:
        raise SubmissionTooLarge("decoded content exceeds configured byte limit")
    if hashlib.sha256(content).hexdigest() != env.content_hash:
        raise ValueError("content_hash does not match decoded content")
    if len(content) != env.size_bytes:
        raise ValueError("size_bytes does not match decoded content")
    return content


def encode_submission(content: bytes, document: dict[str, Any], *,
                      max_content_bytes: int, signature: str, space: str,
                      local_verdicts: list[dict], local_promotion: dict,
                      production_evidence_manifest: dict) -> dict[str, Any]:
    """Encode exact content with a frozen event and its detached signature.

    No bearer or authority is stored. Reuse this body unchanged on token refresh.
    Structural validation only: a 64-byte invalid signature can still parse.
    """
    if not isinstance(content, bytes):
        raise ValueError('content must be bytes')
    if type(max_content_bytes) is not int or max_content_bytes < 0:
        raise ValueError('max_content_bytes must be a non-negative integer')
    if len(content) > max_content_bytes:
        raise SubmissionTooLarge('decoded content exceeds configured byte limit')
    body = {'document': json.loads(json.dumps(document)),
            'artifact_id': 'sha256:'+hashlib.sha256(canonical_submission(document)).hexdigest(),
            'signature': signature, 'content': base64.b64encode(content).decode('ascii'),
            'encoding': 'base64', 'space': space, 'local_verdicts': local_verdicts,
            'local_promotion': local_promotion,
            'production_evidence_manifest': production_evidence_manifest}
    decode_submission(body, max_content_bytes=max_content_bytes)
    return body

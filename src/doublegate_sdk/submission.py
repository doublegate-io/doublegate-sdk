"""Client-to-organization byte contract, shared by sender, door and worker.

Two identities, one canonical form (ADR-0030, ADR-0064, P1-A/01 §2):

* ``content_digest`` is the **content** identity: ``"sha256:" + envelope.content_hash``,
  derived from the envelope and never hashed a second time. Many submissions share one.
* ``artifact_id`` is the **submission event** identity: ``sha256`` of the event
  document's own canonical bytes. It is not a member of the signed document; a door
  recomputes it from the received bytes and compares to the route.

Canonical bytes are RFC 8785 JCS narrowed to a refusal-shaped profile: UTF-8, no
whitespace, members ordered by UTF-16 code unit, strings NFC (refused, not normalized),
integers in ``[0, 2**53-1]``, no floats. The signed event (``SubmissionEvent``) closes the
member set, binds the attribution chain (the federated identity of the writer) and carries a
detached Ed25519 signature; phase and event signatures are not authorization. This module
does not extract archives, resolve retained authority records or admit content.

Stdlib only in its base path, on purpose: the SDK declares ``dependencies = []``; signing
and verification import ``cryptography`` lazily (the ``identity`` extra).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from doublegate_sdk.envelope import Envelope
from doublegate_sdk.identity import _closed, _digest, _identifier
from doublegate_sdk.identity_wire import (
    ParsedAttributionChain,
    decode_attribution_chain,
    evidence_manifest_digest,
)

__all__ = [
    # the canonical form and the two identities
    "ENCODING_INVALID",
    "ARTIFACT_ID_MISMATCH",
    "MAX_SAFE_INTEGER",
    "CanonicalizationRefused",
    "RouteIdMismatch",
    "canonical_bytes",
    "canonical_submission",
    "envelope_content_hash",
    "content_digest_from_content_hash",
    "content_digest_from_envelope",
    "content_hash_from_content_digest",
    "verify_content_digest",
    "submission_artifact_id",
    "verify_route_artifact_id",
    # the signed event and its transport body
    "EVENT_FIELDS",
    "SubmissionEvent",
    "sign_submission",
    "verify_submission_signature",
    "encode_submission",
    "decode_submission",
    "SubmissionTooLarge",
]

#: P1-A/01 §4. One refusal names exactly one code.
ENCODING_INVALID = "SUBMISSION_ENCODING_INVALID"
ARTIFACT_ID_MISMATCH = "SUBMISSION_ARTIFACT_ID_MISMATCH"

#: §2.4 — integers in ``[0, 2**53-1]``. Above it, a JSON reader elsewhere may
#: round, and a rounded id is a different id.
MAX_SAFE_INTEGER = 2 ** 53 - 1

SHA256_ID = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The envelope's ``content_hash`` spelling: bare, 64 lowercase hex, no prefix
#: (ADR-0022 decision 1, untouched by ADR-0068).
BARE_SHA256 = re.compile(r"^[0-9a-f]{64}$")

# §2.3 — escape ONLY these, and never anything else. Short forms where they
# exist; lowercase ``\u00xx`` otherwise.
_SHORT = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
          0x22: '\\"', 0x5C: "\\\\"}


class CanonicalizationRefused(ValueError):
    """The document breaks the §2 profile. Carries the contract's code."""

    code = ENCODING_INVALID

    def __init__(self, message: str) -> None:
        super().__init__(message)


class RouteIdMismatch(ValueError):
    """The route id is not ``sha256`` of the received canonical bytes."""

    code = ARTIFACT_ID_MISMATCH

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _string(s: str, *, what: str) -> str:
    """Serialize one string: NFC-checked, minimally escaped (§2.1, §2.3)."""
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as e:          # lone surrogate: not UTF-8 at all
        raise CanonicalizationRefused(f"{what}: not encodable as UTF-8") from e
    if unicodedata.normalize("NFC", s) != s:
        # Refused, NOT normalized: normalizing changes bytes a signature covers.
        raise CanonicalizationRefused(f"{what}: string is not NFC; it is refused, not normalized")
    out = ['"']
    for ch in s:
        o = ord(ch)
        if o in _SHORT:
            out.append(_SHORT[o])
        elif o < 0x20:
            out.append(f"\\u{o:04x}")        # lowercase hex, §2.3
        else:
            out.append(ch)                   # never escape what needs no escape
    out.append('"')
    return "".join(out)


def _member_sort_key(name: str) -> tuple[int, ...]:
    """§2.2 — sort by member name as a sequence of UTF-16 code units.

    Python orders ``str`` by code point, which disagrees with UTF-16 for every
    astral character: U+10000 leads with the surrogate 0xD800 and therefore
    sorts *before* U+E000..U+FFFF, not after. JCS says UTF-16, so we say UTF-16.
    """
    raw = name.encode("utf-16-be")
    return tuple(int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw), 2))


def _value(v: Any, *, path: str) -> str:
    if v is None:
        return "null"                        # §2.5 — a real value, not absence
    if v is True:
        return "true"
    if v is False:
        return "false"                       # §2.6 — no other spelling
    if isinstance(v, int):
        if v < 0:
            raise CanonicalizationRefused(f"{path}: negative integer; no sign is permitted")
        if v > MAX_SAFE_INTEGER:
            raise CanonicalizationRefused(f"{path}: integer exceeds 2**53-1")
        return str(v)
    if isinstance(v, float):
        # §2.4 — refused outright rather than rounded.
        raise CanonicalizationRefused(f"{path}: floating point is refused, not rounded")
    if isinstance(v, str):
        return _string(v, what=path)
    if isinstance(v, Mapping):
        return _object(v, path=path)
    if isinstance(v, (list, tuple)) and isinstance(v, Sequence):
        # §2.7 — order is significant and preserved exactly as submitted.
        return "[" + ",".join(_value(x, path=f"{path}[{i}]") for i, x in enumerate(v)) + "]"
    raise CanonicalizationRefused(f"{path}: {type(v).__name__} has no canonical JSON form")


def _object(m: Mapping[str, Any], *, path: str) -> str:
    names = []
    for k in m:
        if not isinstance(k, str):
            raise CanonicalizationRefused(f"{path}: member name must be a string")
        names.append(k)
    names.sort(key=_member_sort_key)
    parts = [f"{_string(k, what=f'{path}.{k} (member name)')}:{_value(m[k], path=f'{path}.{k}')}"
             for k in names]
    return "{" + ",".join(parts) + "}"


def canonical_bytes(doc: Mapping[str, Any]) -> bytes:
    """The one byte string this document has under the §2 profile.

    UTF-8, no BOM, no insignificant whitespace, members ordered by UTF-16 code
    unit. Raises :class:`CanonicalizationRefused` rather than normalizing.
    """
    if not isinstance(doc, Mapping):
        raise CanonicalizationRefused("a submission is one JSON object")
    return _object(doc, path="$").encode("utf-8")


def envelope_content_hash(blob: bytes) -> str:
    """The **envelope** member ``content_hash``: bare 64 lowercase hex, no prefix.

    This is the one byte-hashing operation the contract sanctions, and it fills
    an *envelope* member — mirroring ``make_envelope`` deriving ``content_hash``
    and ``size_bytes`` from the blob so they cannot disagree with it.

    It is **not** the submission's ``content_digest`` and its bare return value
    cannot be mistaken for one. To obtain the submission member, build the
    envelope and then call :func:`content_digest_from_envelope`; ADR-0068's
    mapping clarification forbids hashing content bytes a second time.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("envelope_content_hash hashes the content blob")
    return hashlib.sha256(bytes(blob)).hexdigest()


def content_digest_from_content_hash(content_hash: str) -> str:
    """The submission member ``content_digest``, derived from bare hex.

    **This is the derivation ADR-0068 mandates** for the submission's
    ``content_digest``::

        content_digest == "sha256:" + envelope.content_hash

    A total function of a value already present in the same signed document.
    Nothing is computed, so the two values cannot be signed while disagreeing.
    """
    if not isinstance(content_hash, str):
        raise TypeError(
            "content_digest is derived from the envelope's content_hash (bare "
            "64 lowercase hex), not from content bytes")
    if not BARE_SHA256.match(content_hash):
        raise CanonicalizationRefused(
            "envelope content_hash must be bare 64 lowercase hex; a 'sha256:' "
            "prefix is wire presentation and never the stored spelling")
    return "sha256:" + content_hash


def content_digest_from_envelope(envelope: Mapping[str, Any]) -> str:
    """The submission member ``content_digest``, derived from the envelope.

    **This is the derivation ADR-0068 mandates** and the one a producer should
    call: build the envelope first, then emit ``content_digest`` from it.
    """
    if not isinstance(envelope, Mapping):
        raise CanonicalizationRefused("an envelope is one JSON object")
    if "content_hash" not in envelope:
        raise CanonicalizationRefused(
            "envelope carries no content_hash; content_digest is derived from "
            "it and is never computed over content bytes")
    return content_digest_from_content_hash(envelope["content_hash"])


def content_hash_from_content_digest(content_digest: str) -> str:
    """Strip the wire prefix at the door; return the bare stored spelling.

    Consequence 2 of the mapping clarification: the ``sha256:`` prefix is wire
    presentation, stripped at the door and never entering the store, so the
    existing content-keyed dedup index is unchanged. Lossless in both
    directions with :func:`content_digest_from_content_hash`.
    """
    if not isinstance(content_digest, str):
        raise TypeError("content_digest is a string")
    if not SHA256_ID.match(content_digest):
        raise CanonicalizationRefused(
            "content_digest must be 'sha256:' + 64 lowercase hex")
    return content_digest[len("sha256:"):]


def verify_content_digest(doc: Mapping[str, Any]) -> str:
    """Door check: an encoding check of one value, not a reconciliation of two.

    Validates that the received ``content_digest`` equals ``"sha256:"`` plus the
    received ``envelope.content_hash``, then returns the bare hex to store. A
    document in which they differ is *malformed*, not contested, and is refused
    with ``SUBMISSION_ENCODING_INVALID``. No content blob is read: ``content``
    and ``encoding`` are the transport shell outside the signed document.
    """
    if not isinstance(doc, Mapping):
        raise CanonicalizationRefused("a submission is one JSON object")
    if "content_digest" not in doc:
        raise CanonicalizationRefused("submission carries no content_digest")
    if "envelope" not in doc:
        raise CanonicalizationRefused("submission carries no envelope")
    expected = content_digest_from_envelope(doc["envelope"])
    received = doc["content_digest"]
    if received != expected:
        raise CanonicalizationRefused(
            "content_digest does not equal 'sha256:' + envelope.content_hash; "
            "the document is malformed, not contested")
    return content_hash_from_content_digest(expected)


def submission_artifact_id(doc: Mapping[str, Any]) -> str:
    """ADR-0068 — the **submission event** identity.

    ``sha256`` of this document's own canonical bytes, spelled ``sha256:`` + 64
    lowercase hex. The document must not carry ``artifact_id``: a document
    computed over its own bytes cannot contain its own hash, which is why the
    clarification took it out of the closed member set (ten members, not eleven).
    """
    if "artifact_id" in doc:
        raise CanonicalizationRefused(
            "artifact_id is not a member of the signed document (ADR-0068 clarification); "
            "it is sha256 of the document's own canonical bytes")
    return "sha256:" + hashlib.sha256(canonical_bytes(doc)).hexdigest()


def verify_route_artifact_id(route_id: str, doc: Mapping[str, Any]) -> str:
    """Recompute the id from the received body and refuse on mismatch.

    This is step 6 of the clarification's implementer sequence, and the reason
    the route segment needs no signature of its own: the id is a pure function
    of the signed bytes, so tampering with either side moves the comparison.
    """
    if not isinstance(route_id, str) or not SHA256_ID.match(route_id):
        raise RouteIdMismatch("route artifact_id must be sha256: + 64 lowercase hex")
    computed = submission_artifact_id(doc)
    if route_id != computed:
        raise RouteIdMismatch("route artifact_id does not equal sha256 of the received canonical bytes")
    return computed


# --- the signed submission event (ADR-0064; 10-federated-identity-attribution) --------

DOMAIN = b'doublegate.submission.v1\0'
EVENT_FIELDS = frozenset({'contract', 'envelope', 'content_digest',
    'evidence_manifest_digest', 'tenant_id', 'team_id', 'principal', 'membership',
    'submitted_at', 'review_refs', 'attribution_chain'})


def canonical_submission(value: Any) -> bytes:
    """Restricted JCS bytes of any JSON value, under the same profile as :func:`canonical_bytes`.

    ``canonical_bytes`` is the document form (one object); this accepts any JSON value so a
    member can be canonicalized on its own. Both refuse non-NFC strings, invalid Unicode,
    floats and integers outside ``[0, 2**53-1]`` with a :class:`CanonicalizationRefused`,
    which is a ``ValueError``.
    """
    try:
        return _value(value, path="$").encode("utf-8")
    except RecursionError as exc:
        raise CanonicalizationRefused("nesting too deep") from exc


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
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
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

"""ADR-0064 attribution parsing and optional Ed25519 JWS verification.

``validate_attribution`` and ``decode_attribution`` check wire data only; parsing
NEVER authenticates a signer. ``verify_attribution(jws, *, signer_key: PublicKey,
expected_binding: AttributionBinding) -> AttributionV1`` requires the optional
``doublegate-sdk[identity]`` extra, a caller-supplied enrolled public key and
trusted request bindings. It checks the signature, bindings and a 30-second
future-issuance tolerance against the receiver's supplied time.

A valid signature is NOT current authority. No enrollment, membership/grant
lookup, revocation, replay prevention, network access or gate effects occur here.
Gate callers must check attestation scope and current authority before effects.
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable

from doublegate_sdk.envelope import canonical_json


class AttributionError(ValueError):
    """Malformed attribution wire data (not an authentication decision)."""


@dataclass(frozen=True, slots=True)
class AttributionV1:
    """Immutable canonical payload bytes; untrusted even after validation."""

    _canonical: bytes

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._canonical)

    def canonical_bytes(self) -> bytes:
        return self._canonical


_IDS = frozenset('tenant_id operation_id requester_id actor_id contributor_id '
                 'home_team_id membership_authority issuer_deployment_id audience'.split())
_OPTIONAL_IDS = frozenset({'source_author_ref', 'sponsor_id'})
_IDENTITY_KINDS = frozenset({'membership', 'mapping', 'relationship', 'policy',
                             'attester_enrollment'})
_REQUIRED = _IDS | {'attribution_version', 'phase', 'envelope_digest', 'evidence_digest',
                    'membership_observed_at', 'issued_at', 'grant_refs',
                    'identity_refs', 'authority_ref'}


def _identifier(value: Any) -> None:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise AttributionError('identifier must contain 1–512 Unicode code points')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise AttributionError('invalid Unicode') from exc


def _digest(value: Any) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
            c not in '0123456789abcdef' for c in value):
        raise AttributionError('digest must be lowercase SHA-256 hex')


def _closed(value: Any, required: set | frozenset,
            optional: set | frozenset = frozenset()) -> None:
    if not isinstance(value, Mapping) or not required <= value.keys() or (
            value.keys() - required - optional):
        raise AttributionError('object has missing or unknown fields')


def validate_attribution(payload: Mapping[str, Any]) -> AttributionV1:
    """Validate closed shape and pure invariants, NEVER identity or authority."""
    _closed(payload, _REQUIRED, _OPTIONAL_IDS | {'parent_statement_digest'})
    if type(payload['attribution_version']) is not int or payload['attribution_version'] != 1:
        raise AttributionError('attribution_version must be integer 1')
    if payload['phase'] not in ('production', 'submission'):
        raise AttributionError('phase must be production or submission')
    for name in _IDS | (_OPTIONAL_IDS & payload.keys()):
        _identifier(payload[name])
    for name in ('envelope_digest', 'evidence_digest'):
        _digest(payload[name])
    if payload['phase'] == 'submission':
        _digest(payload.get('parent_statement_digest'))
    elif 'parent_statement_digest' in payload:
        raise AttributionError('production forbids a parent')
    for name in ('issued_at', 'membership_observed_at'):
        value = payload[name]
        if type(value) is not int or not 0 <= value <= 9007199254740991:
            raise AttributionError(f'{name} must be a safe nonnegative integer')
    if payload['membership_observed_at'] > payload['issued_at']:
        raise AttributionError('membership observation follows issuance')
    _closed(payload['identity_refs'], _IDENTITY_KINDS)
    for ref in payload['identity_refs'].values():
        _closed(ref, {'revision', 'digest'})
        _identifier(ref['revision'])
        _digest(ref['digest'])
    authority = payload['authority_ref']
    _closed(authority, {'revision', 'digest'})
    if type(authority['revision']) is not int or not 1 <= authority['revision'] <= 9007199254740991:
        raise AttributionError('authority revision must be a positive safe integer')
    _digest(authority['digest'])
    _closed(payload['grant_refs'], {'requester', 'actor'})
    for refs in payload['grant_refs'].values():
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise AttributionError('each role grant_refs must contain 1–8 records')
        seen = set()
        for ref in refs:
            _closed(ref, {'grant_id', 'grant_digest'})
            _identifier(ref['grant_id'])
            _digest(ref['grant_digest'])
            if ref['grant_id'] in seen:
                raise AttributionError('duplicate grant_id')
            seen.add(ref['grant_id'])
    return AttributionV1(canonical_json(dict(payload)))


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AttributionError('duplicate JSON key')
            result[key] = value
        return result

    def no_number(value):
        raise AttributionError('non-integer JSON number')

    def visit(value):
        if value is None:
            raise AttributionError('null is forbidden')
        if isinstance(value, str):
            value.encode('utf-8')
        elif isinstance(value, dict):
            for key, entry in value.items():
                visit(key)
                visit(entry)
        elif isinstance(value, list):
            for entry in value:
                visit(entry)

    if not isinstance(raw, bytes):
        raise AttributionError('JSON wire input must be UTF-8 bytes')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_float=no_number, parse_constant=no_number)
        visit(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AttributionError('invalid strict JSON') from exc


@dataclass(frozen=True, slots=True)
class PublicKey:
    """Caller-supplied enrolled attribution key, NOT evidence of enrollment.

    Gate-owned trusted configuration must bind this kid and deployment to the raw
    32-byte Ed25519 public key. Never construct it from untrusted statement claims.
    No private keys, JWKS lookup, TOFU or enrollment occurs in the SDK.
    """

    kid: str
    issuer_deployment_id: str
    ed25519_bytes: bytes


@dataclass(frozen=True, slots=True)
class AttributionBinding:
    """Expected gate-owned request binding, not authority or token claims.

    Supply digests from the actual envelope/retained evidence manifest. For a
    submission, supply the digest of the exact production JWS as parent. ``now``
    is receiver time in Unix seconds, not the signer's claimed observation time.
    """

    tenant_id: str
    operation_id: str
    envelope_digest: str
    evidence_digest: str
    requester_id: str
    actor_id: str
    contributor_id: str
    home_team_id: str
    audience: str
    phase: str
    now: int
    parent_statement_digest: str | None = None


def verify_attribution(jws: str, *, signer_key: PublicKey,
                       expected_binding: AttributionBinding) -> AttributionV1:
    """Verify Ed25519 JWS cryptography; the returned payload is NOT authorization.

    Requires ``doublegate-sdk[identity]``. No network, membership/grant lookup,
    revocation check, replay journal, evidence retrieval or mutation takes place.
    Gates must verify attestation scope and current authority before any effects.
    """
    from doublegate_sdk.identity_wire import decode_attribution_jws
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from jwt import api_jws
        from jwt.exceptions import PyJWTError
    except ImportError as exc:
        raise AttributionError('verification requires doublegate-sdk[identity]') from exc

    parsed = decode_attribution_jws(jws)
    claims = parsed.payload.to_dict()
    now = expected_binding.now
    if type(now) is not int or not 0 <= now <= 9007199254740991:
        raise AttributionError('receiver time must be a safe nonnegative integer')
    if claims['issued_at'] > now + 30:
        raise AttributionError('attribution observation is in the future')
    if parsed.header['kid'] != signer_key.kid:
        raise AttributionError('kid does not match enrolled key')
    if claims['issuer_deployment_id'] != signer_key.issuer_deployment_id:
        raise AttributionError('issuer_deployment_id does not match enrolled key')
    for field in ('tenant_id', 'operation_id', 'envelope_digest', 'evidence_digest',
                  'requester_id', 'actor_id', 'contributor_id', 'home_team_id',
                  'audience', 'phase', 'parent_statement_digest'):
        if claims.get(field) != getattr(expected_binding, field):
            raise AttributionError(f'{field} does not match expected binding')
    try:
        key = Ed25519PublicKey.from_public_bytes(signer_key.ed25519_bytes)
        api_jws.decode_complete(jws, key=key, algorithms=['EdDSA'])
    except (PyJWTError, ValueError, TypeError) as exc:
        raise AttributionError('invalid attribution signature or public key') from exc
    return parsed.payload


def decode_attribution(raw: bytes) -> AttributionV1:
    """Decode an exact canonical payload, without authenticating it."""
    result = validate_attribution(_strict_json(raw))
    if result.canonical_bytes() != raw:
        raise AttributionError('payload is not canonical JSON')
    return result


class AccessTokenError(ValueError):
    """Access-token verification or trusted identity mapping failed closed."""


@dataclass(frozen=True, slots=True)
class IssuerProfile:
    """Owner-enrolled first RFC 9068 profile, never constructed from a token.

    Exact tenant claim name and direct/app_only interpretation are enrollment
    inputs, not universal OIDC conventions. RS256, 300s TTL and 30s tolerance are
    fixed for this bounded profile. Delegation/OBO is deliberately unsupported.
    No membership authority is implemented here; token groups confer nothing.
    """

    issuer: str
    tenant_id: str
    audience: str
    tenant_claim: str
    mapping_revision: str
    token_kind: str

    def __post_init__(self) -> None:
        for value in (self.issuer, self.tenant_id, self.audience, self.tenant_claim,
                      self.mapping_revision, self.token_kind):
            _access_identifier(value)
        if self.token_kind not in ('direct', 'app_only'):
            raise AccessTokenError('only direct and app_only profiles are supported')
        if self.tenant_claim in {'iss', 'sub', 'aud', 'exp', 'iat', 'nbf', 'jti',
                                 'client_id', 'act', 'scope', 'groups', 'roles'}:
            raise AccessTokenError('tenant mapping must use a distinct enrolled claim')


@dataclass(frozen=True, slots=True)
class IdentityMappingRequest:
    """Verified external identifiers only, for an enrolled trusted resolver."""

    issuer: str
    tenant_id: str
    subject: str
    client_id: str
    mapping_revision: str
    token_kind: str


@dataclass(frozen=True, slots=True)
class ResolvedIdentity:
    """Trusted mapping output, NOT membership, a grant or proof of human presence."""

    requester_id: str
    requester_kind: str
    actor_id: str
    actor_kind: str
    client_principal_id: str


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """Cryptographically valid hop identity, NEVER an authorization capability.

    No raw groups, roles, scope, grant or team claims are promoted to authority.
    Gates still own current grants, membership, revocation and human-action proof.
    """

    issuer: str
    tenant_id: str
    subject: str
    client_id: str
    audience: str
    token_id: str
    issued_at: int
    expires_at: int
    not_before: int | None
    mapping_revision: str
    token_kind: str
    requester_id: str
    requester_kind: str
    actor_id: str
    actor_kind: str
    client_principal_id: str


def _access_identifier(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise AccessTokenError('identity fields must be nonempty strings')
    try:
        value.encode('utf-8')
    except UnicodeError as exc:
        raise AccessTokenError('invalid Unicode identifier') from exc


def _access_json(raw: bytes) -> dict:
    # JWT extension values need not satisfy the closed attribution schema, but
    # duplicate keys (even nested) must never let two parsers disagree.
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AccessTokenError('duplicate JSON key')
            result[key] = value
        return result

    def invalid_constant(value):
        raise AccessTokenError('invalid JSON constant')

    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=pairs,
                           parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise AccessTokenError('JWT header and claims must be objects')
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AccessTokenError('invalid access-token JSON') from exc


def verify_access_token(
    token: str, *, issuer_profile: IssuerProfile, jwks: Mapping[str, Any],
    resolver: Callable[[IdentityMappingRequest], ResolvedIdentity], now: int,
) -> TokenClaims:
    """Verify with caller-supplied enrolled JWKS and a trusted identity resolver.

    Requires the optional identity extra. Does not discover issuers, fetch keys,
    refresh unknown kids, enroll identities or perform gate/admission effects.
    The caller owns bounded JWKS refresh/rotation and mapping freshness. Resolver
    input is signature-verified identifiers, not token-directed lookup locations.
    """
    try:
        import jwt
        from jwt.exceptions import PyJWTError
    except ImportError as exc:
        raise AccessTokenError('verification requires doublegate-sdk[identity]') from exc
    p = issuer_profile
    if not isinstance(p, IssuerProfile):
        raise AccessTokenError('an enrolled IssuerProfile is required')
    if type(now) is not int or now < 0:
        raise AccessTokenError('receiver time must be nonnegative integer seconds')
    try:
        from jwt.utils import base64url_decode, base64url_encode
        if not isinstance(token, str) or len(token.split('.')) != 3:
            raise AccessTokenError('compact JWT must contain three segments')
        parts = []
        for segment in token.split('.'):
            if not segment or any(c not in
                    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'
                    for c in segment):
                raise AccessTokenError('JWT segments must be unpadded base64url')
            raw = base64url_decode(segment)
            if base64url_encode(raw).decode('ascii') != segment:
                raise AccessTokenError('invalid base64url encoding')
            parts.append(raw)
        header = _access_json(parts[0])
        claims = _access_json(parts[1])
        if set(header) != {'alg', 'typ', 'kid'} or (
                header['alg'] != 'RS256' or header['typ'] != 'at+jwt'):
            raise AccessTokenError('unsupported access-token JOSE profile')
        _access_identifier(header['kid'])
        if not isinstance(jwks, Mapping) or not isinstance(jwks.get('keys'), list):
            raise AccessTokenError('JWKS must contain a keys list')
        if any(not isinstance(key, Mapping) for key in jwks['keys']):
            raise AccessTokenError('invalid JWKS key record')
        matches = [key for key in jwks['keys'] if key.get('kid') == header['kid']]
        if len(matches) != 1:
            raise AccessTokenError('unknown or ambiguous kid')
        enrolled = dict(matches[0])
        if (enrolled.get('kty') != 'RSA' or enrolled.get('alg', 'RS256') != 'RS256'
                or enrolled.get('use', 'sig') != 'sig'
                or enrolled.get('key_ops', ['verify']) != ['verify']
                or {'d', 'p', 'q', 'dp', 'dq', 'qi', 'oth'} & enrolled.keys()):
            raise AccessTokenError('enrolled key must be a public RS256 signing key')
        key = jwt.PyJWK.from_dict(enrolled, algorithm='RS256')
        # PyJWT performs crypto and exact issuer/audience/required-claim checks.
        # Time is checked below against caller-supplied receiver time, not a
        # hidden wall clock. Strict JSON above precedes PyJWT's permissive parser.
        jwt.decode(token, key=key.key, algorithms=['RS256'], issuer=p.issuer,
                   audience=p.audience, options={
                       'require': ['iss', 'sub', 'aud', 'exp', 'iat', 'jti', 'client_id'],
                       'verify_exp': False, 'verify_iat': False, 'verify_nbf': False,
                       'strict_aud': True})
    except (PyJWTError, ValueError, TypeError, KeyError, OverflowError) as exc:
        raise AccessTokenError('invalid access token or enrolled key') from exc
    for name in ('sub', 'client_id', 'jti', p.tenant_claim):
        _access_identifier(claims.get(name))
    if claims[p.tenant_claim] != p.tenant_id:
        raise AccessTokenError('token tenant conflicts with enrolled tenant')
    if 'act' in claims:
        raise AccessTokenError('delegation requires a separately qualified issuer profile')
    for name in ('exp', 'iat', 'nbf'):
        if name in claims and (type(claims[name]) is not int or claims[name] < 0):
            raise AccessTokenError('token times must be nonnegative integer seconds')
    issued, expires = claims['iat'], claims['exp']
    if not 0 < expires - issued <= 300:
        raise AccessTokenError('access-token lifetime must be 1–300 seconds')
    if issued > now + 30 or claims.get('nbf', 0) > now + 30:
        raise AccessTokenError('access token is not yet valid')
    if expires <= now - 30:
        raise AccessTokenError('access token expired')
    if 'nbf' in claims and claims['nbf'] >= expires:
        raise AccessTokenError('not-before must precede expiry')
    try:
        mapped = resolver(IdentityMappingRequest(p.issuer, p.tenant_id, claims['sub'],
                                                 claims['client_id'], p.mapping_revision,
                                                 p.token_kind))
    except Exception as exc:
        raise AccessTokenError('trusted identity mapping unavailable') from exc
    if not isinstance(mapped, ResolvedIdentity):
        raise AccessTokenError('identity mapping unresolved')
    for value in (mapped.requester_id, mapped.actor_id, mapped.client_principal_id):
        _access_identifier(value)
    if (mapped.requester_kind not in ('human', 'workload')
            or mapped.actor_kind != mapped.requester_kind
            or mapped.actor_id != mapped.requester_id):
        raise AccessTokenError('direct/app-only mapping must have requester=actor')
    if p.token_kind == 'app_only' and (mapped.requester_kind != 'workload'
                                      or mapped.client_principal_id != mapped.actor_id):
        raise AccessTokenError('app-only mapping must identify the presenting workload')
    return TokenClaims(p.issuer, p.tenant_id, claims['sub'], claims['client_id'],
                       p.audience, claims['jti'], claims['iat'], claims['exp'],
                       claims.get('nbf'), p.mapping_revision, p.token_kind,
                       mapped.requester_id, mapped.requester_kind, mapped.actor_id,
                       mapped.actor_kind, mapped.client_principal_id)

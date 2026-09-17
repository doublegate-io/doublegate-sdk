"""Sign in with your provider; give a program a key (ADR-0082, AUTH-1 to AUTH-6).

This module owns the whole of authentication and the role model for every
service. A service carries one ``[auth]`` block, builds an :class:`Authenticator`
from it, and on every request calls :meth:`Authenticator.principal`, which
answers a :class:`Principal` or raises :class:`AuthError` with a reason. Nothing
else in a service reads a token, hashes a key or names a role.

Three principals: a *person* signed in through OpenID Connect at the one issuer
the deployment group names, a *program* holding an API key this service issued
(or a workload token from a trusted issuer), and a *local* Unix-socket peer. The
base module is standard library only; verifying a bearer JWT needs the
``identity`` extra (``PyJWT[crypto]``), imported lazily. A service without it
still authenticates API keys and socket peers and refuses every JWT with
``AuthError('unsupported')``.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Literal

from doublegate_sdk.errors import DoublegateError

# ---------------------------------------------------------------- roles (AUTH-4)

ROLES: tuple[str, ...] = ("reader", "agent", "reviewer", "admin")
ROLE_RANK: Mapping[str, int] = MappingProxyType({role: rank for rank, role in enumerate(ROLES)})

Role = Literal["reader", "agent", "reviewer", "admin"]
Kind = Literal["person", "program", "local"]
Via = Literal["oidc", "api_key", "socket"]


def role_allows(have: str, need: str) -> bool:
    """``True`` when a principal holding ``have`` may do what ``need`` guards."""
    if have not in ROLE_RANK or need not in ROLE_RANK:
        raise ValueError(f"unknown role: {have if have not in ROLE_RANK else need!r}")
    return ROLE_RANK[have] >= ROLE_RANK[need]


def canonical_identity(*, iss: str | None = None, sub: str | None = None,
                       key_id: str | None = None, uid: int | None = None) -> str:
    """The one identity string a journal line carries (AUTH-5).

    ``"<iss>|<sub>"`` for a token, ``"key:<key_id>"`` for an API key,
    ``"uid:<n>"`` for a socket peer. Exactly one form must be given.
    """
    if iss is not None and sub is not None and key_id is None and uid is None:
        return f"{iss}|{sub}"
    if key_id is not None and iss is None and sub is None and uid is None:
        return f"key:{key_id}"
    if uid is not None and iss is None and sub is None and key_id is None:
        return f"uid:{int(uid)}"
    raise ValueError("canonical_identity takes iss and sub, or key_id, or uid")


@dataclass(frozen=True)
class Principal:
    """Who is calling, as the service records it (AUTH-1)."""

    kind: Kind
    identity: str          # canonical: "<iss>|<sub>" · "key:<key_id>" · "uid:<n>"
    role: Role
    iss: str | None        # person and workload tokens
    sub: str | None
    email: str | None
    name: str | None
    jti: str | None        # the token's id, recorded on every event the principal causes
    key_id: str | None     # programs authenticated by an API key
    via: Via

    def as_record(self) -> dict[str, Any]:
        """The ``principal`` object a journal event carries (AUTH-5); absent fields omitted."""
        record = {"kind": self.kind, "role": self.role, "iss": self.iss, "sub": self.sub,
                  "key_id": self.key_id, "jti": self.jti}
        return {name: value for name, value in record.items() if value is not None}


# --------------------------------------------------------------------- errors

AUTH_ERROR_KINDS: frozenset[str] = frozenset({
    "missing", "malformed", "expired", "issuer", "audience", "signature",
    "unavailable", "unsupported",
})


class AuthError(DoublegateError):
    """A refusal at the door. ``kind`` is what a service acts on; the message is ours, never a token's."""

    def __init__(self, kind: str, message: str) -> None:
        if kind not in AUTH_ERROR_KINDS:
            raise ValueError(f"unknown AuthError kind: {kind!r}")
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message


# ------------------------------------------------------------ configuration (AUTH-6)

@dataclass(frozen=True)
class TrustedIssuer:
    """A workload issuer and the audience its tokens must carry."""

    issuer: str
    audience: str


_CONFIG_KEYS = frozenset({"issuer", "client_id", "admins", "trusted_issuers", "roles_claim",
                          "role_map", "local_issuer_url", "issuer_url"})


def _is_url(value: str) -> bool:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme == "https" and parts.netloc:
        return True
    return parts.scheme == "http" and parts.hostname in ("127.0.0.1", "localhost", "::1")


@dataclass(frozen=True)
class AuthConfig:
    """The ``[auth]`` block every service carries (AUTH-6)."""

    issuer: str = "local"
    client_id: str = "doublegate-console"
    admins: tuple[str, ...] = ()
    trusted_issuers: tuple[TrustedIssuer, ...] = ()
    roles_claim: str = ""
    role_map: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    local_issuer_url: str = ""   # the URL the built-in issuer names itself by, when issuer == "local"

    def __post_init__(self) -> None:
        for name in ("issuer", "client_id", "roles_claim", "local_issuer_url"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"[auth].{name} must be a string")
        if not self.issuer:
            raise ValueError('[auth].issuer must be "local" or the issuer URL')
        if self.issuer != "local" and not _is_url(self.issuer):
            raise ValueError('[auth].issuer must be "local" or an https URL (http only on loopback)')
        if self.local_issuer_url and not _is_url(self.local_issuer_url):
            raise ValueError("[auth].issuer_url must be an https URL (http only on loopback)")
        if not self.client_id:
            raise ValueError("[auth].client_id must not be empty")
        if not all(isinstance(a, str) and a for a in self.admins):
            raise ValueError("[auth].admins must be subjects or emails")
        for entry in self.trusted_issuers:
            if not isinstance(entry, TrustedIssuer) or not _is_url(entry.issuer) or not entry.audience:
                raise ValueError("[auth].trusted_issuers entries need an issuer URL and an audience")
        for value, role in self.role_map.items():
            if not isinstance(value, str) or role not in ROLES:
                raise ValueError(f"[auth.role_map] {value!r} must map to one of {', '.join(ROLES)}")
        object.__setattr__(self, "admins", tuple(self.admins))
        object.__setattr__(self, "trusted_issuers", tuple(self.trusted_issuers))
        object.__setattr__(self, "role_map", MappingProxyType(dict(self.role_map)))

    @property
    def issuer_url(self) -> str:
        """The URL tokens must name as ``iss``: the configured issuer, or the local one."""
        return self.local_issuer_url if self.issuer == "local" else self.issuer

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> AuthConfig:
        """Build from a parsed ``[auth]`` table; unknown keys and wrong types are refused by name."""
        if not isinstance(data, Mapping):
            raise ValueError("[auth] must be a table")
        unknown = sorted(set(data) - _CONFIG_KEYS)
        if unknown:
            raise ValueError(f"[auth] has unknown key {unknown[0]!r}")
        if "issuer_url" in data and "local_issuer_url" in data:
            raise ValueError("[auth] names both issuer_url and local_issuer_url; keep one")
        raw_trusted = data.get("trusted_issuers", ())
        if not isinstance(raw_trusted, (list, tuple)):
            raise ValueError("[auth].trusted_issuers must be a list of {issuer, audience} tables")
        trusted = []
        for entry in raw_trusted:
            if not isinstance(entry, Mapping) or set(entry) != {"issuer", "audience"} or not all(
                    isinstance(entry[k], str) for k in ("issuer", "audience")):
                raise ValueError("[auth].trusted_issuers entries are {issuer = ..., audience = ...}")
            trusted.append(TrustedIssuer(entry["issuer"], entry["audience"]))
        admins = data.get("admins", ())
        if not isinstance(admins, (list, tuple)):
            raise ValueError("[auth].admins must be a list of subjects or emails")
        role_map = data.get("role_map", {})
        if not isinstance(role_map, Mapping):
            raise ValueError("[auth.role_map] must be a table")
        return cls(
            issuer=data.get("issuer", "local"),
            client_id=data.get("client_id", "doublegate-console"),
            admins=tuple(admins),
            trusted_issuers=tuple(trusted),
            roles_claim=data.get("roles_claim", ""),
            role_map=role_map,
            local_issuer_url=data.get("local_issuer_url", data.get("issuer_url", "")),
        )


# ------------------------------------------------------------------ API keys (AUTH-3)

API_KEY_PREFIX = "dgk_"
_B64URL = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


def new_api_key() -> tuple[str, str, str]:
    """Mint a key: ``(key, sha256_hex, key_id)``. The key is shown once; store the hash."""
    key = API_KEY_PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = api_key_hash(key)
    return key, digest, key_id_of(digest)


def is_api_key(token: str) -> bool:
    """A bearer that starts with ``dgk_`` is routed to the key table, whatever follows."""
    return isinstance(token, str) and token.startswith(API_KEY_PREFIX)


def _well_formed_api_key(token: str) -> bool:
    body = token[len(API_KEY_PREFIX):]
    return len(body) == 43 and set(body) <= _B64URL


def api_key_hash(token: str) -> str:
    """``sha256(key)`` as lowercase hex, the only form a service stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def key_id_of(digest: str) -> str:
    """The first 12 hex characters of the hash: what people see."""
    return digest[:12]


@dataclass(frozen=True)
class KeyRow:
    """One issued key, folded from the journal. Times are epoch milliseconds, the journal's unit."""

    key_id: str
    hash: str
    role: Role
    label: str
    issued_by: str
    issued_at: int | None
    expires_at: int | None
    revoked_at: int | None

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def expired(self, now_ms: int) -> bool:
        return self.expires_at is not None and self.expires_at <= now_ms


def fold_keys(events: Iterable[Mapping[str, Any]]) -> dict[str, KeyRow]:
    """Fold ``apikey-issued`` / ``apikey-revoked`` events into rows keyed by hash.

    Each event is a mapping with ``event_type`` (or ``type``); its fields sit at
    the top level or under ``payload`` (the journal's shape), with ``ts`` as the
    fallback for ``issued_at`` and ``revoked_at``. Field names follow the
    organization gate's key table: ``key_hash``, ``label``, ``issued_by``,
    ``expires_at``. A revoke for an unknown key is ignored; a revoked key stays
    revoked. An issued row whose ``role`` is not one of the four is a corrupt
    journal and raises ``ValueError``.
    """
    rows: dict[str, KeyRow] = {}
    for event in events:
        kind = event.get("event_type") or event.get("type")
        payload = event.get("payload")
        fields: dict[str, Any] = dict(event)
        if isinstance(payload, Mapping):
            fields.update(payload)
        digest = fields.get("key_hash")
        if kind == "apikey-issued":
            if not isinstance(digest, str) or len(digest) != 64:
                raise ValueError("apikey-issued event without a sha256 key_hash")
            role = fields.get("role")
            if role not in ROLES:
                raise ValueError(f"apikey-issued {key_id_of(digest)} carries role {role!r}, not one of {', '.join(ROLES)}")
            rows[digest] = KeyRow(
                key_id=fields.get("key_id") or key_id_of(digest), hash=digest, role=role,
                label=str(fields.get("label") or ""), issued_by=str(fields.get("issued_by") or ""),
                issued_at=fields.get("issued_at", fields.get("ts")),
                expires_at=fields.get("expires_at"), revoked_at=None)
        elif kind == "apikey-revoked":
            row = rows.get(digest) if isinstance(digest, str) else None
            if row is not None and row.revoked_at is None:
                rows[digest] = replace(row, revoked_at=fields.get("revoked_at", fields.get("ts")))
    return rows


# --------------------------------------------------------- token verification (AUTH-2)

ALLOWED_ALGORITHMS: tuple[str, ...] = ("RS256", "ES256", "EdDSA")
_MAX_DOCUMENT = 1 << 20
_REFETCH_COOLDOWN = 60


def identity_extra_installed() -> bool:
    """Whether ``doublegate-sdk[identity]`` (PyJWT with cryptography) is importable."""
    return all(importlib.util.find_spec(name) is not None for name in ("jwt", "cryptography"))


def default_fetch(url: str) -> bytes:
    """``urllib`` GET with a 5 s timeout; https only, except plain http on loopback."""
    if not _is_url(url):
        raise AuthError("unavailable", "issuer documents are fetched over https (http only on loopback)")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}),
                                    timeout=5) as response:  # noqa: S310 — scheme checked above
            return response.read(_MAX_DOCUMENT + 1)
    except OSError as exc:
        raise AuthError("unavailable", f"could not fetch {url}: {exc.__class__.__name__}") from exc


def _algorithm_of(jwk: Mapping[str, Any]) -> str | None:
    if {"d", "p", "q", "dp", "dq", "qi", "oth", "k"} & set(jwk) or jwk.get("use", "sig") != "sig":
        return None
    kty, crv = jwk.get("kty"), jwk.get("crv")
    implied = {("RSA", None): "RS256", ("EC", "P-256"): "ES256", ("OKP", "Ed25519"): "EdDSA"}.get(
        (kty, crv if kty != "RSA" else None))
    declared = jwk.get("alg", implied)
    return declared if declared == implied and declared in ALLOWED_ALGORITHMS else None


class IssuerKeys:
    """An issuer's discovery document and key set, fetched once and cached by ``kid``.

    ``fetch(url) -> bytes`` is injectable; the default is :func:`default_fetch`.
    An unknown ``kid`` triggers one refetch, at most once per minute.
    """

    def __init__(self, issuer: str, audience: str, *, fetch: Callable[[str], bytes] | None = None) -> None:
        if not isinstance(issuer, str) or not _is_url(issuer):
            raise ValueError("an issuer is an https URL (http only on loopback)")
        if not isinstance(audience, str) or not audience:
            raise ValueError("an issuer needs the audience its tokens carry")
        self.issuer = issuer
        self.audience = audience
        self._fetch = fetch or default_fetch
        self._keys: dict[str, Mapping[str, Any]] | None = None
        self._jwks_uri: str | None = None
        self._last_refetch = 0.0

    @property
    def discovery_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"

    def _document(self, url: str) -> Mapping[str, Any]:
        raw = self._fetch(url)
        if not isinstance(raw, bytes) or len(raw) > _MAX_DOCUMENT:
            raise AuthError("unavailable", f"{url} answered something other than a bounded document")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise AuthError("unavailable", f"{url} is not JSON") from exc
        if not isinstance(document, Mapping):
            raise AuthError("unavailable", f"{url} is not a JSON object")
        return document

    def refresh(self) -> None:
        """Fetch discovery (once) and the JWKS; replaces the cache."""
        if self._jwks_uri is None:
            discovery = self._document(self.discovery_url)
            if discovery.get("issuer") != self.issuer:
                raise AuthError("issuer", "discovery document names a different issuer")
            jwks_uri = discovery.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not _is_url(jwks_uri):
                raise AuthError("unavailable", "discovery document has no https jwks_uri")
            self._jwks_uri = jwks_uri
        jwks = self._document(self._jwks_uri)
        entries = jwks.get("keys")
        if not isinstance(entries, list):
            raise AuthError("unavailable", "JWKS has no keys list")
        keys: dict[str, Mapping[str, Any]] = {}
        for entry in entries:
            if isinstance(entry, Mapping) and isinstance(entry.get("kid"), str) and _algorithm_of(entry):
                keys[entry["kid"]] = MappingProxyType(dict(entry))
        self._keys = keys

    def key_for(self, kid: str) -> Mapping[str, Any] | None:
        """The public JWK for ``kid``, refetching once when it is unknown."""
        if self._keys is None:
            self.refresh()
        assert self._keys is not None
        if kid not in self._keys and time.monotonic() - self._last_refetch >= _REFETCH_COOLDOWN:
            self.refresh()
            self._last_refetch = time.monotonic()
        return self._keys.get(kid)


@dataclass(frozen=True)
class TokenClaims:
    """The verified claims of a bearer JWT; identity, not authority."""

    iss: str
    sub: str
    aud: tuple[str, ...]
    exp: int
    nbf: int | None
    iat: int | None
    jti: str | None
    email: str | None
    name: str | None
    claims: Mapping[str, Any]


def _segments(token: Any) -> bool:
    return isinstance(token, str) and token.count(".") == 2 and all(
        part and set(part) <= _B64URL for part in token.split("."))


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def verify_bearer_jwt(token: str, *, issuers: Sequence[IssuerKeys], audience: str | None = None,
                      now: int | None = None, leeway: int = 60) -> TokenClaims:
    """Verify a bearer JWT against one of ``issuers`` (AUTH-2).

    Exact ``iss`` match against one issuer; signature against that issuer's JWKS
    with the algorithm the key declares (RS256, ES256 or EdDSA; ``none`` and HMAC
    are refused); ``aud`` containing ``audience`` when given, else the matched
    issuer's own; ``exp`` and ``nbf`` with ``leeway`` seconds; ``sub`` present.
    Raises :class:`AuthError`.
    """
    if not _segments(token):
        raise AuthError("malformed", "a bearer token is a compact JWT of three segments")
    try:
        import jwt
        from jwt.exceptions import (DecodeError, InvalidAudienceError, InvalidIssuerError,
                                    InvalidSignatureError, MissingRequiredClaimError, PyJWTError)
    except ImportError as exc:
        raise AuthError("unsupported", "verifying a bearer token needs doublegate-sdk[identity]") from exc
    try:
        header = jwt.get_unverified_header(token)
        unverified = jwt.decode(token, options={"verify_signature": False})
    except PyJWTError as exc:
        raise AuthError("malformed", "the token's header or claims are not readable") from exc
    if not isinstance(unverified, Mapping):
        raise AuthError("malformed", "the token's claims are not an object")
    iss = unverified.get("iss")
    issuer = next((entry for entry in issuers if entry.issuer == iss), None)
    if issuer is None:
        raise AuthError("issuer", "the token's issuer is not one this service trusts")
    alg, kid = header.get("alg"), header.get("kid")
    if alg not in ALLOWED_ALGORITHMS:
        raise AuthError("signature", f"algorithm {alg!r} is not accepted; one of {', '.join(ALLOWED_ALGORITHMS)}")
    if not isinstance(kid, str) or not kid:
        raise AuthError("malformed", "the token names no kid")
    jwk = issuer.key_for(kid)
    if jwk is None:
        raise AuthError("signature", "the token's kid is not in the issuer's key set")
    expected_alg = _algorithm_of(jwk)
    if alg != expected_alg:
        raise AuthError("signature", "the token's alg is not the one its key declares")
    expected_audience = audience if audience is not None else issuer.audience
    try:
        claims = jwt.decode(
            token, key=jwt.PyJWK.from_dict(dict(jwk), algorithm=alg).key, algorithms=[alg],
            issuer=issuer.issuer, audience=expected_audience,
            options={"require": ["iss", "sub", "aud", "exp"], "verify_exp": False,
                     "verify_nbf": False, "verify_iat": False})
    except InvalidSignatureError as exc:
        raise AuthError("signature", "the token's signature does not verify") from exc
    except InvalidIssuerError as exc:
        raise AuthError("issuer", "the token's issuer is not one this service trusts") from exc
    except InvalidAudienceError as exc:
        raise AuthError("audience", "the token was not issued for this audience") from exc
    except MissingRequiredClaimError as exc:
        claim = getattr(exc, "claim", "a claim")
        raise AuthError("audience" if claim == "aud" else "malformed", f"the token lacks {claim}") from exc
    except (DecodeError, PyJWTError, ValueError, TypeError) as exc:
        raise AuthError("signature", "the token could not be verified") from exc
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise AuthError("malformed", "the token's sub is not a string")
    exp, nbf, iat = claims.get("exp"), claims.get("nbf"), claims.get("iat")
    if type(exp) not in (int, float):
        raise AuthError("malformed", "the token's exp is not a number")
    current = int(time.time()) if now is None else int(now)
    if exp + leeway <= current:
        raise AuthError("expired", "the token has expired")
    if nbf is not None and (type(nbf) not in (int, float) or nbf - leeway > current):
        raise AuthError("expired", "the token is not yet valid")
    aud = claims["aud"]
    return TokenClaims(
        iss=issuer.issuer, sub=sub, aud=tuple(aud) if isinstance(aud, list) else (aud,),
        exp=int(exp), nbf=int(nbf) if nbf is not None else None,
        iat=int(iat) if type(iat) in (int, float) else None,
        jti=_str_or_none(claims.get("jti")), email=_str_or_none(claims.get("email")),
        name=_str_or_none(claims.get("name")), claims=MappingProxyType(dict(claims)))


# ----------------------------------------------------------- the one call (AUTH-1, AUTH-4)

class Authenticator:
    """Built once from the ``[auth]`` block; answers ``principal()`` on every request.

    ``key_lookup(hash)`` returns the :class:`KeyRow` for a key hash or ``None``;
    ``role_lookup(iss, sub)`` returns the role a ``role-assigned`` event gave this
    identity on this service, or ``None``. ``owner_uid`` is the daemon owner; a
    socket peer with that uid is ``admin``, any other uid is ``agent``. ``fetch``
    and ``now`` are injectable for tests.
    """

    def __init__(self, config: AuthConfig, *, key_lookup: Callable[[str], KeyRow | None],
                 role_lookup: Callable[[str, str], str | None], owner_uid: int | None,
                 fetch: Callable[[str], bytes] | None = None,
                 now: Callable[[], int] | None = None) -> None:
        if not isinstance(config, AuthConfig):
            raise ValueError("Authenticator takes an AuthConfig")
        if not config.issuer_url:
            raise ValueError('[auth].issuer is "local" but no issuer_url names the built-in issuer')
        self.config = config
        self._key_lookup = key_lookup
        self._role_lookup = role_lookup
        self._owner_uid = owner_uid
        self._now = now or (lambda: int(time.time()))
        self._person_issuer = IssuerKeys(config.issuer_url, config.client_id, fetch=fetch)
        self._workload_issuers = tuple(IssuerKeys(entry.issuer, entry.audience, fetch=fetch)
                                       for entry in config.trusted_issuers)

    def principal(self, authorization: str | None, *, socket_peer_uid: int | None = None) -> Principal:
        """Resolve the caller (AUTH-4): socket peer, then API key, then bearer JWT; else refuse."""
        if socket_peer_uid is not None:
            uid = int(socket_peer_uid)
            role: Role = "admin" if self._owner_uid is not None and uid == self._owner_uid else "agent"
            return Principal(kind="local", identity=canonical_identity(uid=uid), role=role, iss=None,
                             sub=None, email=None, name=None, jti=None, key_id=None, via="socket")
        if not authorization or not isinstance(authorization, str) or not authorization.strip():
            raise AuthError("missing", "no credential: sign in, or present an API key")
        scheme, _, token = authorization.strip().partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token or " " in token:
            raise AuthError("malformed", "Authorization must be 'Bearer <token>'")
        if is_api_key(token):
            return self._program_from_key(token)
        claims = verify_bearer_jwt(token, issuers=(self._person_issuer, *self._workload_issuers),
                                   now=self._now())
        if claims.iss == self._person_issuer.issuer:
            return self._person(claims)
        return self._workload(claims)

    def _program_from_key(self, token: str) -> Principal:
        if not _well_formed_api_key(token):
            raise AuthError("malformed", "an API key is dgk_ followed by 43 base64url characters")
        row = self._key_lookup(api_key_hash(token))
        # Unknown, revoked and expired keys refuse identically: no oracle over the key table.
        if row is None or row.revoked or row.expired(self._now() * 1000) or row.role not in ROLES:
            raise AuthError("signature", "the API key is not accepted")
        return Principal(kind="program", identity=canonical_identity(key_id=row.key_id), role=row.role,
                         iss=None, sub=None, email=None, name=None, jti=None, key_id=row.key_id,
                         via="api_key")

    def _assigned(self, iss: str, sub: str) -> Role | None:
        role = self._role_lookup(iss, sub)
        if role is None:
            return None
        if role not in ROLES:
            raise ValueError(f"role_lookup answered {role!r} for {iss}|{sub}; not one of {', '.join(ROLES)}")
        return role

    def _mapped(self, claims: TokenClaims) -> Role | None:
        config = self.config
        if not config.roles_claim or not config.role_map:
            return None
        value = claims.claims.get(config.roles_claim)
        values = value if isinstance(value, list) else [value]
        mapped = [config.role_map[v] for v in values if isinstance(v, str) and v in config.role_map]
        return max(mapped, key=ROLE_RANK.__getitem__) if mapped else None

    def _person(self, claims: TokenClaims) -> Principal:
        role = self._assigned(claims.iss, claims.sub)
        if role is None and (claims.sub in self.config.admins
                             or (claims.email is not None and claims.email in self.config.admins)):
            role = "admin"
        if role is None:
            role = self._mapped(claims)
        return Principal(kind="person", identity=canonical_identity(iss=claims.iss, sub=claims.sub),
                         role=role or "reader", iss=claims.iss, sub=claims.sub, email=claims.email,
                         name=claims.name, jti=claims.jti, key_id=None, via="oidc")

    def _workload(self, claims: TokenClaims) -> Principal:
        return Principal(kind="program", identity=canonical_identity(iss=claims.iss, sub=claims.sub),
                         role=self._assigned(claims.iss, claims.sub) or "reader", iss=claims.iss,
                         sub=claims.sub, email=claims.email, name=claims.name, jti=claims.jti,
                         key_id=None, via="oidc")

    def describe(self) -> dict[str, Any]:
        """What a service's ``/identity`` answer or doctor line prints."""
        return {
            "issuer": self.config.issuer,
            "issuer_url": self.config.issuer_url,
            "client_id": self.config.client_id,
            "identity_extra": identity_extra_installed(),
            "trusted_issuers": len(self.config.trusted_issuers),
            "admins": len(self.config.admins),
        }

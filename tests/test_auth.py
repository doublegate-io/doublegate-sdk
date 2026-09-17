"""Sign in with your provider; give a program a key (ADR-0082, AUTH-1 to AUTH-6)."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys

import pytest

from doublegate_sdk import auth
from doublegate_sdk.auth import (
    API_KEY_PREFIX, ROLES, AuthConfig, AuthError, Authenticator, IssuerKeys, KeyRow, Principal,
    TrustedIssuer, api_key_hash, canonical_identity, fold_keys, is_api_key, key_id_of, new_api_key,
    role_allows, verify_bearer_jwt,
)

HAS_IDENTITY_EXTRA = all(importlib.util.find_spec(name) for name in ("jwt", "cryptography"))
needs_extra = pytest.mark.skipif(
    not HAS_IDENTITY_EXTRA, reason="doublegate-sdk[identity] (PyJWT[crypto]) is not installed in this venv")

ISSUER = "https://issuer.example"
CLIENT = "doublegate-console"
NOW = 1_800_000_000


# ------------------------------------------------------------------ roles and identity

def test_the_four_roles_rank_reader_below_agent_below_reviewer_below_admin():
    assert ROLES == ("reader", "agent", "reviewer", "admin")
    assert role_allows("admin", "reader") and role_allows("agent", "agent")
    assert not role_allows("agent", "reviewer") and not role_allows("reader", "agent")
    with pytest.raises(ValueError):
        role_allows("owner", "reader")


def test_the_canonical_identity_has_one_form_per_credential():
    assert canonical_identity(iss=ISSUER, sub="u1") == f"{ISSUER}|u1"
    assert canonical_identity(key_id="abcdef012345") == "key:abcdef012345"
    assert canonical_identity(uid=1000) == "uid:1000"
    with pytest.raises(ValueError):
        canonical_identity(iss=ISSUER, key_id="x")
    with pytest.raises(ValueError):
        canonical_identity()


def test_a_principal_record_omits_absent_fields():
    p = Principal(kind="program", identity="key:abc", role="agent", iss=None, sub=None, email=None,
                  name=None, jti=None, key_id="abc", via="api_key")
    assert p.as_record() == {"kind": "program", "role": "agent", "key_id": "abc"}


# ----------------------------------------------------------------------- config

def test_the_auth_block_parses_and_names_an_unknown_key():
    cfg = AuthConfig.from_mapping({
        "issuer": "https://accounts.google.com", "client_id": "cid", "admins": ["me@example.org"],
        "trusted_issuers": [{"issuer": "https://token.actions.githubusercontent.com", "audience": "dg"}],
        "roles_claim": "groups", "role_map": {"ops": "admin"}})
    assert cfg.issuer_url == "https://accounts.google.com"
    assert cfg.trusted_issuers == (TrustedIssuer("https://token.actions.githubusercontent.com", "dg"),)
    assert dict(cfg.role_map) == {"ops": "admin"} and cfg.admins == ("me@example.org",)
    with pytest.raises(ValueError, match="'admin_emails'"):
        AuthConfig.from_mapping({"admin_emails": []})
    with pytest.raises(ValueError, match="role_map"):
        AuthConfig.from_mapping({"role_map": {"ops": "owner"}})
    with pytest.raises(ValueError, match="trusted_issuers"):
        AuthConfig.from_mapping({"trusted_issuers": [{"issuer": "https://x.example"}]})
    with pytest.raises(ValueError, match="issuer"):
        AuthConfig.from_mapping({"issuer": "http://issuer.example"})


def test_local_issuer_takes_its_url_from_issuer_url():
    cfg = AuthConfig.from_mapping({"issuer": "local", "issuer_url": "http://127.0.0.1:8462"})
    assert cfg.issuer == "local" and cfg.issuer_url == "http://127.0.0.1:8462"
    with pytest.raises(ValueError, match="issuer_url"):
        Authenticator(AuthConfig(), key_lookup=lambda h: None, role_lookup=lambda i, s: None, owner_uid=1)


# --------------------------------------------------------------------- API keys

def test_a_new_key_is_dgk_plus_43_base64url_characters_and_its_id_is_the_hash_prefix():
    key, digest, key_id = new_api_key()
    assert key.startswith(API_KEY_PREFIX) and len(key) == 4 + 43
    assert set(key[4:]) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    assert digest == hashlib.sha256(key.encode()).hexdigest() == api_key_hash(key)
    assert key_id == digest[:12] == key_id_of(digest)
    assert is_api_key(key) and not is_api_key("eyJ.abc.def")
    assert new_api_key()[0] != key


def test_fold_keys_builds_rows_by_hash_and_a_revoke_sticks():
    _, h1, id1 = new_api_key()
    _, h2, id2 = new_api_key()
    rows = fold_keys([
        {"event_type": "apikey-issued", "ts": 10, "payload": {
            "key_hash": h1, "role": "agent", "label": "crawler", "issued_by": f"{ISSUER}|admin"}},
        {"event_type": "apikey-issued", "key_hash": h2, "role": "reviewer", "issued_at": 20,
         "expires_at": 99, "issued_by": "key:abc"},
        {"event_type": "apikey-revoked", "ts": 30, "payload": {"key_hash": h1, "reason": "leaked"}},
        {"event_type": "apikey-revoked", "ts": 40, "payload": {"key_hash": h1}},
        {"event_type": "apikey-revoked", "ts": 40, "payload": {"key_hash": "0" * 64}},
        {"event_type": "promote", "payload": {}},
    ])
    assert set(rows) == {h1, h2}
    assert rows[h1] == KeyRow(key_id=id1, hash=h1, role="agent", label="crawler",
                              issued_by=f"{ISSUER}|admin", issued_at=10, expires_at=None, revoked_at=30)
    assert rows[h2] == KeyRow(key_id=id2, hash=h2, role="reviewer", label="", issued_by="key:abc",
                              issued_at=20, expires_at=99, revoked_at=None)
    assert rows[h1].revoked and not rows[h2].revoked and rows[h2].expired(99)


def test_fold_keys_refuses_a_row_whose_role_is_not_one_of_the_four():
    with pytest.raises(ValueError, match="owner"):
        fold_keys([{"event_type": "apikey-issued", "key_hash": "a" * 64, "role": "owner"}])


# ------------------------------------------------------ principal() without a JWT

def make_auth(rows=None, roles=None, *, owner_uid=1000, fetch=None, now=lambda: NOW, **config):
    rows, roles = rows or {}, roles or {}
    cfg = AuthConfig.from_mapping({"issuer": ISSUER, "client_id": CLIENT, **config})
    return Authenticator(cfg, key_lookup=rows.get, role_lookup=lambda i, s: roles.get((i, s)),
                         owner_uid=owner_uid, fetch=fetch, now=now)


def test_a_socket_peer_is_local_and_admin_only_when_it_is_the_owner():
    a = make_auth()
    owner = a.principal(None, socket_peer_uid=1000)
    assert (owner.kind, owner.identity, owner.role, owner.via) == ("local", "uid:1000", "admin", "socket")
    other = a.principal("Bearer whatever", socket_peer_uid=1001)
    assert (other.identity, other.role) == ("uid:1001", "agent")
    assert make_auth(owner_uid=None).principal(None, socket_peer_uid=0).role == "agent"


def test_no_header_and_no_socket_is_refused_as_missing():
    for header in (None, "", "   "):
        with pytest.raises(AuthError) as exc:
            make_auth().principal(header)
        assert exc.value.kind == "missing"
    with pytest.raises(AuthError) as exc:
        make_auth().principal("Basic abc")
    assert exc.value.kind == "malformed"


def test_an_api_key_bearer_is_a_program_with_the_rows_role():
    key, digest, key_id = new_api_key()
    row = KeyRow(key_id=key_id, hash=digest, role="reviewer", label="", issued_by="",
                 issued_at=1, expires_at=None, revoked_at=None)
    p = make_auth({digest: row}).principal(f"bearer {key}")
    assert p == Principal(kind="program", identity=f"key:{key_id}", role="reviewer", iss=None, sub=None,
                          email=None, name=None, jti=None, key_id=key_id, via="api_key")


def test_an_unknown_revoked_or_expired_key_refuses_identically():
    key, digest, key_id = new_api_key()
    live = KeyRow(key_id=key_id, hash=digest, role="agent", label="", issued_by="",
                  issued_at=1, expires_at=None, revoked_at=None)
    revoked = KeyRow(**{**live.__dict__, "revoked_at": 5})
    expired = KeyRow(**{**live.__dict__, "expires_at": NOW * 1000})
    seen = set()
    for rows in ({}, {digest: revoked}, {digest: expired}):
        with pytest.raises(AuthError) as exc:
            make_auth(rows).principal(f"Bearer {key}")
        seen.add((exc.value.kind, exc.value.message))
    assert seen == {("signature", "the API key is not accepted")}
    with pytest.raises(AuthError) as exc:
        make_auth({digest: live}).principal("Bearer dgk_short")
    assert exc.value.kind == "malformed"


def test_describe_names_the_issuer_the_client_and_the_extra():
    d = make_auth(admins=["me@example.org"], trusted_issuers=[
        {"issuer": "https://token.actions.githubusercontent.com", "audience": "dg"}]).describe()
    assert d == {"issuer": ISSUER, "issuer_url": ISSUER, "client_id": CLIENT,
                 "identity_extra": HAS_IDENTITY_EXTRA, "trusted_issuers": 1, "admins": 1}


def test_without_the_identity_extra_a_jwt_is_refused_as_unsupported_and_keys_still_work(monkeypatch):
    monkeypatch.setitem(sys.modules, "jwt", None)
    key, digest, key_id = new_api_key()
    row = KeyRow(key_id=key_id, hash=digest, role="agent", label="", issued_by="",
                 issued_at=1, expires_at=None, revoked_at=None)
    a = make_auth({digest: row})
    assert a.principal(f"Bearer {key}").kind == "program"
    with pytest.raises(AuthError) as exc:
        a.principal("Bearer eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0.sig")
    assert exc.value.kind == "unsupported" and "identity" in exc.value.message


# ---------------------------------------------------------------- JWT verification

@pytest.fixture
def issuer_fixture():
    """A fake issuer: keys, a signing helper and a counting ``fetch``."""
    if not HAS_IDENTITY_EXTRA:
        pytest.skip("doublegate-sdk[identity] (PyJWT[crypto]) is not installed in this venv")
    import jwt
    from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
    from jwt.algorithms import OKPAlgorithm, RSAAlgorithm

    class Issuer:
        def __init__(self, url=ISSUER):
            self.url = url
            self.ed = ed25519.Ed25519PrivateKey.generate()
            self.rsa = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.published = {"ed": self.ed}
            self.calls = []

        def jwks(self):
            keys = []
            for kid, private in self.published.items():
                if isinstance(private, ed25519.Ed25519PrivateKey):
                    keys.append({**OKPAlgorithm.to_jwk(private.public_key(), as_dict=True),
                                 "kid": kid})
                else:
                    keys.append({**RSAAlgorithm.to_jwk(private.public_key(), as_dict=True),
                                 "kid": kid, "alg": "RS256"})
            return {"keys": keys}

        def fetch(self, url):
            self.calls.append(url)
            if url == self.url + "/.well-known/openid-configuration":
                return json.dumps({"issuer": self.url, "jwks_uri": self.url + "/oauth/jwks"}).encode()
            if url == self.url + "/oauth/jwks":
                return json.dumps(self.jwks()).encode()
            raise AuthError("unavailable", f"no route for {url}")

        def token(self, kid="ed", *, alg=None, key=None, **claims):
            private = key or self.published.get(kid, self.ed)
            alg = alg or ("EdDSA" if isinstance(private, ed25519.Ed25519PrivateKey) else "RS256")
            payload = {"iss": self.url, "sub": "u1", "aud": CLIENT, "iat": NOW - 10,
                       "exp": NOW + 3600, "jti": "j1", "email": "u1@example.org", "name": "U One", **claims}
            payload = {k: v for k, v in payload.items() if v is not None}
            return jwt.encode(payload, private, algorithm=alg, headers={"kid": kid})

    return Issuer()


def verify(issuer, token, **kw):
    keys = IssuerKeys(issuer.url, CLIENT, fetch=issuer.fetch)
    return verify_bearer_jwt(token, issuers=(keys,), now=NOW, **kw)


def refusal(issuer, token, **kw):
    with pytest.raises(AuthError) as exc:
        verify(issuer, token, **kw)
    return exc.value.kind


@needs_extra
def test_an_eddsa_token_from_the_configured_issuer_verifies(issuer_fixture):
    claims = verify(issuer_fixture, issuer_fixture.token())
    assert (claims.iss, claims.sub, claims.aud, claims.jti) == (ISSUER, "u1", (CLIENT,), "j1")
    assert (claims.email, claims.name, claims.exp) == ("u1@example.org", "U One", NOW + 3600)
    assert issuer_fixture.calls == [ISSUER + "/.well-known/openid-configuration", ISSUER + "/oauth/jwks"]


@needs_extra
def test_an_rs256_token_verifies_and_an_aud_list_containing_the_client_is_accepted(issuer_fixture):
    issuer_fixture.published["rsa"] = issuer_fixture.rsa
    claims = verify(issuer_fixture, issuer_fixture.token("rsa", aud=["other", CLIENT]))
    assert claims.sub == "u1" and claims.aud == ("other", CLIENT)


@needs_extra
def test_none_and_hmac_algorithms_are_refused(issuer_fixture):
    import jwt
    unsigned = jwt.encode({"iss": ISSUER, "sub": "u1", "aud": CLIENT, "exp": NOW + 60}, None,
                          algorithm="none", headers={"kid": "ed"})
    assert refusal(issuer_fixture, unsigned) == "malformed"  # an empty signature segment is not a JWT here
    hmac = jwt.encode({"iss": ISSUER, "sub": "u1", "aud": CLIENT, "exp": NOW + 60}, "s" * 32,
                      algorithm="HS256", headers={"kid": "ed"})
    assert refusal(issuer_fixture, hmac) == "signature"


@needs_extra
def test_a_bad_signature_a_wrong_issuer_and_a_wrong_audience_are_refused_by_kind(issuer_fixture):
    from cryptography.hazmat.primitives.asymmetric import ed25519
    forged = issuer_fixture.token(key=ed25519.Ed25519PrivateKey.generate())
    assert refusal(issuer_fixture, forged) == "signature"
    assert refusal(issuer_fixture, issuer_fixture.token(iss="https://other.example")) == "issuer"
    assert refusal(issuer_fixture, issuer_fixture.token(aud="someone-else")) == "audience"
    assert refusal(issuer_fixture, issuer_fixture.token(aud=None)) == "audience"


@needs_extra
def test_expiry_and_not_before_honour_sixty_seconds_of_leeway(issuer_fixture):
    assert verify(issuer_fixture, issuer_fixture.token(exp=NOW - 59)).exp == NOW - 59
    assert refusal(issuer_fixture, issuer_fixture.token(exp=NOW - 60)) == "expired"
    assert verify(issuer_fixture, issuer_fixture.token(nbf=NOW + 60)).nbf == NOW + 60
    assert refusal(issuer_fixture, issuer_fixture.token(nbf=NOW + 61)) == "expired"
    assert refusal(issuer_fixture, issuer_fixture.token(exp=NOW - 1), leeway=0) == "expired"
    assert refusal(issuer_fixture, issuer_fixture.token(exp=None)) == "malformed"


@needs_extra
def test_a_token_without_a_subject_or_a_kid_is_malformed(issuer_fixture):
    import jwt
    assert refusal(issuer_fixture, issuer_fixture.token(sub=None)) == "malformed"
    no_kid = jwt.encode({"iss": ISSUER, "sub": "u1", "aud": CLIENT, "exp": NOW + 60},
                        issuer_fixture.ed, algorithm="EdDSA")
    assert refusal(issuer_fixture, no_kid) == "malformed"
    assert refusal(issuer_fixture, "not.a.jwt") == "malformed"
    assert refusal(issuer_fixture, "two.parts") == "malformed"


@needs_extra
def test_an_unknown_kid_refetches_the_key_set_once(issuer_fixture, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric import ed25519
    keys = IssuerKeys(ISSUER, CLIENT, fetch=issuer_fixture.fetch)
    assert verify_bearer_jwt(issuer_fixture.token(), issuers=(keys,), now=NOW).sub == "u1"
    rotated = ed25519.Ed25519PrivateKey.generate()
    issuer_fixture.published["ed2"] = rotated
    fresh = issuer_fixture.token("ed2", key=rotated)
    assert verify_bearer_jwt(fresh, issuers=(keys,), now=NOW).sub == "u1"
    jwks = ISSUER + "/oauth/jwks"
    assert issuer_fixture.calls.count(jwks) == 2 and issuer_fixture.calls.count(ISSUER + "/.well-known/openid-configuration") == 1
    # Within the cooldown a second unknown kid does not hit the issuer again.
    issuer_fixture.published["ed3"] = ed25519.Ed25519PrivateKey.generate()
    with pytest.raises(AuthError) as exc:
        verify_bearer_jwt(issuer_fixture.token("ed3"), issuers=(keys,), now=NOW)
    assert exc.value.kind == "signature" and issuer_fixture.calls.count(jwks) == 2
    later = auth.time.monotonic() + 10_000.0
    monkeypatch.setattr(auth.time, "monotonic", lambda: later)
    assert verify_bearer_jwt(issuer_fixture.token("ed3"), issuers=(keys,), now=NOW).sub == "u1"
    assert issuer_fixture.calls.count(jwks) == 3


@needs_extra
def test_an_unreachable_issuer_or_a_discovery_naming_another_issuer_is_refused(issuer_fixture):
    def down(url):
        raise AuthError("unavailable", "connection refused")
    with pytest.raises(AuthError) as exc:
        verify_bearer_jwt(issuer_fixture.token(), issuers=(IssuerKeys(ISSUER, CLIENT, fetch=down),), now=NOW)
    assert exc.value.kind == "unavailable"
    lying = IssuerKeys(ISSUER, CLIENT, fetch=lambda url: json.dumps(
        {"issuer": "https://evil.example", "jwks_uri": ISSUER + "/oauth/jwks"}).encode())
    with pytest.raises(AuthError) as exc:
        verify_bearer_jwt(issuer_fixture.token(), issuers=(lying,), now=NOW)
    assert exc.value.kind == "issuer"


def test_the_default_fetch_refuses_plain_http_off_loopback():
    with pytest.raises(AuthError) as exc:
        auth.default_fetch("http://issuer.example/.well-known/openid-configuration")
    assert exc.value.kind == "unavailable"


# ------------------------------------------------------ principal() with a JWT

@needs_extra
def test_a_person_gets_the_assigned_role_then_admins_then_the_mapped_claim_then_reader(issuer_fixture):
    fetch = issuer_fixture.fetch
    token = issuer_fixture.token
    assigned = make_auth(roles={(ISSUER, "u1"): "reviewer"}, fetch=fetch, admins=["u1"])
    p = assigned.principal(f"Bearer {token()}")
    assert p == Principal(kind="person", identity=f"{ISSUER}|u1", role="reviewer", iss=ISSUER, sub="u1",
                          email="u1@example.org", name="U One", jti="j1", key_id=None, via="oidc")
    assert make_auth(fetch=fetch, admins=["u1"]).principal(f"Bearer {token()}").role == "admin"
    assert make_auth(fetch=fetch, admins=["u1@example.org"]).principal(f"Bearer {token()}").role == "admin"
    mapped = make_auth(fetch=fetch, roles_claim="groups", role_map={"ops": "reviewer", "bots": "agent"})
    assert mapped.principal(f"Bearer {token(groups=['bots', 'ops', 'other'])}").role == "reviewer"
    assert mapped.principal(f"Bearer {token(groups='bots')}").role == "agent"
    assert mapped.principal(f"Bearer {token(groups=['other'])}").role == "reader"
    unmapped = make_auth(fetch=fetch, roles_claim="", role_map={"ops": "admin"})
    assert unmapped.principal(f"Bearer {token(groups=['ops'])}").role == "reader"
    assert make_auth(fetch=fetch).principal(f"Bearer {token()}").role == "reader"


@needs_extra
def test_a_trusted_workload_issuer_yields_a_program_with_its_assigned_role_or_reader(issuer_fixture):
    workload = type(issuer_fixture)("https://token.actions.githubusercontent.com")
    fetch = lambda url: issuer_fixture.fetch(url) if url.startswith(ISSUER) else workload.fetch(url)  # noqa: E731
    trusted = [{"issuer": workload.url, "audience": "dg-gates"}]
    job = workload.token(aud="dg-gates", sub="repo:org/repo:ref:refs/heads/main", email=None, name=None)
    p = make_auth(fetch=fetch, trusted_issuers=trusted, admins=["repo:org/repo:ref:refs/heads/main"]).principal(
        f"Bearer {job}")
    assert (p.kind, p.role, p.via, p.identity) == (
        "program", "reader", "oidc", f"{workload.url}|repo:org/repo:ref:refs/heads/main")
    assigned = make_auth(roles={(workload.url, "repo:org/repo:ref:refs/heads/main"): "agent"},
                         fetch=fetch, trusted_issuers=trusted)
    assert assigned.principal(f"Bearer {job}").role == "agent"
    with pytest.raises(AuthError) as exc:
        make_auth(fetch=fetch, trusted_issuers=trusted).principal(f"Bearer {workload.token(aud=CLIENT)}")
    assert exc.value.kind == "audience"
    with pytest.raises(AuthError) as exc:
        make_auth(fetch=fetch).principal(f"Bearer {job}")
    assert exc.value.kind == "issuer"


@needs_extra
def test_a_corrupt_role_table_is_a_programming_error_not_a_reader(issuer_fixture):
    a = make_auth(roles={(ISSUER, "u1"): "owner"}, fetch=issuer_fixture.fetch)
    with pytest.raises(ValueError, match="owner"):
        a.principal(f"Bearer {issuer_fixture.token()}")

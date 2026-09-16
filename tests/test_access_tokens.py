"""In-memory signed RFC 9068 fixtures; no issuer, fetch or credentials."""
from dataclasses import replace
import json

import pytest
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from doublegate_sdk import identity

NOW = 2000000000


@pytest.fixture(scope='module')
def keys():
    return [rsa.generate_private_key(public_exponent=65537, key_size=2048)
            for _ in range(2)]


def jwks(keys):
    return {'keys': [dict(json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())),
                          kid=f'key-{i}', alg='RS256', use='sig')
                     for i, key in enumerate(keys)]}


def payload(**changes):
    return dict(iss='https://issuer.example', sub='external-user', aud='organization',
                exp=NOW + 300, iat=NOW, jti='token-1', client_id='presenting-client',
                tid='tenant', **changes)


def profile(**changes):
    values = dict(issuer='https://issuer.example', tenant_id='tenant',
                  audience='organization', tenant_claim='tid', mapping_revision='map-1',
                  token_kind='direct')
    return identity.IssuerProfile(**(values | changes))


def resolver(request):
    assert request.issuer == 'https://issuer.example'
    assert request.subject == 'external-user'
    assert request.client_id == 'presenting-client'
    assert request.tenant_id == 'tenant'
    assert request.mapping_revision == 'map-1'
    return identity.ResolvedIdentity(requester_id='principal-1', requester_kind='human',
                                     actor_id='principal-1', actor_kind='human',
                                     client_principal_id='client-1')


def signed(keys, claims=None, headers=None, index=0):
    return jwt.encode(payload() if claims is None else claims, keys[index], algorithm='RS256',
                      headers={'typ': 'at+jwt', 'kid': f'key-{index}'} | (headers or {}))


def verify(keys, token=None, **changes):
    options = dict(issuer_profile=profile(), jwks=jwks(keys), resolver=resolver, now=NOW)
    return identity.verify_access_token(signed(keys) if token is None else token,
                                        **(options | changes))


def test_signed_access_token_maps_identity_without_authority(keys):
    assert hasattr(identity, 'verify_access_token'), 'missing access-token verifier'
    result = verify(keys)
    assert isinstance(result, identity.TokenClaims)
    assert result.issuer == 'https://issuer.example'
    assert result.subject == 'external-user'
    assert result.requester_id == result.actor_id == 'principal-1'
    assert result.client_principal_id == 'client-1'
    assert result.token_kind == 'direct'
    assert result.expires_at == NOW + 300
    assert not hasattr(result, 'authorized')
    assert not hasattr(result, 'groups')
    with pytest.raises(AttributeError):
        result.actor_id = 'other'


@pytest.mark.parametrize('field,value', [
    ('iss', 'https://ISSUER.example'), ('iss', 'https://issuer.example/'),
    ('aud', 'crawler'), ('aud', ['organization']), ('aud', ['organization', 'crawler']),
    ('tid', 'other'), ('tid', None), ('sub', ''), ('sub', 4), ('client_id', ''),
    ('client_id', []), ('jti', ''), ('exp', NOW - 30), ('iat', NOW + 31),
    ('nbf', NOW + 31), ('exp', NOW + 301), ('exp', NOW),
    ('exp', True), ('iat', 1.0), ('nbf', '2000000000'), ('nbf', None),
    ('act', {'sub': 'actor'}), ('act', None),
])
def test_invalid_claims_never_reach_resolver(keys, field, value):
    def forbidden(request):
        pytest.fail('invalid token reached authority resolver')
    with pytest.raises(identity.AccessTokenError):
        verify(keys, signed(keys, payload() | {field: value}), resolver=forbidden)


@pytest.mark.parametrize('field', ['iss', 'sub', 'aud', 'exp', 'iat', 'jti', 'client_id', 'tid'])
def test_required_claims(keys, field):
    claims = payload()
    del claims[field]
    with pytest.raises(identity.AccessTokenError):
        verify(keys, signed(keys, claims))


@pytest.mark.parametrize('header', [
    {'typ': 'JWT'}, {'typ': 'doublegate-attribution+jws'}, {'typ': None},
    {'jku': 'https://attacker.example'}, {'x5u': 'https://attacker.example'},
    {'crit': ['extra'], 'extra': True}, {'crit': []}, {'jwk': {}},
    {'b64': False}, {'kid': 'unknown'}, {'kid': ''},
])
def test_rejects_unapproved_jose(keys, header):
    with pytest.raises(identity.AccessTokenError):
        verify(keys, signed(keys, headers=header))


@pytest.mark.parametrize('now', [True, 1.0, None, -1])
def test_receiver_clock_type(keys, now):
    with pytest.raises(identity.AccessTokenError):
        verify(keys, now=now)


def test_time_tolerance_edges(keys):
    assert verify(keys, now=NOW - 30).issued_at == NOW
    assert verify(keys, now=NOW + 329).expires_at == NOW + 300
    assert verify(keys, signed(keys, payload() | {'nbf': NOW + 30})).not_before == NOW + 30
    with pytest.raises(identity.AccessTokenError):
        verify(keys, now=NOW + 330)
    with pytest.raises(identity.AccessTokenError):
        verify(keys, signed(keys, payload() | {'nbf': NOW + 301}))


def test_profile_is_validated_and_immutable():
    with pytest.raises(AttributeError):
        profile().audience = 'crawler'
    for changes in ({'issuer': ''}, {'audience': []}, {'token_kind': 'obo'},
                    {'tenant_claim': 'sub'}, {'mapping_revision': None}):
        with pytest.raises(identity.AccessTokenError):
            profile(**changes)


def test_app_only_cannot_invent_a_human(keys):
    with pytest.raises(identity.AccessTokenError):
        verify(keys, issuer_profile=profile(token_kind='app_only'))
    workload = identity.ResolvedIdentity('worker', 'workload', 'worker', 'workload', 'worker')
    result = verify(keys, issuer_profile=profile(token_kind='app_only'), resolver=lambda r: workload)
    assert result.requester_id == result.actor_id == result.client_principal_id == 'worker'
    assert result.requester_kind == 'workload'
    with pytest.raises(identity.AccessTokenError):
        verify(keys, issuer_profile=profile(token_kind='app_only'),
               resolver=lambda r: replace(workload, client_principal_id='different-worker'))


@pytest.mark.parametrize('mapping', [None, {},
    ('principal-1', 'human', 'other', 'human', 'client-1'),
    ('principal-1', 'unknown', 'principal-1', 'unknown', 'client-1'),
    ('principal-1', 'human', 'principal-1', 'workload', 'client-1'),
    ('', 'human', '', 'human', 'client-1'),
])
def test_unresolved_or_conflicting_mapping(keys, mapping):
    if isinstance(mapping, tuple):
        mapping = identity.ResolvedIdentity(*mapping)
    with pytest.raises(identity.AccessTokenError):
        verify(keys, resolver=lambda r: mapping)


def test_authority_unavailable_fails_closed(keys):
    def unavailable(request):
        raise RuntimeError('authority unavailable')
    with pytest.raises(identity.AccessTokenError):
        verify(keys, resolver=unavailable)


def test_raw_privilege_claims_are_not_mapping_input_or_output(keys):
    claims = payload() | {'groups': ['admins'], 'roles': ['owner'], 'scope': '*',
                          'home_team_id': 'privileged', 'principal_kind': 'human',
                          'extra': {'nullable': None, 'number': 1.5}}
    result = verify(keys, signed(keys, claims))
    assert result.requester_id == 'principal-1'
    assert not hasattr(result, 'home_team_id')


def test_supplied_rotation_and_ambiguous_kid_no_network(keys, monkeypatch):
    import socket
    def no_network(*args, **kwargs):
        pytest.fail('verifier attempted network')
    monkeypatch.setattr(socket, 'socket', no_network)
    assert verify(keys, signed(keys, index=1)).token_id == 'token-1'
    old = jwks(keys)['keys'][0]
    with pytest.raises(identity.AccessTokenError):
        verify(keys, jwks={'keys': [old, old]})
    with pytest.raises(identity.AccessTokenError):
        verify(keys, signed(keys, index=1), jwks={'keys': [old]})
    with pytest.raises(identity.AccessTokenError):
        verify(keys, jwks={'keys': jwks(keys)['keys'][1:]})


@pytest.mark.parametrize('keychange', [{'alg': 'HS256'}, {'kty': 'oct'}, {'use': 'enc'},
                                      {'key_ops': ['sign']}, {'n': '!'}, {'d': 'private'}])
def test_enrolled_key_must_be_public_rsa_signing_key(keys, keychange):
    document = jwks(keys)
    document['keys'][0].update(keychange)
    with pytest.raises(identity.AccessTokenError):
        verify(keys, jwks=document)


def raw_token(keys, header, claims):
    from jwt import api_jws
    # PyJWT signs the exact payload bytes; duplicate header below is signed via
    # its standard algorithm API to exercise decoder ambiguity, not hand crypto.
    return api_jws.encode(claims, keys[0], algorithm='RS256', headers=header)


def test_duplicate_json_and_nonobject_payload(keys):
    header = {'typ': 'at+jwt', 'kid': 'key-0'}
    raw = json.dumps(payload()).encode()
    for value in (raw[:-1] + b',"tid":"tenant"}', b'[]',
                  raw[:-1] + b',"extra":{"x":1,"x":2}}'):
        with pytest.raises(identity.AccessTokenError):
            verify(keys, raw_token(keys, header, value))


def test_duplicate_protected_header(keys):
    from jwt.utils import base64url_encode
    from jwt.algorithms import RSAAlgorithm
    header = b'{"alg":"RS256","typ":"at+jwt","kid":"key-0","kid":"key-0"}'
    data = base64url_encode(header) + b'.' + base64url_encode(json.dumps(payload()).encode())
    sig = RSAAlgorithm(RSAAlgorithm.SHA256).sign(data, keys[0])
    with pytest.raises(identity.AccessTokenError):
        verify(keys, (data + b'.' + base64url_encode(sig)).decode())


def test_wrong_signature_none_and_hs_confusion(keys):
    from cryptography.hazmat.primitives import serialization
    der = keys[0].public_key().public_bytes(serialization.Encoding.DER,
                                           serialization.PublicFormat.SubjectPublicKeyInfo)
    tokens = [jwt.encode(payload(), keys[1], algorithm='RS256',
                         headers={'kid': 'key-0', 'typ': 'at+jwt'}),
              jwt.encode(payload(), None, algorithm='none', headers={'kid': 'key-0', 'typ': 'at+jwt'}),
              jwt.encode(payload(), der, algorithm='HS256', headers={'kid': 'key-0', 'typ': 'at+jwt'})]
    for token in tokens:
        with pytest.raises(identity.AccessTokenError):
            verify(keys, token)


@pytest.mark.parametrize('token', ['', 'a.b', 'a.b.c.d', '!.e.e', 'e.e.e', None, b'abc'])
def test_malformed_compact(keys, token):
    with pytest.raises(identity.AccessTokenError):
        identity.verify_access_token(token, issuer_profile=profile(), jwks=jwks(keys),
                                     resolver=resolver, now=NOW)


def test_identity_import_is_lazy_without_extra():
    import subprocess
    import sys
    script = '''
import builtins
original = builtins.__import__
def blocked(name, *args, **kwargs):
    if name.split('.')[0] in ('jwt', 'cryptography'):
        raise ImportError('identity extra absent')
    return original(name, *args, **kwargs)
builtins.__import__ = blocked
from doublegate_sdk.identity import IssuerProfile, verify_access_token, AccessTokenError
p = IssuerProfile('issuer', 'tenant', 'destination', 'tid', 'revision', 'direct')
try:
    verify_access_token('a.b.c', issuer_profile=p, jwks={'keys': []},
                        resolver=lambda r: None, now=1)
except AccessTokenError as error:
    assert 'doublegate-sdk[identity]' in str(error)
else:
    raise AssertionError('verification without extra must fail')
'''
    run = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True)
    assert run.returncode == 0, run.stderr


@pytest.mark.parametrize('document', [None, {}, {'keys': None}, {'keys': []},
                                     {'keys': [None]}, {'keys': [{}]}])
def test_malformed_or_empty_jwks_fails_closed(keys, document):
    with pytest.raises(identity.AccessTokenError):
        verify(keys, jwks=document)


# ---- ADR-0080 d1/d4/d5: a local issuer is a key, and its profile is EdDSA ----

def _ed_keys():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return [ed25519.Ed25519PrivateKey.generate() for _ in range(2)]


def _ed_jwks(keys):
    return {'keys': [dict(json.loads(jwt.algorithms.OKPAlgorithm.to_jwk(key.public_key())),
                          kid=f'op-{i}', alg='EdDSA', use='sig')
                     for i, key in enumerate(keys)]}


def _ed_signed(keys, claims=None, index=0, alg='EdDSA'):
    return jwt.encode((payload() | {'iss': 'operator:abc', 'aud': 'client-gate-1'}) if claims is None else claims,
                      keys[index], algorithm=alg, headers={'typ': 'at+jwt', 'kid': f'op-{index}'})


def _local_resolver(request):
    assert request.issuer == 'operator:abc'
    return identity.ResolvedIdentity(requester_id='operator:abc', requester_kind='human',
                                     actor_id='operator:abc', actor_kind='human',
                                     client_principal_id='browser-1')


def test_a_local_operator_key_mints_a_per_gate_assertion_the_gate_verifies():
    keys = _ed_keys()
    prof = profile(issuer='operator:abc', audience='client-gate-1', algorithm='EdDSA')
    result = identity.verify_access_token(_ed_signed(keys), issuer_profile=prof, jwks=_ed_jwks(keys),
                                          resolver=_local_resolver, now=NOW)
    assert result.issuer == 'operator:abc' and result.audience == 'client-gate-1'
    assert result.requester_kind == 'human' and result.actor_id == 'operator:abc'


def test_the_algorithm_is_the_profiles_never_the_tokens(keys):
    ed = _ed_keys()
    prof = profile(issuer='operator:abc', audience='client-gate-1', algorithm='EdDSA')
    with pytest.raises(identity.AccessTokenError):
        # an RS256 token, an RS256 key record, under an EdDSA profile: refused at the header
        identity.verify_access_token(signed(keys), issuer_profile=prof, jwks=jwks(keys),
                                     resolver=_local_resolver, now=NOW)
    with pytest.raises(identity.AccessTokenError):
        # an EdDSA token under the external RS256 profile: refused the same way
        identity.verify_access_token(_ed_signed(ed), issuer_profile=profile(), jwks=_ed_jwks(ed),
                                     resolver=resolver, now=NOW)
    with pytest.raises(identity.AccessTokenError):
        # a token for another door is not this door's (ADR-0080 d1)
        identity.verify_access_token(_ed_signed(ed, claims=payload() | {'iss': 'operator:abc', 'aud': 'org-gate-1'}),
                                     issuer_profile=prof, jwks=_ed_jwks(ed), resolver=_local_resolver, now=NOW)
    with pytest.raises(identity.AccessTokenError):
        identity.IssuerProfile(issuer='x', tenant_id='t', audience='a', tenant_claim='tid',
                               mapping_revision='m', token_kind='direct', algorithm='HS256')

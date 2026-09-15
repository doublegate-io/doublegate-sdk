"""Real ephemeral Ed25519 signatures; no credentials or private keys on disk."""
from dataclasses import replace

import pytest

from doublegate_sdk import identity
from doublegate_sdk.envelope import canonical_json
from test_identity_wire import HEADER, PAYLOAD, b64


def signed(payload=None, header=None):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private = Ed25519PrivateKey.generate()
    data = PAYLOAD if payload is None else payload
    protected = HEADER if header is None else header
    signing_input = (b64(canonical_json(protected)) + '.' + b64(canonical_json(data))).encode('ascii')
    wire = signing_input.decode('ascii') + '.' + b64(private.sign(signing_input))
    return wire, private.public_key().public_bytes_raw()


def binding(**changes):
    fields = ('tenant_id', 'operation_id', 'envelope_digest', 'evidence_digest',
              'requester_id', 'actor_id', 'contributor_id', 'home_team_id', 'audience', 'phase')
    return identity.AttributionBinding(**{
        **{name: PAYLOAD[name] for name in fields}, 'now': PAYLOAD['issued_at'], **changes})


def verify(wire, raw_key, **changes):
    key = identity.PublicKey(kid=HEADER['kid'],
                             issuer_deployment_id=PAYLOAD['issuer_deployment_id'],
                             ed25519_bytes=raw_key)
    return identity.verify_attribution(wire, signer_key=key, expected_binding=binding(**changes))


def test_real_signature_returns_payload_not_authorization():
    assert hasattr(identity, 'verify_attribution'), 'missing cryptographic verifier'
    wire, key = signed()
    result = verify(wire, key)
    assert isinstance(result, identity.AttributionV1)
    assert result.canonical_bytes() == canonical_json(PAYLOAD)
    assert not hasattr(result, 'authorized')


@pytest.mark.parametrize('field', [
    'tenant_id', 'operation_id', 'envelope_digest', 'evidence_digest', 'requester_id',
    'actor_id', 'contributor_id', 'home_team_id', 'audience', 'phase',
    'parent_statement_digest',
])
def test_rejects_wrong_expected_binding(field):
    wire, key = signed()
    value = 'c' * 64 if field.endswith('digest') or field == 'envelope_digest' else 'other'
    if field == 'phase':
        value = 'submission'
    with pytest.raises(identity.AttributionError, match=field):
        verify(wire, key, **{field: value})


@pytest.mark.parametrize('field', ['kid', 'issuer_deployment_id'])
def test_key_is_bound_to_enrolled_kid_and_deployment(field):
    wire, raw = signed()
    key = identity.PublicKey(HEADER['kid'], PAYLOAD['issuer_deployment_id'], raw)
    with pytest.raises(identity.AttributionError, match=field):
        identity.verify_attribution(wire, signer_key=replace(key, **{field: 'other'}),
                                    expected_binding=binding())


@pytest.mark.parametrize('now', [PAYLOAD['issued_at'] - 31, True, 1.0, None, -1,
                                 9007199254740992])
def test_rejects_future_observation_or_invalid_receiver_clock(now):
    wire, key = signed()
    with pytest.raises(identity.AttributionError, match='time|future'):
        verify(wire, key, now=now)


def test_missing_extra_fails_closed_with_install_hint(monkeypatch):
    import builtins
    original = builtins.__import__
    wire, key = signed()

    def without_crypto(name, *args, **kwargs):
        if name.startswith('cryptography') or name == 'jwt':
            raise ImportError('identity extra absent')
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', without_crypto)
    with pytest.raises(identity.AttributionError, match=r'doublegate-sdk\[identity\]'):
        verify(wire, key)


@pytest.mark.parametrize('mutation', ['bad-key', 'payload', 'signature', 'header-bytes'])
def test_rejects_actual_signature_tampering(mutation):
    import json
    wire, key = signed()
    parts = wire.split('.')
    if mutation == 'bad-key':
        _, key = signed()
    elif mutation == 'payload':
        parts[1] = b64(canonical_json({**PAYLOAD, 'identity_refs': {**PAYLOAD['identity_refs'], 'membership': {**PAYLOAD['identity_refs']['membership'], 'revision': 'tampered'}}}))
    elif mutation == 'signature':
        parts[2] = b64(b'\0' * 64)
    else:
        parts[0] = b64(json.dumps(HEADER).encode())
    with pytest.raises(identity.AttributionError, match='signature'):
        verify('.'.join(parts), key)


@pytest.mark.parametrize('header', [
    {**HEADER, 'alg': 'none'}, {**HEADER, 'alg': 'RS256'}, {**HEADER, 'alg': 'HS256'},
    {**HEADER, 'typ': 'at+jwt'}, {**HEADER, 'crit': []},
    {**HEADER, 'jku': 'https://attacker.invalid/jwks'},
    {**HEADER, 'x5u': 'https://attacker.invalid/cert'},
])
def test_real_signature_does_not_bypass_closed_header(header):
    wire, key = signed(header=header)
    with pytest.raises(identity.AttributionError):
        verify(wire, key)


@pytest.mark.parametrize('raw', [b'', b'x' * 31, b'x' * 33, 'not-public-key', None])
def test_rejects_invalid_public_key(raw):
    wire, _ = signed()
    with pytest.raises(identity.AttributionError, match='public key'):
        verify(wire, raw)


def test_historical_attribution_and_identical_reverification_are_not_replay_authority():
    wire, key = signed()
    for now in (PAYLOAD['issued_at'] - 30, PAYLOAD['issued_at'] + 100000):
        first = verify(wire, key, now=now)
        assert verify(wire, key, now=now) == first
        assert not hasattr(first, 'receipt')


def test_real_two_hop_chain_verified_separately_with_exact_parent():
    from doublegate_sdk.identity_wire import decode_attribution_chain, decode_attribution_jws
    root, root_key = signed()
    parent = decode_attribution_jws(root).statement_digest
    child_payload = {**PAYLOAD, 'phase': 'submission', 'parent_statement_digest': parent}
    child, child_key = signed(child_payload)
    chain = decode_attribution_chain([root, child], expected_envelope_digest=PAYLOAD['envelope_digest'],
                                    expected_evidence_digest=PAYLOAD['evidence_digest'])
    assert verify(root, root_key) == chain.statements[0].payload
    assert verify(child, child_key, phase='submission', parent_statement_digest=parent
                  ) == chain.statements[1].payload
    with pytest.raises(identity.AttributionError, match='parent_statement_digest'):
        verify(child, child_key, phase='submission', parent_statement_digest='c' * 64)


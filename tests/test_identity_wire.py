"""Untrusted structural fixtures: signature bytes are NOT valid signatures."""
import base64
import hashlib
import json
from pathlib import Path

import pytest

from doublegate_sdk.envelope import canonical_json

PAYLOAD = next(c['payload'] for c in json.loads(
    (Path(__file__).parent / 'data/identity-contract.json').read_text())['cases']
    if c['category'] == 'shape_positive' and c['payload']['phase'] == 'production')
HEADER = {'alg': 'EdDSA', 'kid': 'deployment-key', 'typ': 'doublegate-attribution+jws'}
MANIFEST = {'manifest_version': 1, 'evidence_refs': ['a' * 64, 'b' * 64]}


def test_evidence_manifest_exact_canonical_digest():
    expected = hashlib.sha256(
        ('{"evidence_refs":["' + 'a' * 64 + '","' + 'b' * 64 +
         '"],"manifest_version":1}').encode()).hexdigest()
    assert api().evidence_manifest_digest(MANIFEST) == expected


@pytest.mark.parametrize('manifest', [None, {}, [],
    {**MANIFEST, 'extra': 0}, {**MANIFEST, 'manifest_version': True},
    {**MANIFEST, 'manifest_version': 1.0}, {**MANIFEST, 'manifest_version': 2},
    {**MANIFEST, 'evidence_refs': []}, {**MANIFEST, 'evidence_refs': ['b' * 64, 'a' * 64]},
    {**MANIFEST, 'evidence_refs': ['a' * 64, 'a' * 64]},
    {**MANIFEST, 'evidence_refs': ['A' * 64]},
    {**MANIFEST, 'evidence_refs': ['a' * 64 + '\n']},
    {**MANIFEST, 'evidence_refs': [None]}, {**MANIFEST, 'evidence_refs': 'a' * 64},
])
def test_rejects_invalid_manifest(manifest):
    with pytest.raises(ValueError):
        api().evidence_manifest_digest(manifest)



def api():
    from doublegate_sdk import identity_wire
    return identity_wire


def b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode('ascii')


def compact(payload=None, header=None, signature=b'\0' * 64):
    return '.'.join((b64(canonical_json(HEADER if header is None else header)),
                     b64(canonical_json(PAYLOAD if payload is None else payload)),
                     b64(signature)))


def chain_wires():
    root = compact()
    submission = {**PAYLOAD, 'phase': 'submission',
                  'parent_statement_digest': hashlib.sha256(root.encode('ascii')).hexdigest(),
                  'actor_id': 'other-actor', 'audience': 'other-audience',
                  'issuer_deployment_id': 'other-attester', 'authority_ref': {'revision': 18, 'digest': 'e' * 64}}
    return [root, compact(submission)]


def parse_chain(wires, **expected):
    return api().decode_attribution_chain(wires, **{
        'expected_envelope_digest': PAYLOAD['envelope_digest'],
        'expected_evidence_digest': PAYLOAD['evidence_digest'], **expected})


def test_chain_preserves_exact_ordered_bytes_and_is_untrusted():
    wires = chain_wires()
    parsed = parse_chain(wires)
    assert isinstance(parsed, api().ParsedAttributionChain)
    assert tuple(s.compact for s in parsed.statements) == tuple(wires)
    expected = hashlib.sha256(json.dumps(wires, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()
    assert parsed.chain_digest == expected
    assert all(s.signature == b'\0' * 64 for s in parsed.statements)
    assert not hasattr(parsed, 'verified')
    wires.reverse()
    assert parsed.statements[0].payload.to_dict()['phase'] == 'production'


@pytest.mark.parametrize('shape', ['empty', 'one', 'extra', 'reversed', 'roots', 'submissions',
                                     'none', 'tuple', 'string'])
def test_chain_rejects_non_two_phase_profile(shape):
    wires = chain_wires()
    cases = {'empty': [], 'one': wires[:1], 'extra': wires + wires[:1],
             'reversed': wires[::-1], 'roots': [wires[0], wires[0]],
             'submissions': [wires[1], wires[1]], 'none': None,
             'tuple': tuple(wires), 'string': wires[0]}
    with pytest.raises(ValueError, match='chain|phase'):
        parse_chain(cases[shape])


@pytest.mark.parametrize('mutation', ['wrong-digest', 'header-reserialization', 'signature'])
def test_chain_parent_binds_exact_ascii_root(mutation):
    wires = chain_wires()
    if mutation == 'wrong-digest':
        claims = api().decode_attribution_jws(wires[1]).payload.to_dict()
        claims['parent_statement_digest'] = 'c' * 64
        wires[1] = compact(claims)
    else:
        segments = wires[0].split('.')
        if mutation == 'header-reserialization':
            segments[0] = b64(json.dumps(HEADER).encode())
        else:
            segments[2] = b64(b'\1' * 64)
        wires[0] = '.'.join(segments)
    # Both statements still parse; only their exact-byte parent binding is broken.
    for wire in wires:
        api().decode_attribution_jws(wire)
    with pytest.raises(ValueError, match='parent'):
        parse_chain(wires)
    claims = api().decode_attribution_jws(wires[1]).payload.to_dict()
    claims['parent_statement_digest'] = hashlib.sha256(wires[0].encode('ascii')).hexdigest()
    wires[1] = compact(claims)
    assert parse_chain(wires).statements[0].compact == wires[0]


@pytest.mark.parametrize('field', ['tenant_id', 'operation_id', 'envelope_digest', 'evidence_digest',
                                  'requester_id', 'contributor_id', 'home_team_id'])
@pytest.mark.parametrize('phase_index', [0, 1])
def test_chain_rejects_cross_phase_binding_swap(field, phase_index):
    wires = chain_wires()
    claims = [api().decode_attribution_jws(w).payload.to_dict() for w in wires]
    claims[phase_index][field] = 'c' * 64 if field in ('envelope_digest', 'evidence_digest') else 'other'
    wires[0] = compact(claims[0])
    claims[1]['parent_statement_digest'] = hashlib.sha256(wires[0].encode('ascii')).hexdigest()
    wires[1] = compact(claims[1])
    # Parent is repaired, so rejection must be for this field, not a stale digest.
    assert api().decode_attribution_jws(wires[1]).payload.to_dict()[
        'parent_statement_digest'] == api().decode_attribution_jws(wires[0]).statement_digest
    with pytest.raises(ValueError, match=field):
        parse_chain(wires)


@pytest.mark.parametrize('field', ['envelope_digest', 'evidence_digest'])
def test_chain_rejects_wrong_expected_binding(field):
    with pytest.raises(ValueError, match=field):
        parse_chain(chain_wires(), **{'expected_' + field: 'c' * 64})


@pytest.mark.parametrize('field', ['envelope_digest', 'evidence_digest'])
@pytest.mark.parametrize('value', [None, 7, 'A' * 64, 'a' * 64 + '\n', 'g' * 64])
def test_chain_rejects_malformed_expected_digest(field, value):
    with pytest.raises(ValueError, match='digest'):
        parse_chain(chain_wires(), **{'expected_' + field: value})


@pytest.mark.parametrize('field', ['envelope_digest', 'evidence_digest'])
def test_chain_requires_external_expected_binding(field):
    with pytest.raises(TypeError, match='expected_'):
        api().decode_attribution_chain(
            chain_wires(), **{'expected_' + field: PAYLOAD[field]})


def test_decode_preserves_exact_wire_without_authenticating():
    wire = compact()
    parsed = api().decode_attribution_jws(wire)
    assert parsed.compact == wire
    assert parsed.payload.to_dict() == PAYLOAD
    assert parsed.signature == b'\0' * 64
    assert parsed.signing_input == wire.rsplit('.', 1)[0].encode('ascii')
    assert parsed.statement_digest == hashlib.sha256(wire.encode('ascii')).hexdigest()
    assert parsed.header == HEADER
    parsed.header['kid'] = 'changed'
    assert parsed.header == HEADER
    assert not hasattr(parsed, 'verified')


@pytest.mark.parametrize('header', [
    {}, [], {'alg': 'none', 'kid': 'k', 'typ': HEADER['typ']},
    {**HEADER, 'alg': 'RS256'}, {**HEADER, 'alg': 'HS256'},
    {**HEADER, 'typ': 'at+jwt'}, {**HEADER, 'kid': ''},
    {**HEADER, 'kid': 1}, {**HEADER, 'crit': []},
    {**HEADER, 'jku': 'https://untrusted.invalid'}, {**HEADER, 'extra': True},
])
def test_rejects_header_outside_closed_profile(header):
    with pytest.raises(ValueError):
        api().decode_attribution_jws(compact(header=header))


@pytest.mark.parametrize('raw', [b'{"alg":"EdDSA","alg":"EdDSA","kid":"k","typ":"doublegate-attribution+jws"}',
                                 b'null', b'{', b'\xff'])
def test_rejects_malformed_header(raw):
    wire = compact().split('.')
    wire[0] = b64(raw)
    with pytest.raises(ValueError):
        api().decode_attribution_jws('.'.join(wire))


@pytest.mark.parametrize('wire', [None, b'abc', '', 'a.b', 'a.b.c.d', '..',
    compact() + '=', compact() + '\n', compact()[:-1] + 'B',
    compact(signature=b'\0' * 63), compact(signature=b'\0' * 65),
    compact().replace('.', '=.', 1), 'é.' + compact().split('.', 1)[1],
    compact(payload={**PAYLOAD, 'extra': 'unknown'}),
])
def test_rejects_malformed_compact(wire):
    with pytest.raises(ValueError):
        api().decode_attribution_jws(wire)


def test_header_json_need_not_be_canonical_but_payload_must_be():
    wire = compact().split('.')
    wire[0] = b64(json.dumps(HEADER).encode())
    parsed = api().decode_attribution_jws('.'.join(wire))
    assert parsed.compact == '.'.join(wire)
    wire[1] = b64(json.dumps(PAYLOAD).encode())
    with pytest.raises(ValueError):
        api().decode_attribution_jws('.'.join(wire))

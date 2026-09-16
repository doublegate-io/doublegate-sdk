"""Event transport with real test signatures; parsing remains untrusted."""
import base64
import hashlib

import pytest

from doublegate_sdk import submission
from doublegate_sdk.identity_wire import evidence_manifest_digest
from test_identity_wire import compact

from submission_fixtures import MANIFEST, fixture


def encode(wire, blob=b'\x00\xff\x80'):
    return submission.encode_submission(blob, wire['document'], max_content_bytes=256,
        **{k: v for k, v in wire.items() if k not in ('document', 'artifact_id', 'content', 'encoding')})


def test_v2_roundtrip_exposes_structurally_bound_but_untrusted_identity():
    wire = fixture()
    assert encode(wire) == wire
    parsed = submission._parse_submission(wire, max_content_bytes=256)
    assert parsed.content == b'\x00\xff\x80'
    assert parsed.event.artifact_id == PAYLOAD_ID(wire)
    assert parsed.space == wire['space']
    assert parsed.production_evidence_digest == evidence_manifest_digest(MANIFEST)
    assert tuple(s.compact for s in parsed.untrusted_identity.statements) == tuple(wire['document']['attribution_chain'])
    assert all(s.signature != b'\0' * 64 for s in parsed.untrusted_identity.statements)
    assert not hasattr(parsed, 'verified')
    assert submission.decode_submission(wire, PAYLOAD_ID(wire), max_content_bytes=256) == parsed.content


@pytest.mark.parametrize('version', [None, True, False, 1, 1.0, 2.0, 0, 3, '2', ''])
def test_no_malformed_version_or_downgrade(version):
    wire = fixture()
    wire['submission_version'] = version
    with pytest.raises(ValueError, match='fields'):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(TypeError, match='submission_version'):
        encode(wire)


@pytest.mark.parametrize('field', ['document', 'artifact_id', 'signature', 'content', 'space', 'local_verdicts',
    'local_promotion', 'production_evidence_manifest'])
def test_every_v2_required_field_is_required(field):
    wire = fixture()
    del wire[field]
    with pytest.raises(ValueError):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('field', ['scope', 'tenant_id', 'home_team_id', 'extra', 'access_token'])
def test_v2_outer_body_is_closed(field):
    wire = fixture()
    wire[field] = 'not authority'
    with pytest.raises(ValueError, match='fields'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('space', [None, '', 1, True, [], {}, '\ud800'])
def test_space_must_be_nonempty_valid_unicode_string(space):
    wire = fixture()
    wire['space'] = space
    with pytest.raises(ValueError, match='space'):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError, match='space'):
        encode(wire)


@pytest.mark.parametrize('field,value', [('local_verdicts', None), ('local_verdicts', {}),
    ('local_verdicts', [None]), ('local_verdicts', ['event']),
    ('local_promotion', None), ('local_promotion', []), ('local_promotion', 'event')])
def test_local_records_have_wire_container_types_not_invented_authority(field, value):
    wire = fixture()
    wire[field] = value
    with pytest.raises(ValueError, match=field):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError, match=field):
        encode(wire)


def test_submission_requires_version_even_if_all_identity_fields_removed():
    wire = fixture()
    wire.pop('document')
    wire.pop('production_evidence_manifest')
    # Stripping every identity field must not select a compatibility path.
    with pytest.raises(ValueError, match='fields'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('blob', [b'', b'f', b'\0\xff\x80', bytes(range(256)),
                                b'PK\x03\x04\0\xff', 'é😀'.encode()])
def test_v2_arbitrary_bytes_and_canonical_base64(blob):
    wire = fixture(blob)
    assert encode(wire, blob) == wire
    assert wire['content'] == base64.b64encode(blob).decode('ascii')
    assert submission.decode_submission(wire, max_content_bytes=len(blob)) == blob


@pytest.mark.parametrize('raw', ['Zg', 'Zg=', 'Zg===', 'Zg==\n', ' Zg==', 'Zh==',
                                'Zm9=', '_w==', 'é', '!!!!'])
def test_v2_reuses_strict_base64_rejection(raw):
    wire = fixture(b'f')
    wire['content'] = raw
    with pytest.raises(ValueError, match='base64'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('encoding', [None, 'utf-8', 'BASE64', '', 1])
def test_v2_encoding_is_base64_or_omitted(encoding):
    wire = fixture()
    wire['encoding'] = encoding
    with pytest.raises(ValueError, match='encoding'):
        submission.decode_submission(wire, max_content_bytes=256)


def test_v2_omitted_encoding_retains_utf8_and_exact_space():
    blob = 'é😀 Zg=='
    wire = fixture(blob.encode())
    wire.pop('encoding')
    wire['content'] = blob
    wire['space'] = ' Équipe/CaseSensitive '
    parsed = submission._parse_submission(wire, max_content_bytes=256)
    assert parsed.content == blob.encode()
    assert parsed.space == wire['space']  # no trimming, folding or authorization
    wire['content'] = '\ud800'
    with pytest.raises(ValueError, match='UTF-8'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('shape', [None, [], 'string', (), [None, None]])
def test_v2_requires_actual_two_jws_chain(shape):
    wire = fixture()
    wire['document']['attribution_chain'] = shape
    with pytest.raises(ValueError):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError):
        encode(wire)


@pytest.mark.parametrize('change', ['reverse', 'extra', 'omit-child', 'root-bytes'])
def test_v2_rejects_wrong_chain_order_length_or_exact_parent(change):
    wire = fixture()
    chain = wire['document']['attribution_chain']
    if change == 'reverse':
        chain.reverse()
    elif change == 'extra':
        chain.append(chain[1])
    elif change == 'omit-child':
        chain.pop()
    else:
        segments = chain[0].split('.')
        segments[2] = base64.urlsafe_b64encode(b'\1' * 64).rstrip(b'=').decode()
        chain[0] = '.'.join(segments)
    with pytest.raises(ValueError, match='chain|phase|parent'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('field', ['envelope_digest', 'home_team_id', 'evidence_digest',
                                  'requester_id', 'tenant_id', 'contributor_id', 'operation_id'])
def test_v2_rejects_cross_phase_swaps(field):
    from doublegate_sdk.identity_wire import decode_attribution_jws
    wire = fixture()
    child = decode_attribution_jws(wire['document']['attribution_chain'][1]).payload.to_dict()
    child[field] = 'c' * 64 if field in ('envelope_digest', 'evidence_digest') else 'other'
    wire['document']['attribution_chain'][1] = compact(child)
    with pytest.raises(ValueError, match=field):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError, match=field):
        encode(wire)


@pytest.mark.parametrize('field', ['writer_identity', 'manifest', 'external-artifact', 'content-hash'])
def test_v2_binds_chain_to_actual_body_not_just_itself(field):
    from doublegate_sdk.identity_wire import decode_attribution_jws
    wire = fixture()
    expected = PAYLOAD_ID(wire)
    if field == 'writer_identity':
        wire['document']['envelope']['writer_identity'] = 'different'
    elif field == 'manifest':
        wire['production_evidence_manifest'] = {'manifest_version': 1, 'evidence_refs': ['b' * 64]}
    elif field == 'external-artifact':
        expected = 'c' * 64
    else:
        claims = [decode_attribution_jws(w).payload.to_dict() for w in wire['document']['attribution_chain']]
        for c in claims:
            c['envelope_digest'] = wire['document']['envelope']['content_hash']
        wire['document']['attribution_chain'][0] = compact(claims[0])
        claims[1]['parent_statement_digest'] = hashlib.sha256(wire['document']['attribution_chain'][0].encode()).hexdigest()
        wire['document']['attribution_chain'][1] = compact(claims[1])
    with pytest.raises(ValueError, match='envelope_digest|manifest digest|different artifact'):
        submission.decode_submission(wire, expected if field == 'external-artifact' else None,
                                        max_content_bytes=256)


@pytest.mark.parametrize('manifest', [None, {}, {'manifest_version': 2, 'evidence_refs': ['a' * 64]},
    {**MANIFEST, 'scope': 'team'}, {'manifest_version': 1, 'evidence_refs': []}])
def test_v2_reuses_closed_production_manifest_validator(manifest):
    wire = fixture()
    wire['production_evidence_manifest'] = manifest
    with pytest.raises(ValueError):
        submission.decode_submission(wire, max_content_bytes=256)
    with pytest.raises(ValueError):
        encode(wire)


def test_explicit_v1_rejected_without_identity():
    wire = fixture()
    wire['document'].pop('attribution_chain')
    wire.pop('production_evidence_manifest')
    wire['submission_version'] = 1
    wire['scope'] = 'legacy-opaque-not-authority'
    with pytest.raises(ValueError, match='fields'):
        submission.decode_submission(wire, max_content_bytes=256)


@pytest.mark.parametrize('limit', [-1, True, 1.0, None])
def test_v2_reuses_limit_policy_validation(limit):
    with pytest.raises(ValueError, match='max_content_bytes'):
        submission.decode_submission(fixture(), max_content_bytes=limit)


def test_v2_keeps_size_limit_and_content_binding_checks():
    wire = fixture()
    with pytest.raises(submission.SubmissionTooLarge):
        submission.decode_submission(wire, max_content_bytes=2)
    wire['content'] = base64.b64encode(b'xyz').decode()
    with pytest.raises(ValueError, match='content_hash'):
        submission.decode_submission(wire, max_content_bytes=256)
    wire = fixture()
    wire['document']['envelope']['size_bytes'] = 2
    with pytest.raises(ValueError, match='size_bytes'):
        submission.decode_submission(wire, max_content_bytes=256)


def test_only_generic_submission_api_is_public():
    assert not hasattr(submission, 'decode_submission_v2')
    assert not hasattr(submission, 'encode_submission_v2')
    assert not hasattr(submission, 'ParsedSubmissionV2')


def test_encoder_rejects_omitted_version_and_sidecars():
    wire = fixture()
    with pytest.raises(TypeError, match='signature'):
        submission.encode_submission(b'\x00\xff\x80', wire['document'], max_content_bytes=256)


def PAYLOAD_ID(wire):
    from doublegate_sdk.envelope import Envelope
    return wire['artifact_id']

"""Event transport with real test signatures; parsing remains untrusted."""
import base64

import pytest

from doublegate_sdk import submission
from doublegate_sdk.identity_wire import evidence_manifest_digest

from submission_fixtures import MANIFEST, fixture

OBO = {'identity': 'https://issuer.test|subject-7', 'kind': 'person', 'role': 'reviewer',
       'iss': 'https://issuer.test', 'sub': 'subject-7', 'jti': 'token-id'}


def encode(wire, blob=b'\x00\xff\x80'):
    return submission.encode_submission(blob, wire['document'], max_content_bytes=256,
        **{k: v for k, v in wire.items() if k not in ('document', 'artifact_id', 'content', 'encoding')})


def test_v2_roundtrip_exposes_the_event_and_its_evidence_digest():
    wire = fixture()
    assert encode(wire) == wire
    parsed = submission._parse_submission(wire, max_content_bytes=256)
    assert parsed.content == b'\x00\xff\x80'
    assert parsed.event.artifact_id == wire['artifact_id']
    assert parsed.space == wire['space']
    assert parsed.production_evidence_digest == evidence_manifest_digest(MANIFEST)
    assert parsed.on_behalf_of is None
    assert not hasattr(parsed, 'verified')
    assert submission.decode_submission(wire, wire['artifact_id'], max_content_bytes=256) == parsed.content


def test_the_event_names_no_attribution_chain():
    """ADR-0082 d1, d7: no statement is signed, so the document closes without one."""
    assert 'attribution_chain' not in submission.EVENT_FIELDS
    wire = fixture()
    wire['document']['attribution_chain'] = ['a.b.c', 'd.e.f']
    with pytest.raises(ValueError, match='fields'):
        submission.decode_submission(wire, max_content_bytes=256)


def test_on_behalf_of_is_a_first_class_optional_member_of_the_body():
    """AUTH-5: the upstream principal rides beside the event and both sides parse it."""
    wire = fixture()
    wire['on_behalf_of'] = dict(OBO)
    body = submission.encode_submission(b'\x00\xff\x80', wire['document'], max_content_bytes=256,
        signature=wire['signature'], space=wire['space'], local_verdicts=wire['local_verdicts'],
        local_promotion=wire['local_promotion'],
        production_evidence_manifest=wire['production_evidence_manifest'], on_behalf_of=OBO)
    assert body['on_behalf_of'] == OBO
    assert body['artifact_id'] == wire['artifact_id']   # trace never enters the signed bytes
    parsed = submission._parse_submission(body, max_content_bytes=256)
    assert parsed.on_behalf_of == OBO
    assert submission.decode_submission(body, max_content_bytes=256) == b'\x00\xff\x80'


def test_on_behalf_of_is_trace_and_names_only_a_principal():
    wire = fixture()
    for bad in (None, [], 'operator', {}, {'identity': 'who', 'scope': 'admin'},
                {'identity': ''}, {'identity': 7}, {'identity': 'who', 'role': ['admin']},
                {'kind': 'person'}):
        wire['on_behalf_of'] = bad
        with pytest.raises(ValueError):
            submission.decode_submission(wire, max_content_bytes=256)
    wire['on_behalf_of'] = {'identity': 'operator:local'}
    assert submission._parse_submission(wire, max_content_bytes=256).on_behalf_of == {'identity': 'operator:local'}


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


@pytest.mark.parametrize('field', ['writer_identity', 'manifest', 'external-artifact'])
def test_v2_binds_the_event_to_the_actual_body(field):
    wire = fixture()
    expected = wire['artifact_id']
    if field == 'writer_identity':
        wire['document']['envelope']['writer_identity'] = 'different'
    elif field == 'manifest':
        wire['production_evidence_manifest'] = {'manifest_version': 1, 'evidence_refs': ['b' * 64]}
    else:
        expected = 'c' * 64
    with pytest.raises(ValueError, match='manifest digest|different artifact|content_digest'):
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

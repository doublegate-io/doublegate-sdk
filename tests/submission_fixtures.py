"""Real public-test signatures, never enrolled keys or gate authority.

Production evidence references and local records remain inert test assertions.
"""
import base64
import hashlib

from doublegate_sdk.envelope import Envelope, make_envelope
from doublegate_sdk.identity_wire import evidence_manifest_digest
from doublegate_sdk.submission import SubmissionEvent, sign_submission

MANIFEST = {'manifest_version': 1, 'evidence_refs': ['a' * 64]}
TEST_SEED = hashlib.sha256(b'PUBLIC TEST SEED transport').digest()
TENANT = 'tenant-inert'
TEAM = 'home-team-inert'
ACTOR = 'actor-inert'


def document(envelope):
    env = Envelope.from_mapping(envelope)
    return {'contract': 'doublegate.submission/1', 'envelope': env.to_dict(),
        'content_digest': 'sha256:' + env.content_hash,
        'evidence_manifest_digest': 'sha256:' + evidence_manifest_digest(MANIFEST),
        'tenant_id': TENANT, 'team_id': TEAM,
        'principal': {'kind': 'workload', 'id': ACTOR, 'sponsor': None},
        'membership': {'issuer': 'https://issuer.test', 'revision': 1, 'assertion_digest': 'a' * 64},
        'submitted_at': '2026-09-17T00:00:00.000Z',
        'review_refs': []}


def sidecars(envelope):
    event = SubmissionEvent.from_mapping(document(envelope))
    return {'signature': sign_submission(event, private_key=TEST_SEED), 'space': 'team/exact',
            'local_verdicts': [], 'local_promotion': {'event': {}, 'sig': 'unverified'},
            'production_evidence_manifest': {**MANIFEST, 'evidence_refs': list(MANIFEST['evidence_refs'])}}


def fixture(blob=b'\x00\xff\x80'):
    env = make_envelope(blob=blob, content_type='imported_document', trust_class='T-5',
                        source_uri='test://inert', writer_identity='test',
                        deployment_id='test', ingest_ts=1)
    doc = document(env.to_dict())
    return {'document': doc, 'artifact_id': SubmissionEvent.from_mapping(doc).artifact_id,
            'content': base64.b64encode(blob).decode('ascii'),
            'encoding': 'base64', **sidecars(env.to_dict())}

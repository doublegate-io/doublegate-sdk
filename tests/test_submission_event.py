"""Approved external reconciliation vector; real crypto, never gate authority."""
import base64
import hashlib
import json
from pathlib import Path

import pytest
from doublegate_sdk import submission

PACKET = Path(__file__).parent / 'data/reconciled-event'


def test_event_transport_preserves_original_event_and_exposes_gate_bindings():
    wire = json.loads((PACKET/'wire.json').read_text())
    evidence = json.loads((PACKET/'production-evidence.json').read_text())
    content = bytes.fromhex(evidence['content_hex'])
    body = {**wire, 'content': base64.b64encode(content).decode(), 'encoding': 'base64',
            'space': 'space-A', 'local_verdicts': [], 'local_promotion': {},
            'production_evidence_manifest': evidence['manifest']}
    assert submission.decode_submission(body, wire['artifact_id'], max_content_bytes=1024) == content
    parsed = submission._parse_submission(body, max_content_bytes=1024)
    assert parsed.event.artifact_id == wire['artifact_id']
    assert parsed.event.content_digest == wire['document']['content_digest']
    assert parsed.untrusted_identity.statements[1].payload.to_dict()['operation_id'] == 'operation-1'
    encoded = submission.encode_submission(content, wire['document'], signature=wire['signature'],
        space='space-A', local_verdicts=[], local_promotion={},
        production_evidence_manifest=evidence['manifest'], max_content_bytes=1024)
    assert encoded == body
    with pytest.raises(ValueError):
        submission.decode_submission({**body, 'submission_version': 2}, max_content_bytes=1024)


def test_node_consumes_actual_sdk_bytes_and_signature(tmp_path):
    import shutil
    import subprocess

    provenance = json.loads((PACKET / 'provenance.json').read_text())
    for name, digest in provenance['sha256'].items():
        assert hashlib.sha256((PACKET / name).read_bytes()).hexdigest() == digest
    generated = tmp_path / 'generated'
    generated.mkdir()
    wire = json.loads((PACKET / 'wire.json').read_text())
    event = submission.SubmissionEvent.from_mapping(wire['document'])
    signature = submission.sign_submission(event,
        private_key=hashlib.sha256(b'PUBLIC TEST SEED submission').digest())
    evidence = json.loads((PACKET / 'production-evidence.json').read_text())
    body = submission.encode_submission(bytes.fromhex(evidence['content_hex']), event.to_dict(),
        signature=signature, space='space-A', local_verdicts=[], local_promotion={},
        production_evidence_manifest=evidence['manifest'], max_content_bytes=1024)
    # These files are SDK OUTPUT, not copies of the original packet's wire/bytes.
    (generated / 'wire.json').write_text(json.dumps(body))
    (generated / 'event.canonical.bin').write_bytes(event.canonical_bytes())
    for name in ('manifest.json', 'production-evidence.json'):
        shutil.copyfile(PACKET / name, generated / name)
    shutil.copyfile(PACKET / 'consumer.mjs', tmp_path / 'consumer.mjs')
    result = subprocess.run(['node', str(tmp_path / 'consumer.mjs')], capture_output=True, text=True)
    (tmp_path / 'node-proof.log').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, result.stderr
    assert 'PASS: independent Node' in result.stdout
    # Prove the independent consumer rejects tampered SDK output, then restore.
    original = (generated / 'event.canonical.bin').read_bytes()
    (generated / 'event.canonical.bin').write_bytes(original + b' ')
    negative = subprocess.run(['node', str(tmp_path / 'consumer.mjs')], capture_output=True, text=True)
    assert negative.returncode != 0
    (generated / 'event.canonical.bin').write_bytes(original)
    restored = subprocess.run(['node', str(tmp_path / 'consumer.mjs')], capture_output=True, text=True)
    assert restored.returncode == 0, restored.stderr


def test_existing_packet_whole_event_vector():
    wire = json.loads((PACKET/'wire.json').read_text())
    assert hasattr(submission, 'SubmissionEvent'), 'SDK has no whole-document submission event'
    event = submission.SubmissionEvent.from_mapping(wire['document'])
    assert event.canonical_bytes() == (PACKET/'event.canonical.bin').read_bytes()
    assert event.artifact_id == wire['artifact_id']
    assert event.artifact_id == 'sha256:b8105eff763163ba7ccc94cb2533e22e5d6c7da294877abf1ce49f0c8ca365df'
    key = hashlib.sha256(b'PUBLIC TEST SEED submission').digest()
    assert submission.sign_submission(event, private_key=key) == wire['signature']
    keys = json.loads((PACKET/'manifest.json').read_text())['keys']
    submission.verify_submission_signature(event, wire['signature'],
        public_key=bytes.fromhex(keys['submission']), expected_artifact_id=wire['artifact_id'])
    damaged = bytearray(base64.urlsafe_b64decode(wire['signature']+'==')); damaged[0] ^= 1
    with pytest.raises(ValueError, match='signature'):
        submission.verify_submission_signature(event, base64.urlsafe_b64encode(damaged).decode().rstrip('='),
            public_key=bytes.fromhex(keys['submission']), expected_artifact_id=wire['artifact_id'])

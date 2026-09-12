"""Generate a real signed-event publisher fixture; no network or model calls."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import tempfile

from doublegate import apidoc, keys, ledger, publish

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--producer-source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
# Refuse a different installed renderer rather than mislabel its provenance.
assert hashlib.sha256(Path(publish.__file__).read_bytes()).hexdigest() == (
    'd2682878c1fb6e3158575bcf3d582f528ce26a8b3c8fa6cad416b2593f0cae23'
)
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=True)
root = out.parent
with tempfile.TemporaryDirectory(dir=root) as directory:
    home = Path(directory)
    # Ephemeral test key stays in memory; no configured credentials are read.
    dk = keys.generate()
    journal = ledger.Ledger(home, dk)
    sink = publish.JsonlSink(home / 'notes.jsonl')
    for index, (event, payload) in enumerate([
        ('promote', {'k': 2, 'provisional': True}),
        ('relation', {'outcome': 'restates', 'kind': 'restates', 'child': 'b' * 64}),
        ('scan', {'findings': [{'kind': 'test', 'severity': 'low'}]}),
    ], start=1):
        signed = journal.append(event, 'a' * 64, 'uid:1', index, payload)
        note = publish.note_from_event(signed, dk.deployment_id, lambda aid: {
            'trust_class': 'T-1', 'content_type': 'memory', 'space': 'main', 'provisional': 1,
        })
        assert sink.deliver(note) is True
    sink.close()
    serialized = (home / 'notes.jsonl').read_bytes()
    (out / 'notes.jsonl').write_bytes(serialized)
schema = apidoc.note_schema()
(out / 'note.schema.json').write_text(json.dumps(schema, indent=2) + '\n')
committed = args.producer_source / 'docs/api/note.schema.json'
assert schema == json.loads(committed.read_text())
metadata = {
    'producer_commit': '7e9b1e25f72ea41b9c6917dba9e0a51c29a404e0',
    'python': sys.executable,
    'publisher_import': publish.__file__,
    'publisher_sha256': hashlib.sha256(Path(publish.__file__).read_bytes()).hexdigest(),
    'producer_install': json.loads(importlib.metadata.distribution('doublegate').read_text('direct_url.json') or '{}'),
    'schema_matches_prepared_producer_document': True,
    'notes_sha256': hashlib.sha256(serialized).hexdigest(),
    'notes_count': len(serialized.splitlines()),
    'path': 'Ledger.append -> note_from_event -> JsonlSink.deliver -> UTF-8 JSONL',
}
(out / 'provenance.json').write_text(json.dumps(metadata, indent=2) + '\n')
print(json.dumps(metadata, indent=2))
